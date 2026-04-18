"""Tests for AnchorRepository — CRUD, cascade, patch semantics."""

from __future__ import annotations

from pathlib import Path

import pytest

from piano_core.models.anchor import Anchor
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial

from piano_web.anchor_repository import AnchorRepository
from piano_web.db import init_schema, open_connection
from piano_web.repository import BankRepository


@pytest.fixture
async def repos(tmp_path: Path):
    db = tmp_path / "anchors.sqlite"
    await init_schema(db)
    bank_repo = BankRepository(db)
    anchor_repo = AnchorRepository(db)
    # Seed a parent bank so anchors can FK-reference it
    partial = Partial(
        k=1, f_hz=440.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.99,
    )
    note = Note(midi=60, vel=5, f0_hz=261.6, B=0.0, partials=(partial,))
    bank = Bank(id="parent_bank", notes=(note,))
    await bank_repo.save(bank)
    return bank_repo, anchor_repo, db


def _anchor(
    *, midi: int = 60, velocity: int = 5, k: int = 1,
    parameter: str = "tau1", value: float = 0.4, weight: float = 0.5,
) -> Anchor:
    return Anchor(
        midi=midi, velocity=velocity, k=k,
        parameter=parameter, value=value, weight=weight,
    )


# ---- CRUD ----------------------------------------------------------------

async def test_save_and_list_for_bank(repos):
    _, anchor_repo, _ = repos
    a = _anchor()
    await anchor_repo.save(a, bank_id="parent_bank")
    rows = await anchor_repo.list_for_bank("parent_bank")
    assert len(rows) == 1
    assert rows[0] == a


async def test_list_for_note_filters_midi_velocity(repos):
    _, anchor_repo, _ = repos
    a1 = _anchor(midi=60, velocity=5, k=1)
    a2 = _anchor(midi=61, velocity=5, k=1)
    await anchor_repo.save(a1, bank_id="parent_bank")
    await anchor_repo.save(a2, bank_id="parent_bank")
    assert await anchor_repo.list_for_note("parent_bank", 60, 5) == [a1]
    assert await anchor_repo.list_for_note("parent_bank", 61, 5) == [a2]


async def test_get_by_id(repos):
    _, anchor_repo, _ = repos
    a = _anchor()
    await anchor_repo.save(a, bank_id="parent_bank")
    assert (await anchor_repo.get(a.id)) == a
    assert (await anchor_repo.get("missing")) is None


async def test_patch_changes_mutable_fields(repos):
    _, anchor_repo, _ = repos
    a = _anchor(value=0.5, weight=0.3)
    await anchor_repo.save(a, bank_id="parent_bank")
    updated = await anchor_repo.patch(a.id, value=0.9, weight=0.8, note="adjusted")
    assert updated is True
    roundtripped = await anchor_repo.get(a.id)
    assert roundtripped.value == pytest.approx(0.9)
    assert roundtripped.weight == pytest.approx(0.8)
    assert roundtripped.note == "adjusted"


async def test_patch_rejects_out_of_range_weight(repos):
    _, anchor_repo, _ = repos
    a = _anchor()
    await anchor_repo.save(a, bank_id="parent_bank")
    with pytest.raises(ValueError):
        await anchor_repo.patch(a.id, weight=1.5)


async def test_patch_no_fields_returns_false(repos):
    _, anchor_repo, _ = repos
    a = _anchor()
    await anchor_repo.save(a, bank_id="parent_bank")
    result = await anchor_repo.patch(a.id)
    assert result is False


async def test_delete(repos):
    _, anchor_repo, _ = repos
    a = _anchor()
    await anchor_repo.save(a, bank_id="parent_bank")
    deleted = await anchor_repo.delete(a.id)
    assert deleted is True
    assert (await anchor_repo.delete(a.id)) is False
    assert (await anchor_repo.get(a.id)) is None


async def test_duplicate_id_rejected(repos):
    _, anchor_repo, _ = repos
    a = _anchor()
    await anchor_repo.save(a, bank_id="parent_bank")
    with pytest.raises(ValueError, match="already exists"):
        await anchor_repo.save(a, bank_id="parent_bank")


async def test_dangling_bank_id_rejected(repos):
    _, anchor_repo, _ = repos
    a = _anchor()
    with pytest.raises(ValueError, match="bank_id"):
        await anchor_repo.save(a, bank_id="non_existent_bank")


# ---- cascade -------------------------------------------------------------

async def test_deleting_bank_cascades_to_anchors(repos):
    bank_repo, anchor_repo, _db = repos
    await anchor_repo.save(_anchor(), bank_id="parent_bank")
    await anchor_repo.save(_anchor(k=2), bank_id="parent_bank")
    # Enable foreign key cascade — connection-level pragma already on via db.open_connection
    await bank_repo.delete("parent_bank")
    rows = await anchor_repo.list_for_bank("parent_bank")
    assert rows == []


# ---- bulk ---------------------------------------------------------------

async def test_save_many_all_or_nothing(repos):
    _, anchor_repo, _ = repos
    good_batch = [
        _anchor(k=1, parameter="tau1"),
        _anchor(k=2, parameter="tau1"),
    ]
    await anchor_repo.save_many(good_batch, bank_id="parent_bank")
    rows = await anchor_repo.list_for_bank("parent_bank")
    assert {r.k for r in rows} == {1, 2}

    # Second batch contains a dup id — entire insert rolled back
    duplicate = [
        _anchor(k=3, parameter="tau1"),
        Anchor(
            id=good_batch[0].id,   # collision
            midi=60, velocity=5, k=5, parameter="tau1", value=0.2,
        ),
    ]
    with pytest.raises(Exception):
        await anchor_repo.save_many(duplicate, bank_id="parent_bank")
    rows = await anchor_repo.list_for_bank("parent_bank")
    assert len(rows) == 2   # unchanged
