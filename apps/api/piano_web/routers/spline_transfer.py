"""POST /api/ops/spline-transfer — directed curve transfer."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from piano_core.operators.spline_transfer import (
    ParameterConfig,
    SplineTransferOperator,
    SplineTransferParams,
)

from ..dependencies import get_repository
from ..repository import BankRepository
from ..schemas import (
    SplineTransferConfig,
    SplineTransferParamsIn,
    SplineTransferResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["operators"])

RepoDep = Annotated[BankRepository, Depends(get_repository)]


@router.post("/spline-transfer", response_model=SplineTransferResponse)
async def spline_transfer_endpoint(
    body: SplineTransferParamsIn,
    repo: RepoDep,
    bank_id: str = Query(..., description="Target bank id (also the default source bank)"),
) -> SplineTransferResponse:
    target_bank = await repo.load(bank_id)
    if target_bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    # Resolve the source bank + note
    source_bank_id = body.source_bank_id or bank_id
    if source_bank_id == bank_id:
        source_bank = target_bank
    else:
        source_bank = await repo.load(source_bank_id)
        if source_bank is None:
            raise HTTPException(
                status.HTTP_404_NOT_FOUND, f"source bank {source_bank_id!r} not found",
            )
    source_note = source_bank.get_note(*body.source_note_id)
    if source_note is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"source note {body.source_note_id} not in bank {source_bank_id!r}",
        )

    # Validate target note existence up-front so clients get a 404 instead of
    # a silent "skipped" warning in the diagnostics.
    missing_targets = [
        t for t in body.target_note_ids if target_bank.get_note(*t) is None
    ]
    if missing_targets:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"target notes not in bank {bank_id!r}: {missing_targets}",
        )

    # Build domain-level ParameterConfig tuple
    if body.parameter_configs:
        configs = tuple(
            ParameterConfig(
                parameter=c.parameter,
                mode=c.mode,
                preserve_fundamental=c.preserve_fundamental,
                clamp_to_bounds=c.clamp_to_bounds,
                source_smoothing=c.source_smoothing,
            )
            for c in body.parameter_configs
        )
    else:
        configs = ()

    params = SplineTransferParams(
        source_bank_id=source_bank_id,
        source_note_id=body.source_note_id,
        target_note_ids=tuple(tuple(t) for t in body.target_note_ids),
        parameter_configs=configs,
        legacy_parameter=body.legacy_parameter,
        legacy_mode=body.legacy_mode,
        commit=body.commit,
        random_seed=body.random_seed,
    )

    op = SplineTransferOperator()
    try:
        result = op.apply_with_source(target_bank, params, source_note=source_note)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    new_bank_id: str | None = None
    parent_id: str | None = None
    if body.commit and result.bank is not target_bank:
        await repo.save(result.bank)
        new_bank_id = result.bank.id
        parent_id = result.bank.parent_id

    # Echo back the normalised configs so the UI sees which params/modes actually ran.
    resolved = params.resolved_configs()
    echo_configs = [
        SplineTransferConfig(
            parameter=c.parameter,
            mode=c.mode,
            preserve_fundamental=c.preserve_fundamental,
            clamp_to_bounds=c.clamp_to_bounds,
            source_smoothing=c.source_smoothing,
        )
        for c in resolved
    ]

    logger.info(
        "api.spline_transfer",
        extra={
            "bank_id": bank_id,
            "source_bank_id": source_bank_id,
            "commit": body.commit,
            "new_bank_id": new_bank_id,
            "n_targets": len(body.target_note_ids),
            "n_parameters": len(echo_configs),
        },
    )

    return SplineTransferResponse(
        new_bank_id=new_bank_id,
        parent_id=parent_id,
        source_bank_id=source_bank_id,
        source_note_id=body.source_note_id,
        target_note_ids=[tuple(t) for t in body.target_note_ids],
        parameter_configs=echo_configs,
        warnings=list(result.diagnostics.warnings),
    )
