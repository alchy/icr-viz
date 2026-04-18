"""Integration tests for i5 endpoints — integrity, export, surface."""

from __future__ import annotations

import json
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


def _note(midi: int = 60, *, vel: int = 5, with_extrapolated: bool = True) -> Note:
    partials = []
    for k in range(1, 6):
        partials.append(Partial(
            k=k, f_hz=100.0 * k, A0=10.0 * k ** (-0.5),
            tau1=0.5 * k ** (-0.7), tau2=5.0 * k ** (-0.7),
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
            origin="measured",
        ))
    if with_extrapolated:
        partials.append(Partial(
            k=6, f_hz=600.0, A0=0.1, tau1=0.1, tau2=1.0,
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.5,
            origin="extrapolated",
        ))
    return Note(midi=midi, vel=vel, f0_hz=100.0, B=5e-4, partials=tuple(partials))


@pytest.fixture
async def client_and_db(tmp_path: Path):
    db = tmp_path / "i5.sqlite"
    await init_schema(db)
    repo = BankRepository(db)
    anchor_repo = AnchorRepository(db)
    dependencies.set_repository(repo)
    dependencies.set_anchor_repository(anchor_repo)

    bank = Bank(
        id="demo",
        notes=(_note(60), _note(61)),
        metadata={"instrument_name": "Demo", "k_max": 6},
    )
    await repo.save(bank)

    # Also a bad bank for integrity tests
    bad_partials = list(_note(60).partials)
    bad_partials[2] = Partial(
        k=3, f_hz=300.0, A0=1.0, tau1=5.0, tau2=0.5,   # tau2 < tau1 = physical error
        a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.9,
    )
    bad_bank = Bank(
        id="bad",
        notes=(Note(midi=60, vel=5, f0_hz=100.0, B=5e-4, partials=tuple(bad_partials)),),
    )
    await repo.save(bad_bank)

    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=30.0) as c:
        yield c, repo
    dependencies.set_repository(BankRepository())
    dependencies.set_anchor_repository(AnchorRepository())


# ---- integrity --------------------------------------------------------

async def test_integrity_clean_bank_returns_ok(client_and_db):
    client, _ = client_and_db
    r = await client.post("/api/ops/bank-integrity-validate?bank_id=demo", json={})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bank_id"] == "demo"
    assert body["n_errors"] == 0


async def test_integrity_bad_bank_flags_physical_consistency(client_and_db):
    client, _ = client_and_db
    r = await client.post("/api/ops/bank-integrity-validate?bank_id=bad", json={})
    assert r.status_code == 200
    body = r.json()
    kinds = {i["kind"] for i in body["issues"]}
    assert "physical_consistency" in kinds
    assert body["n_errors"] >= 1
    assert body["ok"] is False


async def test_integrity_404_on_missing_bank(client_and_db):
    client, _ = client_and_db
    r = await client.post("/api/ops/bank-integrity-validate?bank_id=nope", json={})
    assert r.status_code == 404


# ---- export ---------------------------------------------------------

async def test_export_icr_returns_json(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/banks/demo/export?format=icr")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/json")
    body = json.loads(r.text)
    assert body["bank_id"] == "demo"
    assert "notes" in body


async def test_export_synth_csv(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/banks/demo/export?format=synth_csv")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("text/csv")
    lines = r.text.strip().splitlines()
    # Header + (6 partials × 2 notes) = 13 lines
    assert len(lines) == 13


async def test_export_synth_csv_exclude_extrapolated(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/banks/demo/export?format=synth_csv&exclude_extrapolated=true")
    assert r.status_code == 200
    lines = r.text.strip().splitlines()
    # Header + (5 measured × 2 notes) = 11 lines
    assert len(lines) == 11


async def test_export_ndjson_streams_notes(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/banks/demo/export?format=ndjson")
    assert r.status_code == 200
    assert r.headers["content-type"].startswith("application/x-ndjson")
    lines = r.text.strip().splitlines()
    # 1 header + 2 notes = 3 lines
    assert len(lines) == 3
    assert json.loads(lines[0])["bank_id"] == "demo"


async def test_export_404_on_missing_bank(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/banks/no-such/export")
    assert r.status_code == 404


# ---- surface --------------------------------------------------------

async def test_surface_returns_midi_k_grid(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/banks/demo/surface?parameter=tau1&velocity=5")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["parameter"] == "tau1"
    assert body["velocity"] == 5
    assert body["midi"] == [60, 61]
    assert body["k"] == [1, 2, 3, 4, 5, 6]
    assert len(body["z"]) == 2                # midi rows
    assert len(body["z"][0]) == 6             # k columns


async def test_surface_404_on_unknown_parameter(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/banks/demo/surface?parameter=bogus&velocity=5")
    assert r.status_code == 400


async def test_surface_diff_mode_subtracts_parent(client_and_db):
    """Calling difference_from with same bank should give all zeros."""
    client, _ = client_and_db
    r = await client.get(
        "/api/banks/demo/surface?parameter=tau1&velocity=5&difference_from=demo",
    )
    assert r.status_code == 200
    for row in r.json()["z"]:
        for val in row:
            if val is not None:
                assert val == pytest.approx(0.0)


async def test_surface_empty_when_velocity_absent(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/banks/demo/surface?parameter=tau1&velocity=9")
    assert r.status_code == 200
    body = r.json()
    assert body["midi"] == []
    assert body["z"] == []


async def test_surface_404_on_missing_bank(client_and_db):
    client, _ = client_and_db
    r = await client.get("/api/banks/nope/surface?parameter=tau1&velocity=5")
    assert r.status_code == 404


async def test_surface_color_by_fit_quality(client_and_db):
    client, _ = client_and_db
    r = await client.get(
        "/api/banks/demo/surface?parameter=tau1&velocity=5&color_by=fit_quality",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["color"] is not None
    # 5 measured partials have fit_quality 0.95
    colors = [c for row in body["color"] for c in row if c is not None]
    assert all(0.0 <= c <= 1.0 for c in colors)


async def test_surface_color_by_origin_returns_labels(client_and_db):
    client, _ = client_and_db
    r = await client.get(
        "/api/banks/demo/surface?parameter=tau1&velocity=5&color_by=origin",
    )
    assert r.status_code == 200
    body = r.json()
    assert body["origin"] is not None
    origins = {o for row in body["origin"] for o in row if o is not None}
    assert "measured" in origins
    assert "extrapolated" in origins
