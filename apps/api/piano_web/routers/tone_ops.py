"""i3 endpoints — ToneIdentifyAndCorrect (identify-only + full) + DeviationReport."""

from __future__ import annotations

import logging
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from piano_core.completion.deviation_report import (
    ReferenceBankSample,
    deviation_report,
)
from piano_core.models.bank import Bank
from piano_core.operators.tone_identify_and_correct import (
    Source,
    ToneCorrectionParams,
    ToneIdentifyAndCorrectOperator,
    identify_tone,
)

from ..dependencies import get_repository
from ..repository import BankRepository
from ..schemas import (
    DeviationEntryOut,
    DeviationReportResponse,
    ToneCorrectParamsIn,
    ToneCorrectResponse,
    ToneIdentifyOnlyResponse,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["operators"])

RepoDep = Annotated[BankRepository, Depends(get_repository)]


async def _load_sources(
    *,
    repo: BankRepository,
    target_bank: Bank,
    reference_bank_ids: list[str],
    midi: int,
    velocity: int,
    use_anchors: bool,
) -> list[Source]:
    """Load target+references and build Source bundles for the math layer.

    Any reference bank that doesn't exist or doesn't carry the target note is
    skipped with a warning-only behaviour: the math layer filters them out
    again, and the operator already has `fallback_on_insufficient` semantics.
    """
    bundles: list[Source] = []

    target_note = target_bank.get_note(midi, velocity)
    if target_note is not None:
        bundles.append(Source(
            bank_id=target_bank.id,
            note=target_note,
            anchors=target_bank.anchors_for_note(midi, velocity) if use_anchors else (),
        ))

    for ref_id in reference_bank_ids:
        if ref_id == target_bank.id:
            continue
        ref_bank = await repo.load(ref_id)
        if ref_bank is None:
            continue
        ref_note = ref_bank.get_note(midi, velocity)
        if ref_note is None:
            continue
        bundles.append(Source(
            bank_id=ref_bank.id,
            note=ref_note,
            anchors=ref_bank.anchors_for_note(midi, velocity) if use_anchors else (),
        ))

    return bundles


# ---------------------------------------------------------------------------
# POST /api/ops/tone-identify-only
# ---------------------------------------------------------------------------

@router.post("/ops/tone-identify-only", response_model=ToneIdentifyOnlyResponse)
async def tone_identify_only(
    body: ToneCorrectParamsIn,
    repo: RepoDep,
    bank_id: str = Query(..., description="Target bank id"),
) -> ToneIdentifyOnlyResponse:
    """Run Phase A only — return the TonalReference summary without mutating anything."""
    target = await repo.load(bank_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")
    midi, velocity = body.target_note_id
    sources = await _load_sources(
        repo=repo,
        target_bank=target,
        reference_bank_ids=body.reference_bank_ids,
        midi=midi,
        velocity=velocity,
        use_anchors=body.use_anchors,
    )
    ref = identify_tone(
        midi=midi,
        velocity=velocity,
        sources=sources,
        parameters=body.parameters,
        use_physical_prior=body.use_physical_prior,
        random_seed=body.random_seed,
    )
    logger.info(
        "api.tone_identify_only",
        extra={
            "bank_id": bank_id,
            "target": list(body.target_note_id),
            "n_sources": len(sources),
        },
    )
    return ToneIdentifyOnlyResponse(
        target_note_id=body.target_note_id,
        reference_bank_ids=[s.bank_id for s in sources],
        reference_summary=ref.as_summary_dict(),
    )


# ---------------------------------------------------------------------------
# POST /api/ops/tone-identify-and-correct
# ---------------------------------------------------------------------------

@router.post("/ops/tone-identify-and-correct", response_model=ToneCorrectResponse)
async def tone_identify_and_correct(
    body: ToneCorrectParamsIn,
    repo: RepoDep,
    bank_id: str = Query(..., description="Target bank id"),
) -> ToneCorrectResponse:
    """Run Phase A + Phase B + Phase C. When ``commit=true`` the new bank
    is persisted and ``new_bank_id`` is returned; otherwise a preview."""
    target = await repo.load(bank_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")
    midi, velocity = body.target_note_id
    if target.get_note(midi, velocity) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"note ({midi}, {velocity}) not in bank {bank_id!r}",
        )

    sources = await _load_sources(
        repo=repo,
        target_bank=target,
        reference_bank_ids=body.reference_bank_ids,
        midi=midi,
        velocity=velocity,
        use_anchors=body.use_anchors,
    )

    params = ToneCorrectionParams(
        target_note_id=body.target_note_id,
        reference_bank_ids=tuple(body.reference_bank_ids),
        parameters=tuple(body.parameters),
        use_anchors=body.use_anchors,
        use_physical_prior=body.use_physical_prior,
        preserve_fundamental=body.preserve_fundamental,
        noise_threshold_d=body.noise_threshold_d,
        correction_threshold_d=body.correction_threshold_d,
        fill_quality_threshold=body.fill_quality_threshold,
        fallback_on_insufficient=body.fallback_on_insufficient,
        min_sources_for_consensus=body.min_sources_for_consensus,
        random_seed=body.random_seed,
    )
    op = ToneIdentifyAndCorrectOperator()
    try:
        result = op.apply_with_sources(target, params, sources=sources)
    except ValueError as exc:
        raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, str(exc)) from exc

    new_bank_id: str | None = None
    parent_id: str | None = None
    if body.commit and result.bank is not target:
        await repo.save(result.bank)
        new_bank_id = result.bank.id
        parent_id = result.bank.parent_id
    # Preview path uses the in-memory child result.bank without persistence.

    diag_dict = result.diagnostics.as_dict()
    logger.info(
        "api.tone_identify_and_correct",
        extra={
            "bank_id": bank_id,
            "new_bank_id": new_bank_id,
            "target": list(body.target_note_id),
            "commit": body.commit,
            "n_changed": diag_dict.get("n_changed", 0),
        },
    )

    return ToneCorrectResponse(
        new_bank_id=new_bank_id,
        parent_id=parent_id,
        target_note_id=body.target_note_id,
        reference_bank_ids=[s.bank_id for s in sources],
        reference_summary=diag_dict.get("reference_summary") or {},
        per_partial_log=list(diag_dict.get("per_partial_log", [])),
        n_changed=diag_dict.get("n_changed", 0),
        n_filled=diag_dict.get("n_filled", 0),
        n_unchanged=diag_dict.get("n_unchanged", 0),
        warnings=list(diag_dict.get("warnings", [])),
    )


