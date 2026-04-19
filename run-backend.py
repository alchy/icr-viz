"""Backend launcher — spawn uvicorn as a child process with redirected I/O.

Why this exists:
  - Running uvicorn directly makes it inconvenient to tail its output after
    the launcher exits (e.g. when started from a parent task runner).
  - On Windows, killing uvicorn cleanly with Ctrl+C requires CTRL_BREAK_EVENT
    instead of SIGINT; this script hides that complexity.

What it does:
  - Spawns ``uvicorn piano_web.main:app`` with ``--app-dir apps/api``.
  - Streams the child's stdout + stderr to BOTH the parent console AND a log
    file at ``logs/backend-YYYYMMDD-HHMMSS.log``, line-buffered so `tail -f`
    shows every log line immediately.
  - Handles SIGINT / SIGTERM (and Windows CTRL_C_EVENT / CTRL_BREAK_EVENT) to
    forward the signal to the child and wait for graceful shutdown.

Usage:
    python run-backend.py                       # default host/port from env
    python run-backend.py --port 9001
    python run-backend.py --reload               # dev mode
    python run-backend.py --log-file logs/custom.log
    ICR_VIZ_LOG_LEVEL=DEBUG python run-backend.py

Environment variables honoured:
    ICR_VIZ_DB           path to SQLite file
    ICR_VIZ_LOG_LEVEL    forwarded to uvicorn/piano_web
    ICR_VIZ_LOG_JSON     forwarded to piano_web
    ICR_VIZ_CORS_ORIGINS forwarded to piano_web
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent
DEFAULT_DB_PATH = REPO_ROOT / "data" / "dev.sqlite"


def _default_log_path() -> Path:
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    return REPO_ROOT / "logs" / f"backend-{ts}.log"


def _check_port_free(host: str, port: int) -> tuple[bool, str | None]:
    """Return (True, None) if the port can be bound, else (False, hint)."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.settimeout(0.5)
        try:
            sock.bind((host, port))
            return True, None
        except OSError as exc:
            hint = _describe_port_holder(host, port)
            return False, f"port {port} on {host} is busy ({exc.strerror or exc}); {hint}"


def _find_listening_pid(port: int) -> int | None:
    """Return PID of the process LISTENING on `port`, or None if not found."""
    if os.name == "nt":
        try:
            out = subprocess.run(
                ["netstat", "-ano", "-p", "TCP"],
                capture_output=True, text=True, timeout=2.0,
            ).stdout
            for line in out.splitlines():
                parts = line.split()
                if len(parts) >= 5 and parts[1].endswith(f":{port}") and parts[3] == "LISTENING":
                    try:
                        return int(parts[4])
                    except ValueError:
                        return None
        except Exception:
            return None
    else:
        try:
            out = subprocess.run(
                ["lsof", "-tiTCP:" + str(port), "-sTCP:LISTEN"],
                capture_output=True, text=True, timeout=2.0,
            ).stdout.strip()
            return int(out.splitlines()[0]) if out else None
        except Exception:
            return None
    return None


def _stop_icr_via_api(host: str, port: int, timeout_s: float = 2.5) -> str | None:
    """Ask the running backend to stop its child ICR engine.

    Returns a short status string for logging, or None on failure.
    Called before we hard-kill the backend so the child ICR gets a graceful
    CTRL_BREAK instead of being orphaned.
    """
    url = f"http://{host}:{port}/api/icr/stop"
    req = urllib.request.Request(url, method="POST",
                                 headers={"Content-Type": "application/json"},
                                 data=b"")
    try:
        with urllib.request.urlopen(req, timeout=timeout_s) as resp:
            body = json.loads(resp.read().decode("utf-8"))
            return f"running={body.get('running')} return_code={body.get('return_code')}"
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError, OSError):
        return None


def _resolve_icr_path() -> str | None:
    """Read `icr_path` out of the backend's YAML settings, best-effort.

    Used by the wrapper to identify which binary to cull. We avoid importing
    piano_web here (different venv, import order). The file is small — just grep.
    """
    candidates = [
        REPO_ROOT / "data" / "icr-viz-settings.yaml",
        REPO_ROOT / "icr-viz-settings.yaml",
    ]
    for path in candidates:
        if not path.exists():
            continue
        try:
            for line in path.read_text(encoding="utf-8").splitlines():
                line = line.strip()
                if line.startswith("icr_path:"):
                    value = line.split(":", 1)[1].strip().strip('"').strip("'")
                    return value or None
        except Exception:
            continue
    return None


