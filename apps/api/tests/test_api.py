"""Integration tests for the read-only HTTP API (i1 scope)."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest
from httpx import ASGITransport

from piano_core.io.icr import read_bank
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial

from piano_web import dependencies
from piano_web.db import init_schema
from piano_web.main import create_app
from piano_web.repository import BankRepository


REPO_ROOT = Path(__file__).resolve().parents[3]
IDEA_DIR = REPO_ROOT / "idea"


# ---- fixtures -------------------------------------------------------------

@pytest.fixture
async def api_client(tmp_path: Path):
    """Build an ASGI client wired to a tmp SQLite. Repository injected for test isolation."""
    db = tmp_path / "api.sqlite"
    await init_schema(db)
    repo = BankRepository(db, cache_size=3)
    dependencies.set_repository(repo)

    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as client:
        yield client, repo

    # Reset for next test
    dependencies.set_repository(BankRepository())  # points at default path; harmless in tests


def _demo_bank(bank_id: str, *, instrument: str = "Demo") -> Bank:
    partials = tuple(
        Partial(
            k=k, f_hz=261.6 * k, A0=1.0 / k, tau1=0.5, tau2=5.0, a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=0.95,
            sigma=0.02, origin="measured",
        )
        for k in range(1, 6)
    )
    note_60_5 = Note(
        midi=60, vel=5, f0_hz=261.6, B=0.0004, partials=partials,
        phi_diff=3.14, attack_tau=0.08, A_noise=0.3, noise_centroid_hz=1200.0,
        rms_gain=0.01,
    )
    note_61_5 = Note(midi=61, vel=5, f0_hz=277.18, B=0.0004, partials=partials)
    return Bank(
        id=bank_id,
        notes=(note_60_5, note_61_5),
        metadata={"instrument_name": instrument, "k_max": 60},
    )


# ---- meta endpoints -------------------------------------------------------

async def test_health_endpoint(api_client):
    client, _ = api_client
    r = await client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok"}


async def test_root_redirects_to_docs(api_client):
    client, _ = api_client
    r = await client.get("/", follow_redirects=False)
    assert r.status_code == 307
    assert r.headers["location"] == "/docs"


async def test_api_index_lists_routes(api_client):
    client, _ = api_client
    r = await client.get("/api")
    assert r.status_code == 200
    body = r.json()
    assert body["docs"] == "/docs"
    assert any("/api/banks" in route for route in body["routes"])


# ---- GET /api/banks -------------------------------------------------------

async def test_list_banks_empty_by_default(api_client):
    client, _ = api_client
    r = await client.get("/api/banks")
    assert r.status_code == 200
    assert r.json() == []


async def test_list_banks_returns_all_created(api_client):
    client, repo = api_client
    await repo.save(_demo_bank("a", instrument="Alpha"))
    await repo.save(_demo_bank("b", instrument="Beta"))

    r = await client.get("/api/banks")
    assert r.status_code == 200
    items = r.json()
    assert {item["id"] for item in items} == {"a", "b"}
    by_id = {item["id"]: item for item in items}
    assert by_id["a"]["instrument"] == "Alpha"
    assert by_id["b"]["instrument"] == "Beta"


# ---- GET /api/banks/:id ---------------------------------------------------

async def test_get_bank_detail(api_client):
    client, repo = api_client
    await repo.save(_demo_bank("d1", instrument="Detail"))

    r = await client.get("/api/banks/d1")
    assert r.status_code == 200
    detail = r.json()
    assert detail["id"] == "d1"
    assert detail["instrument"] == "Detail"
    assert detail["n_notes"] == 2
    assert detail["velocities"] == [5]
    assert detail["midi_range"] == [60, 61]
    assert detail["k_max"] == 60


async def test_get_bank_404_when_missing(api_client):
    client, _ = api_client
    r = await client.get("/api/banks/nonexistent")
    assert r.status_code == 404


# ---- GET /api/banks/:id/notes --------------------------------------------

async def test_list_notes_returns_midi_velocity_pairs(api_client):
    client, repo = api_client
    await repo.save(_demo_bank("n1"))
    r = await client.get("/api/banks/n1/notes")
    assert r.status_code == 200
    notes = r.json()
    assert {(n["midi"], n["velocity"]) for n in notes} == {(60, 5), (61, 5)}


# ---- GET /api/banks/:id/notes/:midi/:velocity ----------------------------

async def test_get_note_returns_partials_with_new_fields(api_client):
    """i1 spec §6.2 — NoteDetail must surface sigma and origin on partials."""
    client, repo = api_client
    await repo.save(_demo_bank("np"))
    r = await client.get("/api/banks/np/notes/60/5")
    assert r.status_code == 200
    detail = r.json()
    assert detail["midi"] == 60 and detail["velocity"] == 5
    assert len(detail["partials"]) == 5
    first = detail["partials"][0]
    # Storage fields present
    for field in ("k", "f_hz", "A0", "tau1", "tau2", "a1", "beat_hz", "phi", "fit_quality"):
        assert field in first
    # F-2 / F-3
    assert first["sigma"] == pytest.approx(0.02)
    assert first["origin"] == "measured"


async def test_get_note_404_when_note_missing(api_client):
    client, repo = api_client
    await repo.save(_demo_bank("np2"))
    r = await client.get("/api/banks/np2/notes/99/0")
    assert r.status_code == 404


async def test_get_note_404_when_bank_missing(api_client):
    client, _ = api_client
    r = await client.get("/api/banks/no-such-bank/notes/60/5")
    assert r.status_code == 404


# ---- GET /api/banks/:id/notes/:midi/:velocity/curves ---------------------

async def test_curves_payload_has_all_math_params_by_default(api_client):
    client, repo = api_client
    await repo.save(_demo_bank("cv"))
    r = await client.get("/api/banks/cv/notes/60/5/curves")
    assert r.status_code == 200
    payload = r.json()
    assert set(payload["parameters"]) == {"tau1", "tau2", "A0", "a1", "beat_hz", "f_coef"}
    # Each parameter should have one CurvePoint per partial (k=1..5)
    for param, points in payload["parameters"].items():
        assert len(points) == 5
        for pt in points:
            assert "k" in pt and "value" in pt
            assert "sigma" in pt and "fit_quality" in pt
            assert pt["origin"] == "measured"


async def test_curves_subset_via_query_param(api_client):
    client, repo = api_client
    await repo.save(_demo_bank("cv2"))
    r = await client.get(
        "/api/banks/cv2/notes/60/5/curves",
        params={"parameters": ["tau1", "A0"]},
    )
    assert r.status_code == 200
    params = r.json()["parameters"]
    assert set(params) == {"tau1", "A0"}


async def test_curves_unknown_param_rejected(api_client):
    client, repo = api_client
    await repo.save(_demo_bank("cv3"))
    r = await client.get(
        "/api/banks/cv3/notes/60/5/curves",
        params={"parameters": ["nonsense_param"]},
    )
    assert r.status_code == 400


async def test_f_coef_is_derived_not_stored(api_client):
    """f_coef = f_hz/(k*f0*sqrt(1+B*k^2)) - 1 should be computed, not taken from Partial."""
    client, repo = api_client
    await repo.save(_demo_bank("cv4"))
    r = await client.get(
        "/api/banks/cv4/notes/60/5/curves",
        params={"parameters": ["f_coef"]},
    )
    assert r.status_code == 200
    points = r.json()["parameters"]["f_coef"]
    # With f_hz = 261.6*k and f0 = 261.6, B=0.0004:
    # expected f_coef at k=2 = (261.6*2) / (2*261.6*sqrt(1 + 0.0004*4)) - 1 = 1/sqrt(1.0016) - 1
    import math
    expected_k2 = 1.0 / math.sqrt(1.0 + 0.0004 * 4) - 1.0
    pk2 = next(p for p in points if p["k"] == 2)
    assert pk2["value"] == pytest.approx(expected_k2, rel=1e-6)


# ---- integration with a real bank ----------------------------------------

async def test_end_to_end_with_real_bank(api_client):
    """Load a real reference bank, hit every endpoint through the HTTP stack."""
    client, repo = api_client
    src = IDEA_DIR / "ks-grand-2604161547-icr.json"
    if not src.exists():
        pytest.skip("reference bank not present")

    bank = read_bank(src)
    await repo.save(bank)

    # list
    r = await client.get("/api/banks")
    assert r.status_code == 200
    assert len(r.json()) == 1

    # detail
    r = await client.get(f"/api/banks/{bank.id}")
    assert r.status_code == 200
    assert r.json()["n_notes"] > 0

    # notes index
    r = await client.get(f"/api/banks/{bank.id}/notes")
    assert r.status_code == 200
    notes = r.json()
    assert len(notes) > 0

    # Pick a known note (m060_vel5 should exist in every 88-key bank)
    m, v = 60, 5
    r = await client.get(f"/api/banks/{bank.id}/notes/{m}/{v}")
    assert r.status_code == 200
    note_detail = r.json()
    assert note_detail["midi"] == m and note_detail["velocity"] == v
    assert len(note_detail["partials"]) > 0

    # curves
    r = await client.get(f"/api/banks/{bank.id}/notes/{m}/{v}/curves")
    assert r.status_code == 200
    curves = r.json()
    assert len(curves["parameters"]) == 6  # tau1, tau2, A0, a1, beat_hz, f_coef
    # f_coef should have len == len(partials) (all partials have valid f_hz)
    assert len(curves["parameters"]["f_coef"]) == len(note_detail["partials"])
