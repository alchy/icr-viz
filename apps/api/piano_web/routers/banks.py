"""Read-only HTTP routes for browsing banks / notes / curves (i1 scope)."""

from __future__ import annotations

import logging
import time
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from piano_core.constants import MATH_PARAMS

from ..dependencies import get_repository
from ..repository import BankRepository
from ..schemas import (
    BankDetail,
    BankSummary,
    CurvesPayload,
    NoteDetail,
    NoteIndex,
    bank_detail_from_domain,
    bank_summary_from_row,
    curves_from_note,
    note_detail_from_domain,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/banks", tags=["banks"])

RepoDep = Annotated[BankRepository, Depends(get_repository)]


@router.get("", response_model=list[BankSummary])
async def list_banks(repo: RepoDep) -> list[BankSummary]:
    rows = await repo.list_summaries()
    return [bank_summary_from_row(r) for r in rows]


@router.get("/{bank_id}", response_model=BankDetail)
async def get_bank(bank_id: str, repo: RepoDep) -> BankDetail:
    bank = await repo.load(bank_id)
    if bank is None:
        logger.info("api.bank.not_found", extra={"bank_id": bank_id})
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"bank {bank_id!r} not found")
    return bank_detail_from_domain(bank)


@router.get("/{bank_id}/notes", response_model=list[NoteIndex])
async def list_notes(bank_id: str, repo: RepoDep) -> list[NoteIndex]:
    bank = await repo.load(bank_id)
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"bank {bank_id!r} not found")
    return [NoteIndex(midi=m, velocity=v) for (m, v) in bank.note_ids]


@router.get("/{bank_id}/notes/{midi}/{velocity}", response_model=NoteDetail)
async def get_note(
    bank_id: str,
    midi: int,
    velocity: int,
    repo: RepoDep,
) -> NoteDetail:
    bank = await repo.load(bank_id)
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"bank {bank_id!r} not found")
    note = bank.get_note(midi, velocity)
    if note is None:
        logger.info("api.note.not_found", extra={"bank_id": bank_id, "midi": midi, "velocity": velocity})
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"note ({midi}, {velocity}) not in bank {bank_id!r}",
        )
    return note_detail_from_domain(note)


@router.get("/{bank_id}/notes/{midi}/{velocity}/curves", response_model=CurvesPayload)
async def get_note_curves(
    bank_id: str,
    midi: int,
    velocity: int,
    repo: RepoDep,
    parameters: list[str] | None = Query(
        default=None,
        description=f"Subset of parameters to return. Defaults to all MATH_PARAMS: {list(MATH_PARAMS)}",
    ),
) -> CurvesPayload:
    t0 = time.perf_counter()
    bank = await repo.load(bank_id)
    if bank is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"bank {bank_id!r} not found")
    note = bank.get_note(midi, velocity)
    if note is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"note ({midi}, {velocity}) not in bank {bank_id!r}",
        )

    if parameters is not None:
        unknown = [p for p in parameters if p not in MATH_PARAMS]
        if unknown:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=f"unknown parameters: {unknown}; allowed: {list(MATH_PARAMS)}",
            )
    payload = curves_from_note(note, parameters=parameters)
    logger.info(
        "api.curves",
        extra={
            "bank_id": bank_id,
            "midi": midi,
            "velocity": velocity,
            "n_params": len(payload.parameters),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )
    return payload
