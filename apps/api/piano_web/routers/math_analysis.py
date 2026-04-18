"""Math-analysis endpoints (i4).

GET /api/banks/:id/math-analysis
    Full per-bank report (per-note fits + aggregates + violations). Cached
    in-process for 10 minutes keyed by bank_id (banks are immutable so the
    cache is trivially invalidated by new_bank_id on mutations).

GET /api/banks/:id/notes/:m/:v/physical-fit
    Per-note fit detail — the same NoteMathDiag row from the bank report,
    faster to compute when callers only need one note.

GET /api/banks/:id/cross-note/:parameter/:velocity/:k
    Keyboard-wide trace for one (parameter, velocity, k) slice — used by
    the MathRelationshipsPanel to render a single trend mini-plot without
    downloading the entire report.
"""

from __future__ import annotations

import logging
import time
from collections import OrderedDict
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from piano_core.analysis import (
    MathAnalysisReport,
    analyze_bank,
    fit_note,
)

from ..dependencies import get_repository
from ..repository import BankRepository


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/banks", tags=["analysis"])


RepoDep = Annotated[BankRepository, Depends(get_repository)]


# ---------------------------------------------------------------------------
# In-process cache for MathAnalysisReport
# ---------------------------------------------------------------------------

_REPORT_CACHE_SIZE = 8
_REPORT_CACHE_TTL = 10 * 60   # 10 minutes
_report_cache: "OrderedDict[str, tuple[float, MathAnalysisReport]]" = OrderedDict()


def _cache_get(bank_id: str) -> MathAnalysisReport | None:
    entry = _report_cache.get(bank_id)
    if entry is None:
        return None
    ts, report = entry
    if time.time() - ts > _REPORT_CACHE_TTL:
        _report_cache.pop(bank_id, None)
        return None
    _report_cache.move_to_end(bank_id)
    return report


def _cache_put(bank_id: str, report: MathAnalysisReport) -> None:
    _report_cache[bank_id] = (time.time(), report)
    _report_cache.move_to_end(bank_id)
    while len(_report_cache) > _REPORT_CACHE_SIZE:
        _report_cache.popitem(last=False)


def _invalidate(bank_id: str) -> None:
    _report_cache.pop(bank_id, None)


# ---------------------------------------------------------------------------
# GET /api/banks/:id/math-analysis
# ---------------------------------------------------------------------------

@router.get("/{bank_id}/math-analysis")
async def math_analysis_endpoint(
    bank_id: str,
    repo: RepoDep,
) -> dict:
    """Return the full MathAnalysisReport for the bank as a JSON dict.

    Response shape matches `MathAnalysisReport.as_dict()`.
    """
    cached = _cache_get(bank_id)
    if cached is not None:
        logger.debug("api.math_analysis.cache_hit", extra={"bank_id": bank_id})
        return cached.as_dict()

    bank = await repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    t0 = time.perf_counter()
    report = analyze_bank(bank)
    _cache_put(bank_id, report)
    logger.info(
        "api.math_analysis.generated",
        extra={
            "bank_id": bank_id,
            "n_notes": report.n_notes,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 1),
        },
    )
    return report.as_dict()


# ---------------------------------------------------------------------------
# GET /api/banks/:id/notes/:m/:v/physical-fit
# ---------------------------------------------------------------------------

@router.get("/{bank_id}/notes/{midi}/{velocity}/physical-fit")
async def physical_fit_endpoint(
    bank_id: str,
    midi: int,
    velocity: int,
    repo: RepoDep,
) -> dict:
    """Return a single note's NoteMathDiag without loading the full bank report."""
    bank = await repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")
    note = bank.get_note(midi, velocity)
    if note is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"note ({midi}, {velocity}) not in bank {bank_id!r}",
        )
    diag = fit_note(note)
    return diag.as_dict()


# ---------------------------------------------------------------------------
# GET /api/banks/:id/cross-note/:parameter/:velocity/:k
# ---------------------------------------------------------------------------

@router.get("/{bank_id}/cross-note/{parameter}/{velocity}/{k}")
async def cross_note_slice_endpoint(
    bank_id: str,
    parameter: str,
    velocity: int,
    k: int,
    repo: RepoDep,
) -> dict:
    """Return a (midi, value) series for one (parameter, velocity, k) slice.

    Backs the single-parameter mini-plots in the relationships panel without
    forcing the client to parse the full per_note map.
    """
    bank = await repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    series: list[dict] = []
    for note in bank.notes:
        if note.vel != velocity:
            continue
        partial = next((p for p in note.partials if p.k == k), None)
        if partial is None:
            continue
        if not hasattr(partial, parameter):
            raise HTTPException(
                status.HTTP_400_BAD_REQUEST,
                f"unknown parameter {parameter!r} on Partial",
            )
        series.append({
            "midi": note.midi,
            "value": float(getattr(partial, parameter)),
        })

    series.sort(key=lambda row: row["midi"])
    return {
        "bank_id": bank.id,
        "parameter": parameter,
        "velocity": velocity,
        "k": k,
        "n": len(series),
        "series": series,
    }


# Export for tests that need to reach into the cache.
def _cache_clear_for_tests() -> None:
    _report_cache.clear()
