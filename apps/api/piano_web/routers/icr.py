"""/api/icr/* — launch and supervise the ICR engine binary from the GUI.

Endpoints:
    GET  /api/icr/settings             — current persisted icr_path
    POST /api/icr/settings             — update icr_path (body: {icr_path})
    GET  /api/icr/status               — running, pid, uptime, return_code
    POST /api/icr/launch               — start (body: optional {path, extra_args})
    POST /api/icr/stop                 — graceful stop
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel

from piano_web.icr_process import (
    IcrProcess,
    IcrProcessStatus,
    load_settings,
    save_settings,
)


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


class LaunchBody(BaseModel):
    path: str | None = None     # overrides settings.icr_path if supplied
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


@router.post("/launch", response_model=StatusResponse)
async def launch(body: LaunchBody, proc: ProcDep) -> StatusResponse:
    settings = load_settings()
    path = body.path or settings.get("icr_path")
    if not path:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "no icr_path — POST /api/icr/settings with {icr_path: <path>} first, or include path in body",
        )
    try:
        s = proc.launch(path, extra_args=body.extra_args)
    except FileNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except (ValueError, RuntimeError) as exc:
        raise HTTPException(status.HTTP_400_BAD_REQUEST, str(exc)) from exc

    logger.info("api.icr.launch", extra={"path": path, "pid": s.pid})
    return _to_response(s)


@router.post("/stop", response_model=StatusResponse)
async def stop(proc: ProcDep) -> StatusResponse:
    s = proc.stop()
    logger.info("api.icr.stop")
    return _to_response(s)
