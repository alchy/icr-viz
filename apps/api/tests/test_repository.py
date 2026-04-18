"""Tests for BankRepository — CRUD, immutable-chain discipline, LRU cache."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piano_core.io.icr import read_bank
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial

from piano_web.db import init_schema
from piano_web.repository import BankRepository


REPO_ROOT = Path(__file__).resolve().parents[3]
IDEA_DIR = REPO_ROOT / "idea"


# ---- fixtures -------------------------------------------------------------

@pytest.fixture
async def repo(tmp_path: Path) -> BankRepository:
    db = tmp_path / "test.sqlite"
    await init_schema(db)
    return BankRepository(db, cache_size=3)


def _make_bank(bank_id: str, *, parent_id: str | None = None, instrument: str = "Test") -> Bank:
    partial = Partial(
        k=1, f_hz=440.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.99,
    )
    note = Note(midi=60, vel=5, f0_hz=261.6, B=0.0, partials=(partial,))
    return Bank(
        id=bank_id,
        parent_id=parent_id,
        notes=(note,),
        metadata={"instrument_name": instrument, "k_max": 60},
    )


# ---- basic CRUD -----------------------------------------------------------

async def test_save_and_load_roundtrip(repo: BankRepository):
    bank = _make_bank("rt1")
    await repo.save(bank)

    loaded = await repo.load("rt1")
    assert loaded is not None
    assert loaded == bank


async def test_load_returns_none_when_absent(repo: BankRepository):
    assert await repo.load("nope") is None


async def test_exists_returns_true_after_save(repo: BankRepository):
    await repo.save(_make_bank("ex1"))
    assert await repo.exists("ex1") is True
    assert await repo.exists("other") is False


async def test_list_summaries_returns_saved_banks(repo: BankRepository):
    await repo.save(_make_bank("a", instrument="Alpha"))
    await repo.save(_make_bank("b", instrument="Beta"))

    rows = await repo.list_summaries()
    assert len(rows) == 2
    by_id = {r["id"]: r for r in rows}
    assert by_id["a"]["instrument"] == "Alpha"
    assert by_id["b"]["instrument"] == "Beta"
    # Both have created_at from STORED default
    assert all(r["created_at"] for r in rows)


async def test_delete_removes_row(repo: BankRepository):
    await repo.save(_make_bank("d1"))
    deleted = await repo.delete("d1")
    assert deleted is True
    assert await repo.load("d1") is None
    # Deleting again returns False
    assert await repo.delete("d1") is False


# ---- immutability --------------------------------------------------------

async def test_save_refuses_duplicate_id(repo: BankRepository):
    bank = _make_bank("dup")
    await repo.save(bank)
    with pytest.raises(ValueError, match="immutable"):
        await repo.save(_make_bank("dup", instrument="Changed"))


async def test_save_rejects_dangling_parent_id(repo: BankRepository):
    orphan = _make_bank("orphan", parent_id="does_not_exist")
    with pytest.raises(ValueError, match="parent_id"):
        await repo.save(orphan)


async def test_parent_child_chain_supported(repo: BankRepository):
    root = _make_bank("v1")
    await repo.save(root)
    child = _make_bank("v2", parent_id="v1", instrument="Changed")
    await repo.save(child)

    rows = await repo.list_summaries()
    ids = {r["id"]: r["parent_id"] for r in rows}
    assert ids["v1"] is None
    assert ids["v2"] == "v1"


# ---- cache behaviour -----------------------------------------------------

async def test_load_populates_cache(repo: BankRepository):
    await repo.save(_make_bank("c1"))
    assert repo.cache_size == 0  # save does not populate
    await repo.load("c1")
    assert repo.cache_size == 1


async def test_cache_hit_returns_same_instance(repo: BankRepository):
    await repo.save(_make_bank("c2"))
    first = await repo.load("c2")
    second = await repo.load("c2")
    assert first is second  # identity, not just equality — proves cache hit


async def test_cache_evicts_lru_when_over_capacity(repo: BankRepository):
    # cache_size = 3 (fixture)
    for i in range(5):
        await repo.save(_make_bank(f"b{i}"))
        await repo.load(f"b{i}")
    assert repo.cache_size == 3
    # Earliest (b0, b1) should be evicted; most recent (b2, b3, b4) retained
    assert await repo.load("b4") is not None  # no DB round trip — cached


async def test_save_invalidates_cache_for_same_id(repo: BankRepository):
    # Not strictly possible with immutable discipline, but delete+resave tests the path.
    await repo.save(_make_bank("inv"))
    _ = await repo.load("inv")
    assert repo.cache_size == 1
    await repo.delete("inv")
    assert repo.cache_size == 0


async def test_cache_can_be_disabled(tmp_path: Path):
    db = tmp_path / "no-cache.sqlite"
    await init_schema(db)
    r = BankRepository(db, cache_size=0)
    await r.save(_make_bank("nc"))
    await r.load("nc")
    assert r.cache_size == 0


async def test_clear_cache_wipes_entries(repo: BankRepository):
    await repo.save(_make_bank("x"))
    await repo.load("x")
    assert repo.cache_size == 1
    repo.clear_cache()
    assert repo.cache_size == 0


# ---- bulk ingest ---------------------------------------------------------

async def test_save_many_inserts_in_one_transaction(repo: BankRepository):
    banks = [_make_bank(f"bulk{i}", instrument=f"I{i}") for i in range(4)]
    await repo.save_many(banks)
    rows = await repo.list_summaries()
    assert len(rows) == 4


async def test_save_many_rollback_on_failure(repo: BankRepository):
    await repo.save(_make_bank("existing"))
    # Second entry in the batch collides with existing -> rollback must keep repo consistent
    batch = [_make_bank("new1"), _make_bank("existing"), _make_bank("new2")]
    with pytest.raises(Exception):
        await repo.save_many(batch)
    # Nothing from the batch should persist
    assert await repo.exists("new1") is False
    assert await repo.exists("new2") is False


# ---- integration with real bank JSON files ------------------------------

@pytest.mark.parametrize("bank_file", [
    "icr-bank-sample1.json",
    "ks-grand-2604161547-icr.json",
    "as-blackgrand-04162340-raw-icr.json",
])
async def test_real_bank_save_load_roundtrip(bank_file: str, tmp_path: Path):
    """Full round-trip: read JSON -> save to SQLite -> load back -> equal."""
    src = IDEA_DIR / bank_file
    if not src.exists():
        pytest.skip(f"{bank_file} not present")

    db = tmp_path / "real.sqlite"
    await init_schema(db)
    r = BankRepository(db, cache_size=2)

    bank = read_bank(src)
    await r.save(bank)

    loaded = await r.load(bank.id)
    assert loaded == bank

    # Summary row reflects metadata
    rows = await r.list_summaries()
    assert len(rows) == 1
    instrument = bank.metadata.get("instrument_name") or None
    assert rows[0]["instrument"] == instrument
