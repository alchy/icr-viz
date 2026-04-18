"""Operator endpoints (i2+).

For i2 we expose only `/api/ops/anchor-interpolate`. It runs the canonical
`completion.anchor_interpolate` pipeline for a set of (note, parameter)
pairs and returns curve+sigma samples.

When `commit=True`, the target notes' partials have their parameter values
rewritten to match the interpolated curve (k-by-k), and a new Bank is
persisted with updated partials + origin="derived" on the touched partials.
The original partials stay in the parent Bank (immutable chain).
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import replace
from typing import Annotated

import numpy as np
from fastapi import APIRouter, Depends, HTTPException, status

from piano_core.completion.anchor_interpolate import (
    AnchorObservation,
    InterpolationResult,
    anchor_interpolate,
)
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial

from ..anchor_repository import AnchorRepository
from ..dependencies import get_anchor_repository, get_repository
from ..repository import BankRepository
from ..schemas import (
    AnchorInterpolateParams,
    AnchorInterpolateResponse,
    ParameterCurveDiag,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["operators"])


RepoDep = Annotated[BankRepository, Depends(get_repository)]
AnchorRepoDep = Annotated[AnchorRepository, Depends(get_anchor_repository)]


def _new_bank_id(parent_id: str) -> str:
    return f"{parent_id}.{uuid.uuid4().hex[:8]}"


@router.post("/anchor-interpolate", response_model=AnchorInterpolateResponse)
async def anchor_interpolate_endpoint(
    params: AnchorInterpolateParams,
    bank_id: str,
    bank_repo: RepoDep,
    anchor_repo: AnchorRepoDep,
) -> AnchorInterpolateResponse:
    """Run AnchorInterpolate on one or more (note, parameter) combinations.

    `bank_id` is passed as a query parameter so the endpoint URL stays short
    (the body carries the potentially large list of target notes and params).
    """
    bank = await bank_repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    per_parameter: list[ParameterCurveDiag] = []
    partials_update_plan: dict[tuple[int, int], dict[int, dict[str, float]]] = {}

    for (midi, velocity) in params.target_note_ids:
        note = bank.get_note(midi, velocity)
        if note is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND,
                f"note ({midi}, {velocity}) not in bank {bank_id!r}",
            )
        note_anchors = bank.anchors_for_note(midi, velocity)
        anchor_obs = [
            AnchorObservation(
                k=a.k, parameter=a.parameter, value=a.value,
                weight=a.weight, sigma=None,
            )
            for a in note_anchors
        ]

        # k range: explicit override, else defaults to note's partial coverage
        if params.k_range is not None:
            k_range = (int(params.k_range[0]), int(params.k_range[1]))
        else:
            ks = [p.k for p in note.partials]
            if ks:
                k_range = (min(ks), max(ks))
            else:
                # Without partials, default to a conservative range
                k_range = (1, 60)

        for parameter in params.parameters:
            result = anchor_interpolate(
                partials=note.partials,
                anchors=anchor_obs,
                parameter=parameter,
                prior_weight=params.prior_weight,
                smoothing=params.smoothing,
                random_seed=params.random_seed,
                k_range=k_range,
            )
            diag = _result_to_curve_diag(
                result, midi=midi, velocity=velocity, parameter=parameter,
            )
            per_parameter.append(diag)

            if params.commit:
                _record_curve_update(
                    partials_update_plan, midi, velocity, result, parameter,
                )

    new_bank_id = None
    parent_id = None
    if params.commit:
        new_bank_id = _new_bank_id(bank.id)
        parent_id = bank.id
        new_bank = _apply_curve_update(bank, partials_update_plan, new_id=new_bank_id)
        await bank_repo.save(new_bank)
        # Anchors travel with the Bank JSON blob — see routers.anchors for the rationale.

    logger.info(
        "api.ops.anchor_interpolate",
        extra={
            "bank_id": bank.id,
            "commit": params.commit,
            "new_bank_id": new_bank_id,
            "n_targets": len(params.target_note_ids),
            "n_params": len(params.parameters),
        },
    )
    return AnchorInterpolateResponse(
        new_bank_id=new_bank_id,
        parent_id=parent_id,
        per_parameter=per_parameter,
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _result_to_curve_diag(
    result: InterpolationResult,
    *,
    midi: int,
    velocity: int,
    parameter: str,
) -> ParameterCurveDiag:
    k_lo, k_hi = result.coverage
    grid = list(range(k_lo, k_hi + 1))
    if not grid:
        return ParameterCurveDiag(
            midi=midi, velocity=velocity, parameter=parameter,
            k_grid=[], values=[], sigmas=[],
            lambda_used=result.lambda_used,
            used_pchip=result.used_pchip,
            coverage=result.coverage,
            n_observations=result.n_observations,
            n_anchors_used=result.n_anchors_used,
            warnings=list(result.warnings),
        )
    ks_arr = np.array(grid, dtype=float)
    vals = np.asarray(result.estimate(ks_arr), dtype=float)
    sigmas = np.asarray(result.sigma(ks_arr), dtype=float)
    # Recharts / JSON don't tolerate NaN — substitute 0/big sigma.
    vals = np.where(np.isfinite(vals), vals, 0.0)
    sigmas = np.where(np.isfinite(sigmas), sigmas, 1e6)
    return ParameterCurveDiag(
        midi=midi,
        velocity=velocity,
        parameter=parameter,
        k_grid=grid,
        values=[float(v) for v in vals],
        sigmas=[float(s) for s in sigmas],
        lambda_used=result.lambda_used,
        used_pchip=result.used_pchip,
        coverage=result.coverage,
        n_observations=result.n_observations,
        n_anchors_used=result.n_anchors_used,
        warnings=list(result.warnings),
    )


def _record_curve_update(
    plan: dict[tuple[int, int], dict[int, dict[str, float]]],
    midi: int,
    velocity: int,
    result: InterpolationResult,
    parameter: str,
) -> None:
    """Buffer the per-k interpolated value for commit-time application."""
    if parameter == "f_coef":
        # f_coef is derived; committing it is not a no-op and needs a separate
        # inharmonicity-aware code path. Skip for i2 and flag via warnings
        # already surfaced from the math layer.
        return
    key = (midi, velocity)
    note_plan = plan.setdefault(key, {})
    k_lo, k_hi = result.coverage
    for k in range(k_lo, k_hi + 1):
        v = float(result.estimate(float(k)))
        if not math.isfinite(v):
            continue
        per_k = note_plan.setdefault(k, {})
        per_k[parameter] = v


def _apply_curve_update(
    bank: Bank,
    plan: dict[tuple[int, int], dict[int, dict[str, float]]],
    *,
    new_id: str,
) -> Bank:
    """Replace parameter values on targeted partials with the interpolated curve."""
    updated_notes: list[Note] = []
    for note in bank.notes:
        key = (note.midi, note.vel)
        if key not in plan:
            updated_notes.append(note)
            continue
        note_plan = plan[key]
        updated_partials: list[Partial] = []
        for p in note.partials:
            if p.k not in note_plan:
                updated_partials.append(p)
                continue
            overrides = note_plan[p.k]
            new_fields: dict[str, float | str] = dict(overrides)
            new_fields["origin"] = "derived"
            updated_partials.append(replace(p, **new_fields))
        updated_notes.append(replace(note, partials=tuple(updated_partials)))

    return bank.with_notes(tuple(updated_notes), new_id=new_id)