# ---------------------------------------------------------------------------
# GET /api/banks/:id/deviation-report
# ---------------------------------------------------------------------------

@router.get(
    "/banks/{bank_id}/deviation-report",
    response_model=DeviationReportResponse,
)
async def deviation_report_endpoint(
    bank_id: str,
    repo: RepoDep,
    ref: list[str] = Query(..., description="Reference bank ids (repeat key to pass many)"),
    min_z: float = Query(2.0, ge=0.0),
    parameters: list[str] | None = Query(None),
    notes: list[str] | None = Query(
        None,
        description="Optional filter '{midi}_{vel}' entries; if set, only these notes are scanned.",
    ),
) -> DeviationReportResponse:
    target = await repo.load(bank_id)
    if target is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    # Load reference banks, tolerating missing ones (cross-bank queries are lenient).
    reference_samples: list[ReferenceBankSample] = []
    for r_id in ref:
        r_bank = await repo.load(r_id)
        if r_bank is None:
            continue
        note_by_key = {n.id: n for n in r_bank.notes}
        anchors_by_key: dict[tuple[int, int], tuple] = {}
        for a in r_bank.anchors:
            anchors_by_key.setdefault((a.midi, a.velocity), ())
            anchors_by_key[(a.midi, a.velocity)] = anchors_by_key[(a.midi, a.velocity)] + (a,)
        reference_samples.append(ReferenceBankSample(
            bank_id=r_bank.id,
            note_by_key=note_by_key,
            anchors_by_key=anchors_by_key,
        ))

    note_filter = None
    if notes is not None:
        try:
            note_filter = [tuple(int(x) for x in n.split("_")) for n in notes]
        except Exception as exc:
            raise HTTPException(
                status.HTTP_422_UNPROCESSABLE_ENTITY,
                f"invalid notes filter: {exc}",
            ) from exc

    target_note_by_key = {n.id: n for n in target.notes}
    report = deviation_report(
        target_bank_id=target.id,
        target_notes=target_note_by_key,
        references=reference_samples,
        parameters=parameters,
        min_z=min_z,
        note_filter=note_filter,
    )
    logger.info(
        "api.deviation_report",
        extra={
            "bank_id": bank_id,
            "n_refs": len(reference_samples),
            "n_entries": len(report.entries),
            "min_z": min_z,
        },
    )

    return DeviationReportResponse(
        target_bank_id=report.target_bank_id,
        reference_bank_ids=list(report.reference_bank_ids),
        loo=report.loo,
        min_z=report.min_z,
        parameters=list(report.parameters),
        entries=[DeviationEntryOut(**e.as_dict()) for e in report.entries],
        n_entries=len(report.entries),
    )