def _kill_icr_by_name(icr_path: str | None) -> int:
    """Hard-kill any ICR binary matching `icr_path`'s basename. Returns count killed.

    Belt-and-braces: if the API /icr/stop call didn't reach the backend (e.g. backend
    already dead), this ensures the child doesn't outlive the wrapper.
    """
    if not icr_path:
        return 0
    name = Path(icr_path).name
    if not name:
        return 0
    try:
        if os.name == "nt":
            r = subprocess.run(
                ["taskkill", "/F", "/IM", name],
                capture_output=True, text=True, timeout=5.0,
            )
            # taskkill returns 128 if no process matched — that's success-equivalent here.
            if r.returncode == 0:
                return 1
        else:
            r = subprocess.run(["pkill", "-TERM", "-f", name],
                               capture_output=True, text=True, timeout=5.0)
            if r.returncode == 0:
                return 1
    except Exception:
        pass
    return 0


def _kill_pid(pid: int) -> bool:
    """Terminate `pid`. Returns True on success."""
    try:
        if os.name == "nt":
            subprocess.run(["taskkill", "/F", "/PID", str(pid)],
                           capture_output=True, text=True, timeout=5.0, check=True)
        else:
            os.kill(pid, signal.SIGTERM)
            time.sleep(0.3)
            try:
                os.kill(pid, 0)
                os.kill(pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        return True
    except Exception:
        return False


def _describe_port_holder(host: str, port: int) -> str:
    """Best-effort lookup of the process holding the port."""
    try:
        # Try connecting — if it accepts, something is listening.
        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
            s.settimeout(0.3)
            s.connect((host, port))
        hint = f"something is already listening on {host}:{port}. "
    except OSError:
        hint = f"{host}:{port} is bound but not accepting connections. "

    pid = _find_listening_pid(port)
    if pid is not None:
        if os.name == "nt":
            hint += f"PID {pid}. Stop with: taskkill /F /PID {pid}  (or rerun with --kill-existing)"
        else:
            hint += f"PID {pid}. Stop with: kill {pid}  (or rerun with --kill-existing)"
        return hint
    if os.name != "nt":
        hint += f"Try: lsof -i:{port}  or  ss -ltnp | grep :{port}. "
    return hint + "Or rerun with --port <different>."


def _resolve_db_path(explicit: Path | None) -> Path:
    """Pick a DB path: explicit flag > ICR_VIZ_DB env > default."""
    if explicit is not None:
        return explicit.resolve()
    env_val = os.environ.get("ICR_VIZ_DB")
    if env_val:
        return Path(env_val).resolve()
    return DEFAULT_DB_PATH.resolve()


def _print_banner(host: str, port: int, db_path: Path, log_path: Path | None) -> None:
    """Pre-startup banner. Visible before uvicorn's own output scrolls in.

    Explicit split between FE and API to avoid the "why does / redirect to /docs"
    confusion: this port hosts the API + Swagger only; the React editor runs
    separately on the Vite dev server (default :3000).
    """
    url = f"http://{host}:{port}"
    fe_host = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    width = 76
    print("=" * width, file=sys.stderr)
    print(f"  ICR Piano Spectral Editor — backend", file=sys.stderr)
    print("-" * width, file=sys.stderr)
    print(f"  Open in browser", file=sys.stderr)
    print(f"    Editor UI   http://{fe_host}:3000/     (Vite dev — `npm run dev`)", file=sys.stderr)
    print(f"    Swagger     {url}/docs", file=sys.stderr)
    print(f"  Backend API (served by this process)", file=sys.stderr)
    print(f"    Root (→ /docs redirect) {url}/", file=sys.stderr)
    print(f"    API index   {url}/api", file=sys.stderr)
    print(f"    Health      {url}/api/health", file=sys.stderr)
    print(f"  Runtime", file=sys.stderr)
    print(f"    DB          {db_path}", file=sys.stderr)
    if log_path:
        print(f"    Log file    {log_path}", file=sys.stderr)
    print("=" * width, file=sys.stderr)


def _tee(src, sinks: list) -> None:
    """Read lines from `src` and write each to every sink in `sinks`.

    Runs in a background thread so stdout and stderr can be tee'd concurrently
    without interleaving garbage into the line buffers.
    """
    try:
        for raw in iter(src.readline, b""):
            line = raw.decode("utf-8", errors="replace")
            for sink in sinks:
                try:
                    sink.write(line)
                    sink.flush()
                except Exception:
                    # A single sink failure must not tear down the reader;
                    # the other sinks still deserve the line.
                    pass
    finally:
        try:
            src.close()
        except Exception:
            pass


def _build_uvicorn_cmd(args: argparse.Namespace) -> list[str]:
    cmd = [
        sys.executable, "-m", "uvicorn",
        "piano_web.main:app",
        "--app-dir", str(REPO_ROOT / "apps" / "api"),
        "--host", args.host,
        "--port", str(args.port),
        "--log-level", args.uvicorn_log_level,
    ]
    if args.reload:
        cmd.append("--reload")
    return cmd


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--host", default=os.environ.get("ICR_VIZ_HOST", "127.0.0.1"))
    parser.add_argument("--port", type=int, default=int(os.environ.get("ICR_VIZ_PORT", "8000")))
    parser.add_argument("--reload", action="store_true", help="Reload on code changes (dev)")
    parser.add_argument(
        "--db", type=Path, default=None,
        help=f"SQLite DB path (overrides ICR_VIZ_DB). Default: {DEFAULT_DB_PATH}",
    )
    parser.add_argument(
        "--uvicorn-log-level", default="info",
        choices=("critical", "error", "warning", "info", "debug", "trace"),
        help="uvicorn's own log level (separate from piano_web logging)",
    )
    parser.add_argument(
        "--log-file", type=Path, default=None,
        help="File to tee output into. Default: logs/backend-YYYYMMDD-HHMMSS.log",
    )
    parser.add_argument(
        "--no-file-log", action="store_true",
        help="Do not write a log file; output only to stdout/stderr.",
    )
    parser.add_argument(
        "--skip-port-check", action="store_true",
        help="Do not pre-check that --port is free (use if the check itself fails spuriously).",
    )
    parser.add_argument(
        "--kill-existing", action="store_true",
        help="If --port is busy, kill the LISTENING process and retry. "
             "Useful for `restart` flows: `python run-backend.py --kill-existing`.",
    )
    args = parser.parse_args()

    # Resolve SQLite path and ensure parent dir exists before child starts.
    db_path = _resolve_db_path(args.db)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    os.environ["ICR_VIZ_DB"] = str(db_path)   # child (uvicorn) inherits this env

    # Pre-check port availability so failure is obvious instead of a silent uvicorn crash.
    if not args.skip_port_check:
        ok, hint = _check_port_free(args.host, args.port)
        if not ok:
            if args.kill_existing:
                pid = _find_listening_pid(args.port)
                if pid is None:
                    # Busy without a LISTENING pid → port is in TIME_WAIT from
                    # a prior clean shutdown. Wait for it to drain instead of
                    # bailing out — the common case here is "restart right
                    # after Ctrl+C".
                    print(f"run-backend: port :{args.port} busy but no LISTENING pid; waiting for TIME_WAIT to clear…",
                          file=sys.stderr)
                    for _ in range(60):   # up to 12 s
                        time.sleep(0.2)
                        ok2, _ = _check_port_free(args.host, args.port)
                        if ok2:
                            break
                    else:
                        print(f"run-backend: ERROR: :{args.port} still busy after wait; {hint}",
                              file=sys.stderr)
                        return 2
                else:
                    # Ask the old backend to stop its child ICR gracefully before we hard-kill uvicorn.
                    icr_status = _stop_icr_via_api(args.host, args.port)
                    if icr_status is not None:
                        print(f"run-backend: --kill-existing: asked old backend to stop ICR → {icr_status}",
                              file=sys.stderr)
                    print(f"run-backend: --kill-existing: killing PID {pid} on :{args.port}", file=sys.stderr)
                    if not _kill_pid(pid):
                        print(f"run-backend: ERROR: failed to kill PID {pid}", file=sys.stderr)
                        return 2
                    # Belt-and-braces: taskkill any lingering ICR binary by image name.
                    icr_path = _resolve_icr_path()
                    n = _kill_icr_by_name(icr_path)
                    if n:
                        print(f"run-backend: --kill-existing: also killed lingering {Path(icr_path).name}",
                              file=sys.stderr)
                    # Wait briefly for the socket to free, then recheck.
                    for _ in range(20):
                        time.sleep(0.1)
                        ok2, _ = _check_port_free(args.host, args.port)
                        if ok2:
                            break
                    else:
                        print(f"run-backend: ERROR: port :{args.port} still busy after kill", file=sys.stderr)
                        return 2
            else:
                print(f"run-backend: ERROR: {hint}", file=sys.stderr)
                return 2

    log_path: Path | None = None
    if not args.no_file_log:
        log_path = args.log_file or _default_log_path()
        log_path.parent.mkdir(parents=True, exist_ok=True)

    _print_banner(args.host, args.port, db_path, log_path)

    cmd = _build_uvicorn_cmd(args)
    print(f"run-backend: launching: {' '.join(cmd)}", file=sys.stderr)
    if log_path:
        print(f"run-backend: tee log -> {log_path}", file=sys.stderr)

    # On Windows, attach to a new process group so we can send CTRL_BREAK_EVENT
    # without also killing the parent console. On POSIX, start a new session for
    # the same reason (pass signals to the whole group cleanly).
    popen_kwargs: dict = dict(
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        bufsize=0,   # unbuffered binary pipes — the tee thread reads line-by-line
    )
    if os.name == "nt":
        popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    else:
        popen_kwargs["start_new_session"] = True

    proc = subprocess.Popen(cmd, **popen_kwargs)

    log_file = log_path.open("a", encoding="utf-8") if log_path else None

    stdout_sinks: list = [sys.stdout]
    stderr_sinks: list = [sys.stderr]
    if log_file:
        stdout_sinks.append(log_file)
        stderr_sinks.append(log_file)

    threads = [
        threading.Thread(target=_tee, args=(proc.stdout, stdout_sinks), daemon=True),
        threading.Thread(target=_tee, args=(proc.stderr, stderr_sinks), daemon=True),
    ]
    for t in threads:
        t.start()

    shutdown_requested = threading.Event()

    def _forward_signal(signum: int, _frame) -> None:
        if shutdown_requested.is_set():
            print("run-backend: second signal — forcing kill.", file=sys.stderr)
            proc.kill()
            return
        shutdown_requested.set()
        sig_name = signal.Signals(signum).name if signum in signal.Signals.__members__.values() else str(signum)
        print(f"run-backend: got {sig_name}; forwarding to child (pid={proc.pid})", file=sys.stderr)
        try:
            if os.name == "nt":
                proc.send_signal(signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
            else:
                proc.send_signal(signal.SIGTERM)
        except Exception as exc:
            print(f"run-backend: signal forwarding failed: {exc}; falling back to kill", file=sys.stderr)
            proc.kill()

    signal.signal(signal.SIGINT, _forward_signal)
    try:
        signal.signal(signal.SIGTERM, _forward_signal)
    except (AttributeError, ValueError):
        pass  # SIGTERM not available on older Windows runtimes

    try:
        exit_code = proc.wait()
    finally:
        # Ensure the tee threads drain before we close the log file
        for t in threads:
            t.join(timeout=2.0)
        if log_file:
            log_file.flush()
            log_file.close()
        # Belt-and-braces: if the backend's own lifespan shutdown didn't stop
        # the child ICR (e.g. uvicorn was hard-killed), cull any lingering
        # binary whose image matches the configured icr_path.
        icr_path = _resolve_icr_path()
        n = _kill_icr_by_name(icr_path)
        if n:
            print(f"run-backend: culled lingering {Path(icr_path).name}", file=sys.stderr)

    print(f"run-backend: child exited with code {exit_code}", file=sys.stderr)
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
