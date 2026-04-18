"""Integration tests for i3 endpoints — tone-identify, tone-correct, deviation-report."""

from __future__ import annotations

from pathlib import Path

import httpx
import numpy as np
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


def _note(
    *, midi: int = 60, vel: int = 5, rng_seed: int = 0,
    noise_scale: float = 0.0,
    override_k: int | None = None, override_tau1: float | None = None,
) -> Note:
    rng = np.random.default_rng(rng_seed)
    partials = []
    for k in range(1, 11):
        tau = 0.5 * k ** (-0.7)
        if noise_scale > 0:
            tau *= 1.0 + rng.normal(0, noise_scale)
        if override_k is not None and k == override_k and override_tau1 is not None:
            tau = override_tau1
        partials.append(Partial(
            k=k, f_hz=100.0 * k, A0=10.0 * k ** (-0.5),
            tau1=max(tau, 1e-6), tau2=5.0 * max(tau, 1e-6), a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=100.0, B=0.0, partials=tuple(partials))


@pytest.fixture
async def client_and_db(tmp_path: Path):
    db = tmp_path / "i3.sqlite"
    await init_schema(db)
    repo = BankRepository(db)
    anchor_repo = AnchorRepository(db)
    dependencies.set_repository(repo)
    dependencies.set_anchor_repository(anchor_repo)

    # Two clean reference banks + a target with a seeded anomaly.
    target = _note(override_k=5, override_tau1=3.0)
    clean_a = _note(rng_seed=1)
    clean_b = _note(rng_seed=2)
    await repo.save(Bank(id="target", notes=(target,), metadata={"instrument_name": "Target"}))
    await repo.save(Bank(id="clean_a", notes=(clean_a,), metadata={"instrument_name": "Clean A"}))
    await repo.save(Bank(id="clean_b", notes=(clean_b,), metadata={"instrument_name": "Clean B"}))

    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=30.0) as c:
        yield c, repo
    dependencies.set_repository(BankRepository())
    dependencies.set_anchor_repository(AnchorRepository())


# ---- tone-identify-only (Phase A) ----------------------------------------

async def test_tone_identify_only_returns_summary(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/tone-identify-only?bank_id=target",
        json={
            "target_note_id": [60, 5],
            "reference_bank_ids": ["clean_a", "clean_b"],
            "parameters": ["tau1", "A0"],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["target_note_id"] == [60, 5]
    assert "target" in body["reference_bank_ids"] and "clean_a" in body["reference_bank_ids"]
    summary = body["reference_summary"]
    assert "tau1" in summary["coverage"]
    assert "A0" in summary["quality"]


async def test_tone_identify_only_404_on_missing_bank(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/tone-identify-only?bank_id=nope",
        json={"target_note_id": [60, 5]},
    )
    assert r.status_code == 404


# ---- tone-identify-and-correct -----------------------------------------

async def test_tone_correct_preview_does_not_persist(client_and_db):
    client, repo = client_and_db
    r = await client.post(
        "/api/ops/tone-identify-and-correct?bank_id=target",
        json={
            "target_note_id": [60, 5],
            "reference_bank_ids": ["clean_a", "clean_b"],
            "parameters": ["tau1"],
            "preserve_fundamental": False,
            "commit": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_bank_id"] is None   # preview
    assert body["parent_id"] is None
    assert body["n_changed"] >= 1
    # Log includes entries for every k-parameter pair
    assert len(body["per_partial_log"]) >= 10


async def test_tone_correct_commit_persists_new_bank(client_and_db):
    client, repo = client_and_db
    r = await client.post(
        "/api/ops/tone-identify-and-correct?bank_id=target",
        json={
            "target_note_id": [60, 5],
            "reference_bank_ids": ["clean_a", "clean_b"],
            "parameters": ["tau1"],
            "preserve_fundamental": False,
            "commit": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_bank_id"] is not None
    assert body["parent_id"] == "target"
    # DB has the new bank
    new_bank = await repo.load(body["new_bank_id"])
    assert new_bank is not None
    p5 = next(p for p in new_bank.get_note(60, 5).partials if p.k == 5)
    # tau1 was pulled away from 3.0 toward the clean baseline ~0.15
    assert p5.tau1 < 1.0


async def test_tone_correct_404_on_missing_note(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/tone-identify-and-correct?bank_id=target",
        json={
            "target_note_id": [99, 0],
            "reference_bank_ids": ["clean_a"],
        },
    )
    assert r.status_code == 404


async def test_tone_correct_422_when_insufficient_sources_and_error_mode(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/tone-identify-and-correct?bank_id=target",
        json={
            "target_note_id": [60, 5],
            "reference_bank_ids": [],                  # no extra refs — only the target itself
            "min_sources_for_consensus": 5,
            "fallback_on_insufficient": "error",
        },
    )
    assert r.status_code == 422


# ---- deviation-report ---------------------------------------------------

async def test_deviation_report_finds_seeded_anomaly(client_and_db):
    client, _ = client_and_db
    r = await client.get(
        "/api/banks/target/deviation-report",
        params=[("ref", "clean_a"), ("ref", "clean_b"), ("min_z", 2.0), ("parameters", "tau1")],
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["n_entries"] >= 1
    assert body["target_bank_id"] == "target"
    # The injected anomaly is at (60, 5, k=5)
    assert any(
        e["midi"] == 60 and e["velocity"] == 5 and e["k"] == 5 and e["parameter"] == "tau1"
        for e in body["entries"]
    )


async def test_deviation_report_empty_for_clean_target(client_and_db):
    client, repo = client_and_db
    # Replace target bank with clean data
    clean_target = Bank(
        id="clean_target",
        notes=(_note(rng_seed=99),),
        metadata={"instrument_name": "Clean Target"},
    )
    await repo.save(clean_target)

    r = await client.get(
        "/api/banks/clean_target/deviation-report",
        params=[("ref", "clean_a"), ("ref", "clean_b"), ("min_z", 3.0)],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["n_entries"] == 0


async def test_deviation_report_loo_flag_when_target_in_references(client_and_db):
    client, _ = client_and_db
    r = await client.get(
        "/api/banks/target/deviation-report",
        params=[("ref", "target"), ("ref", "clean_a"), ("ref", "clean_b")],
    )
    assert r.status_code == 200
    body = r.json()
    assert body["loo"] is True


async def test_deviation_report_404_on_missing_bank(client_and_db):
    client, _ = client_and_db
    r = await client.get(
        "/api/banks/no-such/deviation-report",
        params=[("ref", "clean_a")],
    )
    assert r.status_code == 404
