"""Anchor CRUD routes (i2 US-2.1 through US-2.4).

Every mutation produces a *new* Bank version — the immutable chain that
powers deterministic replay in i5. The router orchestrates:

  1. Load current Bank.
  2. Apply mutation via Bank.with_added_anchor / with_patched_anchor / with_removed_anchor.
  3. Persist new bank (new id, parent_id=old id).
  4. Mirror the anchor change into the anchors table so cross-bank queries
     stay fast (i3 propagation will rely on this).
  5. Return {new_bank_id, parent_id, anchor?}.

The "new bank per mutation" rule can feel heavy, but JSON payload serialization
is the bulk of cost — and i1 cache already avoids parse overhead on reads.
"""

from __future__ import annotations

import logging
import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from piano_core.models.anchor import Anchor

from ..anchor_repository import AnchorRepository
from ..dependencies import get_anchor_repository, get_repository
from ..repository import BankRepository
from ..schemas import (
    AnchorCreate,
    AnchorDetail,
    AnchorMutationResponse,
    AnchorPatch,
    anchor_detail_from_domain,
)


logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/banks", tags=["anchors"])


RepoDep = Annotated[BankRepository, Depends(get_repository)]
AnchorRepoDep = Annotated[AnchorRepository, Depends(get_anchor_repository)]


def _new_bank_id(parent_id: str) -> str:
    """Child bank id = parent id + short uuid suffix. Keeps lineage readable."""
    return f"{parent_id}.{uuid.uuid4().hex[:8]}"


# ---------------------------------------------------------------------------
# GET list
# ---------------------------------------------------------------------------

@router.get(
    "/{bank_id}/notes/{midi}/{velocity}/anchors",
    response_model=list[AnchorDetail],
)
async def list_anchors(
    bank_id: str,
    midi: int,
    velocity: int,
    bank_repo: RepoDep,
) -> list[AnchorDetail]:
    bank = await bank_repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")
    return [anchor_detail_from_domain(a) for a in bank.anchors_for_note(midi, velocity)]


# ---------------------------------------------------------------------------
# POST add
# ---------------------------------------------------------------------------

@router.post(
    "/{bank_id}/notes/{midi}/{velocity}/anchors",
    response_model=AnchorMutationResponse,
    status_code=status.HTTP_201_CREATED,
)
async def create_anchor(
    bank_id: str,
    midi: int,
    velocity: int,
    body: AnchorCreate,
    bank_repo: RepoDep,
    anchor_repo: AnchorRepoDep,
) -> AnchorMutationResponse:
    bank = await bank_repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    # Verify note exists before creating the anchor — keeps orphan anchors out.
    if bank.get_note(midi, velocity) is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND,
            f"note ({midi}, {velocity}) not in bank {bank_id!r}",
        )

    anchor = Anchor(
        midi=midi,
        velocity=velocity,
        k=body.k,
        parameter=body.parameter,
        value=body.value,
        weight=body.weight,
        origin=body.origin,
        created_by=body.created_by,
        note=body.note,
    )
    new_id = _new_bank_id(bank.id)
    new_bank = bank.with_added_anchor(anchor, new_id=new_id)

    # Anchors live in the bank JSON blob (Bank.anchors tuple) — authoritative in i2.
    # The `anchors` SQLite table is reserved for i3 cross-bank propagation queries
    # and is intentionally NOT kept in sync here to avoid unique-id collisions across
    # versions of the same anchor.
    await bank_repo.save(new_bank)

    logger.info(
        "api.anchor.create",
        extra={
            "bank_id": bank.id, "new_bank_id": new_bank.id,
            "midi": midi, "velocity": velocity,
            "k": anchor.k, "parameter": anchor.parameter,
        },
    )
    return AnchorMutationResponse(
        new_bank_id=new_bank.id,
        parent_id=new_bank.parent_id,
        anchor=anchor_detail_from_domain(anchor),
    )


# ---------------------------------------------------------------------------
# PATCH update weight/value/note
# ---------------------------------------------------------------------------

@router.patch(
    "/{bank_id}/anchors/{anchor_id}",
    response_model=AnchorMutationResponse,
)
async def patch_anchor(
    bank_id: str,
    anchor_id: str,
    body: AnchorPatch,
    bank_repo: RepoDep,
    anchor_repo: AnchorRepoDep,
) -> AnchorMutationResponse:
    bank = await bank_repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    existing = bank.anchor_by_id(anchor_id)
    if existing is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"anchor {anchor_id!r} not in bank {bank_id!r}")

    patched = existing.patched(value=body.value, weight=body.weight, note=body.note)
    new_id = _new_bank_id(bank.id)
    new_bank = bank.with_patched_anchor(patched, new_id=new_id)

    await bank_repo.save(new_bank)

    logger.info(
        "api.anchor.patch",
        extra={"bank_id": bank.id, "new_bank_id": new_bank.id, "anchor_id": anchor_id},
    )
    return AnchorMutationResponse(
        new_bank_id=new_bank.id,
        parent_id=new_bank.parent_id,
        anchor=anchor_detail_from_domain(patched),
    )


# ---------------------------------------------------------------------------
# DELETE
# ---------------------------------------------------------------------------

@router.delete(
    "/{bank_id}/anchors/{anchor_id}",
    response_model=AnchorMutationResponse,
)
async def delete_anchor(
    bank_id: str,
    anchor_id: str,
    bank_repo: RepoDep,
    anchor_repo: AnchorRepoDep,
) -> AnchorMutationResponse:
    bank = await bank_repo.load(bank_id)
    if bank is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"bank {bank_id!r} not found")

    if bank.anchor_by_id(anchor_id) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"anchor {anchor_id!r} not in bank {bank_id!r}")

    new_id = _new_bank_id(bank.id)
    new_bank = bank.with_removed_anchor(anchor_id, new_id=new_id)

    await bank_repo.save(new_bank)

    logger.info(
        "api.anchor.delete",
        extra={"bank_id": bank.id, "new_bank_id": new_bank.id, "anchor_id": anchor_id},
    )
    return AnchorMutationResponse(
        new_bank_id=new_bank.id,
        parent_id=new_bank.parent_id,
        anchor=None,
    )
