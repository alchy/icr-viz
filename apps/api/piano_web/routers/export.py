"""Export endpoint (i5.3).

GET /api/banks/{id}/export?format=icr|synth_csv|analysis_csv|ndjson&exclude_extrapolated=true

Produces a StreamingResponse for the CSV / ndjson cases so memory stays flat
on large banks. ICR format returns a plain JSON payload (already serialised
compactly by Bank.to_icr_dict).
"""

from __future__ import annotations

import json
import logging
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import Response, StreamingResponse

from piano_core.io.export import to_analysis_csv, to_ndjson, to_synth_csv

from ..dependencies import get_repository
from ..repository import BankRepository


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/banks", tags=["export"])

RepoDep = Annotated[BankRepository, Depends(get_repository)]


ExportFormat = Literal["icr", "synth_csv", "analysis_csv", "ndjson"]


@router.get("/{bank_id}/export")
async def export_endpoint(
    bank_id: str,
    repo: RepoDep,
    format: ExportFormat = Query("icr"),
    exclude_extrapolated: bool = Query(False),
) -> Response:
    bank = await repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    logger.info(
        "api.export",
        extra={
            "bank_id": bank_id, "format": format,
            "exclude_extrapolated": exclude_extrapolated,
            "n_notes": len(bank.notes),
        },
    )

    if format == "icr":
        payload = bank.to_icr_dict()
        if exclude_extrapolated:
            _filter_payload_partials(payload)
        body = json.dumps(payload, ensure_ascii=False, indent=2)
        return Response(
            content=body,
            media_type="application/json",
            headers={
                "Content-Disposition": f'attachment; filename="{bank_id}.icr.json"',
            },
        )

    if format == "synth_csv":
        text = to_synth_csv(bank, exclude_extrapolated=exclude_extrapolated)
        return Response(
            content=text,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{bank_id}-synth.csv"',
            },
        )

    if format == "analysis_csv":
        text = to_analysis_csv(bank, exclude_extrapolated=exclude_extrapolated)
        return Response(
            content=text,
            media_type="text/csv",
            headers={
                "Content-Disposition": f'attachment; filename="{bank_id}-analysis.csv"',
            },
        )

    if format == "ndjson":
        # Copy so the generator can be consumed by Starlette outside the closure.
        gen = to_ndjson(bank)
        return StreamingResponse(
            iter(gen),
            media_type="application/x-ndjson",
            headers={
                "Content-Disposition": f'attachment; filename="{bank_id}.icr.ndjson"',
            },
        )

    # Unreachable given Literal typing, but keeps the branch explicit.
    raise HTTPException(status.HTTP_400_BAD_REQUEST, f"unsupported format {format!r}")


def _filter_payload_partials(payload: dict) -> None:
    """In-place removal of extrapolated partials from an ICR-dict payload."""
    notes = payload.get("notes") or {}
    for key, note_obj in notes.items():
        partials = note_obj.get("partials") or []
        note_obj["partials"] = [
            p for p in partials
            if p.get("origin", "measured") != "extrapolated"
        ]
