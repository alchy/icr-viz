"""Integrity-check endpoint (i5.3).

POST /api/ops/bank-integrity-validate?bank_id=<id>
  Runs the BankIntegrityOperator against the target bank and returns the
  diagnostic issue list. Because the operator never mutates, this is a pure
  read — no new bank is persisted.
"""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel

from piano_core.operators.bank_integrity import (
    BankIntegrityOperator,
    BankIntegrityParams,
)

from ..dependencies import get_repository
from ..repository import BankRepository


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/ops", tags=["operators"])

RepoDep = Annotated[BankRepository, Depends(get_repository)]


class BankIntegrityParamsIn(BaseModel):
    quality_floor: float = 0.3
    quality_floor_max_ratio: float = 0.3
    inharmonicity_min: float = 0.0
    inharmonicity_max: float = 1e-2
    expected_midi_range: tuple[int, int] | None = None
    expected_velocities: list[int] | None = None
    random_seed: int = 0


class BankIntegrityResponse(BaseModel):
    bank_id: str
    ok: bool
    n_issues: int
    n_errors: int
    n_warnings: int
    issues: list[dict]


@router.post("/bank-integrity-validate", response_model=BankIntegrityResponse)
async def bank_integrity_validate(
    body: BankIntegrityParamsIn,
    repo: RepoDep,
    bank_id: str = Query(..., description="Target bank id"),
) -> BankIntegrityResponse:
    bank = await repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    params = BankIntegrityParams(
        quality_floor=body.quality_floor,
        quality_floor_max_ratio=body.quality_floor_max_ratio,
        inharmonicity_min=body.inharmonicity_min,
        inharmonicity_max=body.inharmonicity_max,
        expected_midi_range=body.expected_midi_range,
        expected_velocities=tuple(body.expected_velocities) if body.expected_velocities else None,
        random_seed=body.random_seed,
    )
    op = BankIntegrityOperator()
    result = op.apply(bank, params)
    diag = result.diagnostics.as_dict()
    logger.info(
        "api.bank_integrity",
        extra={
            "bank_id": bank_id,
            "n_issues": diag["n_issues"],
            "n_errors": diag["n_errors"],
            "ok": diag["ok"],
        },
    )
    return BankIntegrityResponse(
        bank_id=bank_id,
        ok=diag["ok"],
        n_issues=diag["n_issues"],
        n_errors=diag["n_errors"],
        n_warnings=diag["n_warnings"],
        issues=diag["issues"],
    )
