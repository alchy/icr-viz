"""/api/icr/* — launch and supervise the ICR engine binary from the GUI.

Endpoints:
    GET  /api/icr/settings             — current persisted icr_path
    POST /api/icr/settings             — update icr_path (body: {icr_path})
    GET  /api/icr/status               — running, pid, uptime, return_code
    POST /api/icr/launch               — start (body: optional {path, bank_id,
                                         core, extra_args}). When bank_id is
                                         given, the bank is exported to a temp
                                         file and passed via --core/--params.
    POST /api/icr/stop                 — graceful stop
"""

from __future__ import annotations

import json
import logging
import tempfile
from pathlib import Path
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from piano_web.icr_process import (
    IcrProcess,
    IcrProcessStatus,
    load_settings,
    save_settings,
)
from piano_web.dependencies import get_repository
from piano_web.repository import BankRepository


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/icr", tags=["icr"])


_proc: IcrProcess | None = None


def get_process() -> IcrProcess:
    global _proc
    if _proc is None:
        _proc = IcrProcess()
    return _proc


def set_process(p: IcrProcess | None) -> None:
    global _proc
    _proc = p


ProcDep = Annotated[IcrProcess, Depends(get_process)]


# ---- schemas ------------------------------------------------------------

class SettingsResponse(BaseModel):
    icr_path: str | None


class SettingsBody(BaseModel):
    icr_path: str


class StatusResponse(BaseModel):
    running: bool
    pid: int | None
    path: str | None
    started_at: float | None
    uptime_s: float | None
    return_code: int | None
    args: list[str]


IcrCoreName = Literal[
    "AdditiveCore", "PhysicalCore", "SamplerCore", "SineCore", "IFFSynthCore",
]


class LaunchBody(BaseModel):
    path: str | None = None                 # overrides settings.icr_path if supplied
    bank_id: str | None = None              # if set, export bank and pass --params
    core: IcrCoreName = "AdditiveCore"      # passed via --core
    extra_args: list[str] = []


# ---- endpoints ----------------------------------------------------------

def _to_response(s: IcrProcessStatus) -> StatusResponse:
    return StatusResponse(
        running=s.running,
        pid=s.pid,
        path=s.path,
        started_at=s.started_at,
        uptime_s=s.uptime_s,
        return_code=s.return_code,
        args=s.args,
    )


@router.get("/settings", response_model=SettingsResponse)
async def get_settings() -> SettingsResponse:
    settings = load_settings()
    return SettingsResponse(icr_path=settings.get("icr_path"))


@router.post("/settings", response_model=SettingsResponse)
async def update_settings(body: SettingsBody) -> SettingsResponse:
    settings = save_settings({"icr_path": body.icr_path})
    return SettingsResponse(icr_path=settings.get("icr_path"))


@router.get("/status", response_model=StatusResponse)
async def get_status(proc: ProcDep) -> StatusResponse:
    return _to_response(proc.status())


_BANK_EXPORT_DIR = Path(tempfile.gettempdir()) / "icr-viz-launch"


async def _export_bank_for_launch(bank_repo: BankRepository, bank_id: str) -> Path:
    """Dump bank JSON to a temp file and return its path.

    The file is overwritten on each launch so stale bank versions don't linger
    on disk. The temp dir is created lazily. icrgui reads this via --params.
    """
    bank = await bank_repo.load(bank_id)
    if bank is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"bank {bank_id!r} not found — cannot launch with it",
        )
    _BANK_EXPORT_DIR.mkdir(parents=True, exist_ok=True)
    # Keep the filename stable per bank_id so repeated launches don't leak.
    safe = "".join(c if c.isalnum() or c in "._-" else "_" for c in bank.id)
    out = _BANK_EXPORT_DIR / f"{safe}.icr.json"
    out.write_text(json.dumps(bank.to_icr_dict(), ensure_ascii=False, indent=2),
                   encoding="utf-8")
    logger.info("api.icr.export_for_launch",
                extra={"bank_id": bank.id, "path": str(out)})
    return out


