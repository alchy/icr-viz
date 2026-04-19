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

# Compose CLI args: --core <Name> [--soundbank-file <tempfile>]
    # [--soundbank-dir <first bank_dirs entry>] plus any caller extras.
    args: list[str] = ["--core", body.core]
    if body.bank_id:
        bank_file = await _export_bank_for_launch(bank_repo, body.bank_id)
        args += ["--soundbank-file", str(bank_file)]
    # Let the engine's bank dropdown work when launched from a different cwd:
    # use the first configured bank_dirs entry as --soundbank-dir.
    bank_dirs = settings.get("bank_dirs") or []
    if isinstance(bank_dirs, list) and bank_dirs:
        first = str(bank_dirs[0]) if bank_dirs[0] else ""
        if first:
            args += ["--soundbank-dir", first]
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
