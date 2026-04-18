"""Surface grid endpoint (i5.4).

GET /api/banks/{id}/surface?parameter=tau1&velocity=5&color_by=value

Returns a 2D (midi × k) grid of values for a chosen parameter/velocity pair.
FE plots this as a heatmap (Recharts or plotly). `difference_from` parameter
enables diff-mode against a parent bank for visualising edit effects.
"""

from __future__ import annotations

import logging
import math
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from piano_core.constants import STORAGE_PARAMS
from piano_core.models.bank import Bank

from ..dependencies import get_repository
from ..repository import BankRepository


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/banks", tags=["visualization"])

RepoDep = Annotated[BankRepository, Depends(get_repository)]


class SurfaceGridResponse(BaseModel):
    bank_id: str
    parameter: str
    velocity: int
    color_by: str
    midi: list[int]
    k: list[int]
    z: list[list[float | None]]         # rows = midi, cols = k; None = no partial
    color: list[list[float | None]] | None = None
    origin: list[list[str | None]] | None = None


@router.get("/{bank_id}/surface", response_model=SurfaceGridResponse)
async def surface_grid_endpoint(
    bank_id: str,
    repo: RepoDep,
    parameter: str = Query(..., description="Partial attribute (tau1, A0, ...)"),
    velocity: int = Query(..., ge=0),
    color_by: str = Query("value", pattern="^(value|fit_quality|origin)$"),
    difference_from: str | None = Query(None, description="Parent bank id for diff mode"),
    k_max: int | None = Query(None, ge=1),
) -> SurfaceGridResponse:
    bank = await repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    if parameter not in STORAGE_PARAMS:
        raise HTTPException(
            status.HTTP_400_BAD_REQUEST,
            f"unknown parameter {parameter!r}; allowed: {list(STORAGE_PARAMS)}",
        )

    # Build midi/k axes from the notes at the requested velocity.
    notes = [n for n in bank.notes if n.vel == velocity]
    if not notes:
        return SurfaceGridResponse(
            bank_id=bank.id, parameter=parameter, velocity=velocity,
            color_by=color_by, midi=[], k=[], z=[],
        )

    midis = sorted({n.midi for n in notes})
    k_bound = k_max if k_max is not None else max((p.k for n in notes for p in n.partials), default=0)
    ks = list(range(1, k_bound + 1))

    by_midi = {n.midi: n for n in notes}

    z: list[list[float | None]] = [[None] * len(ks) for _ in midis]
    color: list[list[float | None]] | None = (
        [[None] * len(ks) for _ in midis] if color_by == "fit_quality" else None
    )
    origin_grid: list[list[str | None]] | None = (
        [[None] * len(ks) for _ in midis] if color_by == "origin" else None
    )

    for i, m in enumerate(midis):
        note = by_midi[m]
        for p in note.partials:
            if p.k > k_bound:
                continue
            val = getattr(p, parameter, None)
            if val is None or not math.isfinite(val):
                continue
            z[i][p.k - 1] = float(val)
            if color is not None:
                color[i][p.k - 1] = float(p.fit_quality)
            if origin_grid is not None:
                origin_grid[i][p.k - 1] = p.origin

    # Diff mode — subtract parent bank's same parameter/velocity grid.
    if difference_from:
        parent = await repo.load(difference_from)
        if parent is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"parent bank {difference_from!r} not found",
            )
        parent_by_midi = {n.midi: n for n in parent.notes if n.vel == velocity}
        for i, m in enumerate(midis):
            parent_note = parent_by_midi.get(m)
            if parent_note is None:
                continue
            for p in parent_note.partials:
                if p.k > k_bound:
                    continue
                parent_val = getattr(p, parameter, None)
                if parent_val is None or not math.isfinite(parent_val):
                    continue
                current = z[i][p.k - 1]
                z[i][p.k - 1] = (current - float(parent_val)) if current is not None else None

    logger.info(
        "api.surface_grid",
        extra={
            "bank_id": bank_id, "parameter": parameter, "velocity": velocity,
            "n_midi": len(midis), "n_k": len(ks),
            "difference_from": difference_from,
        },
    )
    return SurfaceGridResponse(
        bank_id=bank.id,
        parameter=parameter,
        velocity=velocity,
        color_by=color_by,
        midi=midis,
        k=ks,
        z=z,
        color=color,
        origin=origin_grid,
    )
