"""MIDI/SysEx bridge endpoints (i6).

One process-wide MidiBridge instance holds the rtmidi handles. The router is
pure glue — it validates input, invokes MidiBridge, wraps results.

MVP scope (deferred: live-edit stream and per-partial push):

    GET    /api/midi/ports       — list input + output ports
    GET    /api/midi/status      — connection state
    POST   /api/midi/connect     — open input and/or output
    POST   /api/midi/disconnect
    POST   /api/midi/play-note   — note-on + scheduled note-off
    POST   /api/midi/ping        — send PING, wait for PONG, report RTT
    POST   /api/midi/push-bank   — serialise bank JSON and send as SET_BANK chunks

Errors:
    409 — operation attempted while the relevant port is not open
    404 — bank id for push-bank not found
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, Field

from piano_web.dependencies import get_repository
from piano_web.midi_bridge import (
    CORE_ACTIVE,
    CORE_ADDITIVE,
    CORE_ENGINE,
    CORE_IFF,
    CORE_PHYSICAL,
    CORE_SAMPLER,
    CORE_SINE,
    MidiBridge,
)
from piano_web.repository import BankRepository


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/midi", tags=["midi"])

RepoDep = Annotated[BankRepository, Depends(get_repository)]


# A single bridge lives for the app lifetime. Injected via dependency so tests
# can swap in a fake.
_bridge: MidiBridge | None = None


def get_bridge() -> MidiBridge:
    global _bridge
    if _bridge is None:
        _bridge = MidiBridge()
    return _bridge


def set_bridge(b: MidiBridge | None) -> None:
    global _bridge
    _bridge = b


BridgeDep = Annotated[MidiBridge, Depends(get_bridge)]


# ---------------------------------------------------------------------------
# Push-bank progress state
# ---------------------------------------------------------------------------
# One-slot snapshot of the most recent (or in-flight) push-bank job. Only one
# job runs at a time — a large bank is a few hundred frames and the bridge is
# single-threaded anyway. Keeping it singleton avoids the complexity of a job
# registry for a UX that's already trivially "click, wait, done".

import threading as _threading
import time as _time
from dataclasses import dataclass, field as _field


@dataclass
class _PushBankJob:
    active: bool = False
    sent: int = 0
    total: int = 0
    bank_id: str | None = None
    core: str | None = None
    done: bool = False
    error: str | None = None
    started_at: float | None = None


_push_job: _PushBankJob = _PushBankJob()
_push_lock = _threading.Lock()


def _push_job_snapshot() -> _PushBankJob:
    with _push_lock:
        return _PushBankJob(
            active=_push_job.active,
            sent=_push_job.sent,
            total=_push_job.total,
            bank_id=_push_job.bank_id,
            core=_push_job.core,
            done=_push_job.done,
            error=_push_job.error,
            started_at=_push_job.started_at,
        )


# ---------------------------------------------------------------------------
# Core-name → id mapping (API layer convenience)
# ---------------------------------------------------------------------------

_CORE_BY_NAME: dict[str, int] = {
    "active":    CORE_ACTIVE,
    "additive":  CORE_ADDITIVE,
    "physical":  CORE_PHYSICAL,
    "sampler":   CORE_SAMPLER,
    "sine":      CORE_SINE,
    "iff":       CORE_IFF,
    "engine":    CORE_ENGINE,
}


def _resolve_core(name: str) -> int:
    key = name.lower()
    if key not in _CORE_BY_NAME:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown core {name!r}; allowed: {sorted(_CORE_BY_NAME)}",
        )
    return _CORE_BY_NAME[key]


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class PortsResponse(BaseModel):
    input_ports: list[str]
    output_ports: list[str]


class ConnectBody(BaseModel):
    # Names preferred (stable across enumeration order). Indices kept for
    # back-compat — if both are given the name wins.
    input_port_name: str | None = None
    output_port_name: str | None = None
    input_port_index: int | None = None
    output_port_index: int | None = None


class StatusResponse(BaseModel):
    input_open: bool
    output_open: bool
    input_port_name: str | None
    output_port_name: str | None
    last_pong_ts: float | None


class PlayNoteBody(BaseModel):
    midi: int = Field(..., ge=0, le=127)
    velocity: int = Field(100, ge=1, le=127)
    duration_ms: int = Field(500, ge=10, le=10000)
    channel: int = Field(0, ge=0, le=15)


class PingResponse(BaseModel):
    ok: bool
    rtt_ms: float | None


class PushBankBody(BaseModel):
    bank_id: str
    core: Literal["active", "additive", "physical", "sampler", "sine", "iff"] = "active"
    # ms between SysEx frames. Prevents the Windows MME / loopMIDI driver
    # from saturating when the engine is slow to drain. 1 ms is safe for
    # large banks; 0 is fine for small banks or non-Windows.
    chunk_delay_ms: float = Field(1.0, ge=0.0, le=50.0)


class PushBankResponse(BaseModel):
    bank_id: str
    core: str
    n_frames: int
    bytes_sent: int


class PushBankProgressResponse(BaseModel):
    """Snapshot of the most recent push-bank job.

    The FE polls this while push-bank is in flight. `active` flips to False
    when the job settles (success or error); `error` is non-null on failure.
    `elapsed_s` lets the client distinguish slow-but-advancing from stalled.
    """
    active: bool
    sent: int
    total: int
    bank_id: str | None
    core: str | None
    done: bool
    error: str | None
    started_at: float | None
    elapsed_s: float | None


class PushPartialBody(BaseModel):
    """Instant-preview push of one (midi, velocity, k, parameter) → value.

    Used by the FE for live-edit audition: drag a weight slider, call this
    per debounced change, and the engine reflects the result in audio within
    milliseconds. Safe to spam at ~20-30 Hz.
    """

    midi: int = Field(..., ge=0, le=127)
    velocity: int = Field(..., ge=0, le=7)
    k: int = Field(..., ge=1, le=127)
    parameter: Literal["f_hz", "A0", "tau1", "tau2", "a1", "beat_hz", "phi"]
    value: float
    core: Literal["active", "additive", "physical", "sampler", "sine", "iff"] = "active"


class PushNoteParamBody(BaseModel):
    """Push one per-note scalar (f0_hz, B, attack_tau, ...) for instant audition."""

    midi: int = Field(..., ge=0, le=127)
    velocity: int = Field(..., ge=0, le=7)
    parameter: Literal[
        "f0_hz", "B", "attack_tau", "A_noise", "rms_gain",
        "phi_diff", "pan_correction", "stereo_width",
    ]
    value: float
    core: Literal["active", "additive", "physical", "sampler", "sine", "iff"] = "active"


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@router.get("/ports", response_model=PortsResponse)
async def list_ports(bridge: BridgeDep) -> PortsResponse:
    # Call through the instance so tests can inject a fake bridge with their
    # own list_input_ports/list_output_ports implementation.
    return PortsResponse(
        input_ports=bridge.list_input_ports(),
        output_ports=bridge.list_output_ports(),
    )


@router.get("/status", response_model=StatusResponse)
async def get_status(bridge: BridgeDep) -> StatusResponse:
    s = bridge.status()
    return StatusResponse(
        input_open=s.input_open,
        output_open=s.output_open,
        input_port_name=s.input_port_name,
        output_port_name=s.output_port_name,
        last_pong_ts=s.last_pong_ts,
    )


@router.post("/connect", response_model=StatusResponse)
async def connect(body: ConnectBody, bridge: BridgeDep) -> StatusResponse:
    # Resolve names → indices (name wins when both are given).
    in_idx = body.input_port_index
    out_idx = body.output_port_index
    if body.input_port_name is not None:
        names = bridge.list_input_ports()
        try:
            in_idx = names.index(body.input_port_name)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"input port {body.input_port_name!r} not present; got {names}",
            )
    if body.output_port_name is not None:
        names = bridge.list_output_ports()
        try:
            out_idx = names.index(body.output_port_name)
        except ValueError:
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"output port {body.output_port_name!r} not present; got {names}",
            )
    if in_idx is None and out_idx is None:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            "at least one of input_port_(name|index) or output_port_(name|index) must be given",
        )
    try:
        bridge.open(input_port_index=in_idx, output_port_index=out_idx)
    except Exception as exc:
        logger.warning("midi.connect.failed", extra={"detail": str(exc)})
        raise HTTPException(status.HTTP_400_BAD_REQUEST, f"failed to open ports: {exc}") from exc
    s = bridge.status()

    # Persist the resolved names so the next backend run auto-connects.
    try:
        from piano_web import settings as _settings
        _settings.save({"midi": {
            "input": s.input_port_name,
            "output": s.output_port_name,
        }})
    except Exception as exc:
        logger.warning("midi.connect.settings_save_failed", extra={"detail": str(exc)})

    return StatusResponse(
        input_open=s.input_open,
        output_open=s.output_open,
        input_port_name=s.input_port_name,
        output_port_name=s.output_port_name,
        last_pong_ts=s.last_pong_ts,
    )


@router.post("/disconnect", response_model=StatusResponse)
async def disconnect(bridge: BridgeDep) -> StatusResponse:
    bridge.close()
    s = bridge.status()
    return StatusResponse(
        input_open=s.input_open,
        output_open=s.output_open,
        input_port_name=s.input_port_name,
        output_port_name=s.output_port_name,
        last_pong_ts=s.last_pong_ts,
    )


@router.post("/play-note")
async def play_note(body: PlayNoteBody, bridge: BridgeDep) -> dict[str, object]:
    if not bridge.status().output_open:
        raise HTTPException(status.HTTP_409_CONFLICT, "MIDI output port is not open")
    bridge.send_note_on(channel=body.channel, midi=body.midi, velocity=body.velocity)

    # Schedule a matching note-off without blocking the request thread.
    duration_s = body.duration_ms / 1000.0

    async def _release() -> None:
        try:
            await asyncio.sleep(duration_s)
            # Re-check the output in case the user disconnected in the meantime.
            if bridge.status().output_open:
                bridge.send_note_off(channel=body.channel, midi=body.midi)
        except Exception as exc:   # pragma: no cover — diagnostic only
            logger.warning("midi.note_off.failed", extra={"detail": str(exc)})

    asyncio.create_task(_release())
    logger.info(
        "api.midi.play_note",
        extra={"midi": body.midi, "velocity": body.velocity, "duration_ms": body.duration_ms},
    )
    return {"midi": body.midi, "velocity": body.velocity, "duration_ms": body.duration_ms}


@router.post("/ping", response_model=PingResponse)
async def ping(bridge: BridgeDep) -> PingResponse:
    if not bridge.status().output_open:
        raise HTTPException(status.HTTP_409_CONFLICT, "MIDI output port is not open")
    # Ping blocks for up to 0.5s — run in a worker thread so we don't stall the event loop.
    loop = asyncio.get_running_loop()
    rtt = await loop.run_in_executor(None, bridge.ping, 0.5)
    if rtt is None:
        return PingResponse(ok=False, rtt_ms=None)
    return PingResponse(ok=True, rtt_ms=round(rtt * 1000.0, 2))


@router.post("/push-bank", response_model=PushBankResponse)
async def push_bank(
    body: PushBankBody,
    bridge: BridgeDep,
    repo: RepoDep,
) -> PushBankResponse:
    if not bridge.status().output_open:
        raise HTTPException(status.HTTP_409_CONFLICT, "MIDI output port is not open")

    bank = await repo.load(body.bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {body.bank_id!r} not found")

    payload = bank.to_icr_dict()
    bank_json = json.dumps(payload, ensure_ascii=False)
    core_id = _resolve_core(body.core)

    # Initialise the progress snapshot so the FE's poll sees an active job
    # immediately after it fires this POST.
    with _push_lock:
        _push_job.active = True
        _push_job.sent = 0
        _push_job.total = 0
        _push_job.bank_id = body.bank_id
        _push_job.core = body.core
        _push_job.done = False
        _push_job.error = None
        _push_job.started_at = _time.time()

    def _on_progress(sent: int, total: int) -> None:
        # Runs on the worker thread; lock-protected because the GET poll
        # reads this concurrently on the event loop.
        with _push_lock:
            _push_job.sent = sent
            _push_job.total = total

    # Send chunks on a worker thread — each SysEx send is a blocking syscall.
    import functools
    loop = asyncio.get_running_loop()
    try:
        n_frames = await loop.run_in_executor(
            None,
            functools.partial(
                bridge.push_bank,
                core_id=core_id,
                bank_json=bank_json,
                on_progress=_on_progress,
                chunk_delay_s=body.chunk_delay_ms / 1000.0,
            ),
        )
    except Exception as exc:
        with _push_lock:
            _push_job.active = False
            _push_job.done = True
            _push_job.error = str(exc)
        logger.warning("midi.push_bank.failed", extra={"detail": str(exc)})
        raise HTTPException(status.HTTP_500_INTERNAL_SERVER_ERROR,
                            f"push-bank failed: {exc}") from exc

    with _push_lock:
        _push_job.active = False
        _push_job.done = True

    return PushBankResponse(
        bank_id=body.bank_id,
        core=body.core,
        n_frames=n_frames,
        bytes_sent=len(bank_json.encode("utf-8")),
    )


@router.get("/push-bank/progress", response_model=PushBankProgressResponse)
async def push_bank_progress() -> PushBankProgressResponse:
    """Poll-friendly progress snapshot for the most recent push-bank job."""
    j = _push_job_snapshot()
    elapsed = (_time.time() - j.started_at) if j.started_at else None
    return PushBankProgressResponse(
        active=j.active,
        sent=j.sent,
        total=j.total,
        bank_id=j.bank_id,
        core=j.core,
        done=j.done,
        error=j.error,
        started_at=j.started_at,
        elapsed_s=elapsed,
    )


@router.post("/push-partial")
async def push_partial(body: PushPartialBody, bridge: BridgeDep) -> dict[str, object]:
    """Send one SET_NOTE_PARTIAL — instant-preview for live slider drags."""
    if not bridge.status().output_open:
        raise HTTPException(status.HTTP_409_CONFLICT, "MIDI output port is not open")
    core_id = _resolve_core(body.core)
    bridge.push_partial_param(
        core_id=core_id, midi=body.midi, velocity=body.velocity, k=body.k,
        parameter=body.parameter, value=body.value,
    )
    return {
        "midi": body.midi, "velocity": body.velocity, "k": body.k,
        "parameter": body.parameter, "value": body.value, "core": body.core,
    }


@router.post("/push-note-param")
async def push_note_param(body: PushNoteParamBody, bridge: BridgeDep) -> dict[str, object]:
    """Send one SET_NOTE_PARAM — instant-preview for per-note scalars."""
    if not bridge.status().output_open:
        raise HTTPException(status.HTTP_409_CONFLICT, "MIDI output port is not open")
    core_id = _resolve_core(body.core)
    bridge.push_note_param(
        core_id=core_id, midi=body.midi, velocity=body.velocity,
        parameter=body.parameter, value=body.value,
    )
    return {
        "midi": body.midi, "velocity": body.velocity,
        "parameter": body.parameter, "value": body.value, "core": body.core,
    }