@router.post("/launch", response_model=StatusResponse)
async def launch(
    body: LaunchBody,
    proc: ProcDep,
    bank_repo: Annotated[BankRepository, Depends(get_repository)],
) -> StatusResponse:
    settings = load_settings()
    path = body.path or settings.get("icr_path")
    if not path:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "no icr_path — POST /api/icr/settings with {icr_path: <path>} first, or include path in body",
        )

    # Probe the current rtmidi enumeration so we can validate the names we're
    # about to hand to icrgui. Windows MME + loopMIDI can enumerate a virtual
    # port in only one direction; passing an unresolvable name would make
    # icrgui's resolveMidiPort fall back to index 0 and pick something wild
    # (e.g. Microsoft GS Wavetable Synth). Better to skip the flag.
    from piano_web.midi_bridge import MidiBridge as _MidiBridge
    try:
        _sys_in = _MidiBridge.list_input_ports()
        _sys_out = _MidiBridge.list_output_ports()
    except Exception:
        _sys_in = []
        _sys_out = []

# Compose CLI args. Each --foo flag is skipped when its settings key is
    # None, so users only opt in to what they need. Schema mirrors icrgui 1:1.
    args: list[str] = ["--core", body.core]

    def _str_or_none(v: object) -> str | None:
        return str(v) if v else None

    bank_file: Path | None = None
    if body.bank_id:
        bank_file = await _export_bank_for_launch(bank_repo, body.bank_id)
        args += ["--soundbank-file", str(bank_file)]

    # Soundbank directory — single source of truth. Also used by the ingest
    # script, so keeping it at the top level avoids two places to configure.
    # Fall back to the temp export dir when the user hasn't configured one,
    # so icrgui's bank dropdown always has *something* to show (our exported
    # bank) instead of rendering empty.
    bank_dir = _str_or_none(settings.get("bank_dir"))
    if not bank_dir and bank_file is not None:
        bank_dir = str(bank_file.parent)
    if bank_dir:
        args += ["--soundbank-dir", bank_dir]

    # Engine overrides — a grouped namespace so the mapping to icrgui's flags
    # is obvious on inspection of the YAML.
    eng = settings.get("engine") or {}
    if isinstance(eng, dict):
        for key, flag in (
            ("ir_file",          "--ir-file"),
            ("ir_dir",           "--ir-dir"),
            ("config_file",      "--engine-config-file"),
            ("config_dir",       "--engine-config-dir"),
            ("core_config_file", "--core-config-file"),
        ):
            value = _str_or_none(eng.get(key))
            if value:
                args += [flag, value]

    # MIDI: push the editor-side port pair into icrgui with SWAPPED direction.
    # Editor's OUTPUT (where we send SysEx) is engine's INPUT; editor's INPUT
    # (where we listen for PONG) is engine's OUTPUT.
    #
    # Guard: on Windows MME + loopMIDI a virtual port often enumerates in
    # only one direction. If we hand icrgui a name that isn't in the matching
    # enum, its resolveMidiPort falls back to index 0 and picks something
    # wildly wrong (e.g. Microsoft GS Wavetable Synth). Skip the flag when
    # the name can't be resolved in the opposite direction — the user can
    # pick manually in icrgui without being misled.
    midi_cfg = settings.get("midi") or {}
    if isinstance(midi_cfg, dict):
        editor_out = _str_or_none(midi_cfg.get("output"))
        editor_in = _str_or_none(midi_cfg.get("input"))
        if editor_out and editor_out in _sys_in:
            args += ["--midi-in", editor_out]
        elif editor_out:
            logger.warning(
                "api.icr.launch.midi_in_unresolvable",
                extra={"editor_output": editor_out, "engine_inputs": _sys_in},
            )
        if editor_in and editor_in in _sys_out:
            args += ["--midi-out", editor_in]
        elif editor_in:
            logger.warning(
                "api.icr.launch.midi_out_unresolvable",
                extra={"editor_input": editor_in, "engine_outputs": _sys_out},
            )

    args += list(body.extra_args)

    try:
        s = proc.launch(path, extra_args=args)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    logger.info("api.icr.launch",
                extra={"path": path, "pid": s.pid, "core": body.core,
                       "bank_id": body.bank_id})
    return _to_response(s)


@router.post("/stop", response_model=StatusResponse)
async def stop(proc: ProcDep) -> StatusResponse:
    s = proc.stop()
    logger.info("api.icr.stop")
    return _to_response(s)
