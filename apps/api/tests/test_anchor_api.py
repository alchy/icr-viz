"""End-to-end tests for anchor CRUD + anchor-interpolate op."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial

from piano_web import dependencies
from piano_web.anchor_repository import AnchorRepository
from piano_web.db import init_schema
from piano_web.main import create_app
from piano_web.repository import BankRepository


@pytest.fixture
async def client_and_bank(tmp_path: Path):
    db = tmp_path / "anchor_api.sqlite"
    await init_schema(db)
    repo = BankRepository(db)
    anchor_repo = AnchorRepository(db)
    dependencies.set_repository(repo)
    dependencies.set_anchor_repository(anchor_repo)

    # Seed a bank with a well-populated note (many partials for the pipeline)
    partials = tuple(
        Partial(
            k=k, f_hz=261.6 * k, A0=10.0 * k ** (-0.5),
            tau1=0.5 * k ** (-0.7), tau2=5.0 * k ** (-0.7), a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=0.95,
        )
        for k in range(1, 11)
    )
    note = Note(midi=60, vel=5, f0_hz=261.6, B=1e-4, partials=partials)
    bank = Bank(id="seed", notes=(note,), metadata={"instrument_name": "Test", "k_max": 60})
    await repo.save(bank)

    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c, repo
    dependencies.set_repository(BankRepository())
    dependencies.set_anchor_repository(AnchorRepository())


# ---- CRUD happy path -----------------------------------------------------

async def test_list_anchors_empty_by_default(client_and_bank):
    client, _ = client_and_bank
    r = await client.get("/api/banks/seed/notes/60/5/anchors")
    assert r.status_code == 200
    assert r.json() == []


async def test_create_anchor_returns_new_bank_id(client_and_bank):
    client, _ = client_and_bank
    r = await client.post(
        "/api/banks/seed/notes/60/5/anchors",
        json={"k": 3, "parameter": "tau1", "value": 0.4, "weight": 0.5},
    )
    assert r.status_code == 201
    body = r.json()
    assert body["new_bank_id"].startswith("seed.")
    assert body["parent_id"] == "seed"
    assert body["anchor"]["k"] == 3
    assert body["anchor"]["parameter"] == "tau1"


async def test_list_anchors_reflects_new_bank(client_and_bank):
    client, _ = client_and_bank
    r = await client.post(
        "/api/banks/seed/notes/60/5/anchors",
        json={"k": 3, "parameter": "tau1", "value": 0.4},
    )
    new_id = r.json()["new_bank_id"]
    r = await client.get(f"/api/banks/{new_id}/notes/60/5/anchors")
    assert r.status_code == 200
    rows = r.json()
    assert len(rows) == 1
    assert rows[0]["k"] == 3


async def test_patch_anchor_updates_fields(client_and_bank):
    client, _ = client_and_bank
    created = await client.post(
        "/api/banks/seed/notes/60/5/anchors",
        json={"k": 4, "parameter": "A0", "value": 1.0, "weight": 0.3},
    )
    new_id = created.json()["new_bank_id"]
    anchor_id = created.json()["anchor"]["id"]
    r = await client.patch(
        f"/api/banks/{new_id}/anchors/{anchor_id}",
        json={"value": 2.5, "weight": 0.9, "note": "updated"},
    )
    assert r.status_code == 200
    patched = r.json()
    assert patched["anchor"]["value"] == pytest.approx(2.5)
    assert patched["anchor"]["weight"] == pytest.approx(0.9)
    assert patched["anchor"]["note"] == "updated"
    assert patched["parent_id"] == new_id


async def test_delete_anchor_removes_from_bank(client_and_bank):
    client, _ = client_and_bank
    created = await client.post(
        "/api/banks/seed/notes/60/5/anchors",
        json={"k": 5, "parameter": "tau2", "value": 3.0},
    )
    new_id = created.json()["new_bank_id"]
    anchor_id = created.json()["anchor"]["id"]
    r = await client.delete(f"/api/banks/{new_id}/anchors/{anchor_id}")
    assert r.status_code == 200
    body = r.json()
    assert body["parent_id"] == new_id
    listing = await client.get(f"/api/banks/{body['new_bank_id']}/notes/60/5/anchors")
    assert listing.json() == []


# ---- error paths ---------------------------------------------------------

async def test_create_anchor_404_when_note_missing(client_and_bank):
    client, _ = client_and_bank
    r = await client.post(
        "/api/banks/seed/notes/99/0/anchors",
        json={"k": 1, "parameter": "tau1", "value": 0.5},
    )
    assert r.status_code == 404


async def test_patch_anchor_404_on_unknown_anchor(client_and_bank):
    client, _ = client_and_bank
    r = await client.patch(
        "/api/banks/seed/anchors/does-not-exist",
        json={"value": 1.0},
    )
    assert r.status_code == 404


async def test_create_anchor_422_on_invalid_parameter(client_and_bank):
    client, _ = client_and_bank
    r = await client.post(
        "/api/banks/seed/notes/60/5/anchors",
        json={"k": 1, "parameter": "bogus", "value": 0.5},
    )
    assert r.status_code == 422


async def test_create_anchor_422_on_weight_outside_range(client_and_bank):
    client, _ = client_and_bank
    r = await client.post(
        "/api/banks/seed/notes/60/5/anchors",
        json={"k": 1, "parameter": "tau1", "value": 0.5, "weight": 2.0},
    )
    assert r.status_code == 422


# ---- anchor-interpolate preview -----------------------------------------

async def test_anchor_interpolate_preview_returns_curves(client_and_bank):
    client, _ = client_and_bank
    body = {
        "target_note_ids": [[60, 5]],
        "parameters": ["tau1", "A0"],
        "prior_weight": 0.3,
        "commit": False,
        "random_seed": 7,
    }
    r = await client.post("/api/ops/anchor-interpolate?bank_id=seed", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    # Preview must not persist
    assert payload["new_bank_id"] is None
    assert payload["parent_id"] is None
    # Two parameter diagnostics
    assert len(payload["per_parameter"]) == 2
    for diag in payload["per_parameter"]:
        assert diag["midi"] == 60
        assert diag["velocity"] == 5
        assert diag["parameter"] in ("tau1", "A0")
        assert len(diag["k_grid"]) == len(diag["values"]) == len(diag["sigmas"]) > 0


async def test_anchor_interpolate_commit_creates_new_bank(client_and_bank):
    client, repo = client_and_bank
    body = {
        "target_note_ids": [[60, 5]],
        "parameters": ["tau1"],
        "prior_weight": 0.3,
        "commit": True,
        "random_seed": 0,
    }
    r = await client.post("/api/ops/anchor-interpolate?bank_id=seed", json=body)
    assert r.status_code == 200, r.text
    payload = r.json()
    assert payload["new_bank_id"] is not None
    assert payload["parent_id"] == "seed"

    # Target note on new bank should have partials with origin="derived"
    new_bank = await repo.load(payload["new_bank_id"])
    note = new_bank.get_note(60, 5)
    changed = [p for p in note.partials if p.origin == "derived"]
    assert len(changed) > 0, "commit should mark touched partials as origin=derived"


async def test_anchor_interpolate_preview_reads_existing_anchors(client_and_bank):
    client, _ = client_and_bank
    # 1. Create an anchor that pulls tau1 hard at k=5
    created = await client.post(
        "/api/banks/seed/notes/60/5/anchors",
        json={"k": 5, "parameter": "tau1", "value": 10.0, "weight": 1.0},
    )
    new_id = created.json()["new_bank_id"]

    # 2. Preview against the post-anchor bank
    body = {
        "target_note_ids": [[60, 5]],
        "parameters": ["tau1"],
        "commit": False,
    }
    r = await client.post(f"/api/ops/anchor-interpolate?bank_id={new_id}", json=body)
    diag = r.json()["per_parameter"][0]
    # The curve should be pulled up toward the anchor value at k=5 (index depends on coverage)
    idx5 = diag["k_grid"].index(5)
    assert diag["values"][idx5] > 1.0


async def test_anchor_interpolate_404_on_unknown_bank(client_and_bank):
    client, _ = client_and_bank
    r = await client.post(
        "/api/ops/anchor-interpolate?bank_id=nope",
        json={"target_note_ids": [[60, 5]], "parameters": ["tau1"]},
    )
    assert r.status_code == 404


async def test_anchor_interpolate_422_on_invalid_parameter(client_and_bank):
    client, _ = client_and_bank
    r = await client.post(
        "/api/ops/anchor-interpolate?bank_id=seed",
        json={"target_note_ids": [[60, 5]], "parameters": ["bogus"]},
    )
    assert r.status_code == 422
