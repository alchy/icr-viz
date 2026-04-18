"""Integration tests for math-analysis endpoints."""

from __future__ import annotations

import math
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
from piano_web.routers.math_analysis import _cache_clear_for_tests


def _note(midi: int, *, vel: int = 5, B: float = 5e-4) -> Note:
    partials = []
    for k in range(1, 14):
        f = k * 100.0 * math.sqrt(1.0 + B * k * k)
        partials.append(Partial(
            k=k, f_hz=f, A0=10.0 * k ** (-0.5),
            tau1=0.5 * k ** (-0.7), tau2=5.0 * k ** (-0.7),
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=100.0, B=B, partials=tuple(partials))


@pytest.fixture
async def client_and_bank(tmp_path: Path):
    _cache_clear_for_tests()
    db = tmp_path / "ma.sqlite"
    await init_schema(db)
    repo = BankRepository(db)
    anchor_repo = AnchorRepository(db)
    dependencies.set_repository(repo)
    dependencies.set_anchor_repository(anchor_repo)

    notes = tuple(_note(m, B=1e-4 * math.exp(0.05 * (m - 60))) for m in range(60, 75))
    bank = Bank(id="clean", notes=notes, metadata={"instrument_name": "Clean"})
    await repo.save(bank)

    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=30.0) as c:
        yield c, repo
    dependencies.set_repository(BankRepository())
    dependencies.set_anchor_repository(AnchorRepository())
    _cache_clear_for_tests()


# ---- /math-analysis --------------------------------------------------------

async def test_math_analysis_returns_full_report(client_and_bank):
    client, _ = client_and_bank
    r = await client.get("/api/banks/clean/math-analysis")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bank_id"] == "clean"
    assert body["n_notes"] == 15
    assert "per_note" in body
    assert "60_5" in body["per_note"]
    assert "inharmonicity_trend" in body
    assert "gamma_ratio_stats" in body


async def test_math_analysis_cache_hit_is_faster_on_second_call(client_and_bank):
    """The second call should come from cache — not observationally verified here
    (timings are flaky in CI), but we verify the returned report matches."""
    client, _ = client_and_bank
    r1 = await client.get("/api/banks/clean/math-analysis")
    r2 = await client.get("/api/banks/clean/math-analysis")
    assert r1.json()["generated_at"] == r2.json()["generated_at"]


async def test_math_analysis_404_on_missing_bank(client_and_bank):
    client, _ = client_and_bank
    r = await client.get("/api/banks/no-such/math-analysis")
    assert r.status_code == 404


# ---- /physical-fit --------------------------------------------------------

async def test_physical_fit_returns_single_diag(client_and_bank):
    client, _ = client_and_bank
    r = await client.get("/api/banks/clean/notes/60/5/physical-fit")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["midi"] == 60
    assert body["velocity"] == 5
    assert "B_hat" in body
    assert "tau1_alpha" in body


async def test_physical_fit_404_on_missing_note(client_and_bank):
    client, _ = client_and_bank
    r = await client.get("/api/banks/clean/notes/99/0/physical-fit")
    assert r.status_code == 404


async def test_physical_fit_404_on_missing_bank(client_and_bank):
    client, _ = client_and_bank
    r = await client.get("/api/banks/nope/notes/60/5/physical-fit")
    assert r.status_code == 404


# ---- /cross-note ----------------------------------------------------------

async def test_cross_note_returns_midi_value_series(client_and_bank):
    client, _ = client_and_bank
    r = await client.get("/api/banks/clean/cross-note/tau1/5/1")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["bank_id"] == "clean"
    assert body["parameter"] == "tau1"
    assert body["velocity"] == 5
    assert body["k"] == 1
    assert body["n"] == 15
    assert len(body["series"]) == 15
    assert body["series"][0]["midi"] == 60
    assert body["series"][-1]["midi"] == 74


async def test_cross_note_400_on_unknown_parameter(client_and_bank):
    client, _ = client_and_bank
    r = await client.get("/api/banks/clean/cross-note/nonsense/5/1")
    # The first note hit triggers the hasattr check
    assert r.status_code == 400


async def test_cross_note_empty_when_velocity_not_present(client_and_bank):
    client, _ = client_and_bank
    r = await client.get("/api/banks/clean/cross-note/tau1/9/1")
    # velocity=9 not in our test bank → empty series, no error
    assert r.status_code == 200
    assert r.json()["n"] == 0
