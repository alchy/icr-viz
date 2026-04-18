"""Launch and supervise the ICR engine binary (icr.exe / icr).

Minimal process manager:
  - `launch(path, extra_args)` — start subprocess; returns status.
  - `stop()` — graceful on POSIX (SIGTERM), CTRL_BREAK on Windows, hard kill
    as fallback.
  - `status()` — running flag, pid, uptime, return_code.

Config (ICR binary path) persists to `settings.json` under the app data dir so
the GUI "connect to engine" flow survives a backend restart.

Kept in its own module — not wired into any router yet; icr_router.py (next)
does the HTTP surface.
"""

from __future__ import annotations

import json
import logging
import os
import signal
import subprocess
import threading
import time
from dataclasses import dataclass
from pathlib import Path


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Settings file
# ---------------------------------------------------------------------------

# Settings used to live as a small JSON blob here. Since i6.4 they are
# handled by piano_web.settings (YAML-backed, richer schema). These thin
# shims keep the existing ICR endpoints unchanged: icr_path is now the
# top-level `icr_path` key in the unified settings file.
from . import settings as _settings_module


def load_settings() -> dict:
    return _settings_module.load()


def save_settings(updates: dict) -> dict:
    return _settings_module.save(updates)


# ---------------------------------------------------------------------------
# Process manager
# ---------------------------------------------------------------------------

@dataclass
class IcrProcessStatus:
    running: bool
    pid: int | None
    path: str | None
    started_at: float | None
    uptime_s: float | None
    return_code: int | None
    args: list[str]


class IcrProcess:
    """One icr.exe subprocess, started / stopped from FastAPI."""

    def __init__(self) -> None:
        self._proc: subprocess.Popen | None = None
        self._path: str | None = None
        self._args: list[str] = []
        self._started_at: float | None = None
        self._lock = threading.Lock()

    # -- lifecycle ------------------------------------------------------

    def launch(self, path: str | Path, *, extra_args: list[str] | None = None) -> IcrProcessStatus:
        path = Path(path).resolve()
        if not path.exists():
            raise FileNotFoundError(f"ICR binary not found: {path}")
        if not path.is_file():
            raise ValueError(f"ICR path is not a file: {path}")

        with self._lock:
            self._stop_locked()

            args = [str(path), *(extra_args or [])]
            popen_kwargs: dict = {
                "stdout": subprocess.DEVNULL,   # don't pipe — we don't read it, and PIPE + no reader deadlocks on Windows
                "stderr": subprocess.DEVNULL,
            }
            if os.name == "nt":
                popen_kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP   # type: ignore[attr-defined]
            else:
                popen_kwargs["start_new_session"] = True

            try:
                self._proc = subprocess.Popen(args, **popen_kwargs)
            except OSError as exc:
                logger.error("icr.launch_failed", extra={"path": str(path), "detail": str(exc)})
                raise RuntimeError(f"failed to launch {path}: {exc}") from exc

            self._path = str(path)
            self._args = list(args)
            self._started_at = time.time()
            logger.info("icr.launched", extra={"pid": self._proc.pid, "path": str(path)})

        return self.status()

    def stop(self, *, timeout_s: float = 5.0) -> IcrProcessStatus:
        with self._lock:
            self._stop_locked(timeout_s=timeout_s)
        return self.status()

    def _stop_locked(self, *, timeout_s: float = 5.0) -> None:
        if self._proc is None:
            return
        if self._proc.poll() is not None:
            # Already dead
            self._proc = None
            self._started_at = None
            return

        try:
            if os.name == "nt":
                self._proc.send_signal(signal.CTRL_BREAK_EVENT)   # type: ignore[attr-defined]
            else:
                self._proc.send_signal(signal.SIGTERM)
        except Exception as exc:
            logger.warning("icr.stop.signal_failed", extra={"detail": str(exc)})

        try:
            self._proc.wait(timeout=timeout_s)
        except subprocess.TimeoutExpired:
            logger.warning("icr.stop.timeout_kill", extra={"pid": self._proc.pid})
            self._proc.kill()
            try:
                self._proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass

        logger.info("icr.stopped", extra={"return_code": self._proc.returncode})
        self._proc = None
        self._started_at = None

    # -- state ---------------------------------------------------------

    def status(self) -> IcrProcessStatus:
        with self._lock:
            if self._proc is None:
                return IcrProcessStatus(
                    running=False, pid=None, path=self._path,
                    started_at=None, uptime_s=None, return_code=None,
                    args=list(self._args),
                )
            poll = self._proc.poll()
            if poll is None:
                return IcrProcessStatus(
                    running=True,
                    pid=self._proc.pid,
                    path=self._path,
                    started_at=self._started_at,
                    uptime_s=(time.time() - self._started_at) if self._started_at else None,
                    return_code=None,
                    args=list(self._args),
                )
            # Process exited on its own — record return code.
            return IcrProcessStatus(
                running=False,
                pid=None,
                path=self._path,
                started_at=None,
                uptime_s=None,
                return_code=poll,
                args=list(self._args),
            )
