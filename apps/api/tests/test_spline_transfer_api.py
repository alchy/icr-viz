"""Integration tests for /api/ops/spline-transfer."""

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
    *, midi: int = 60, vel: int = 5,
    tau1_scale: float = 1.0, A0_scale: float = 1.0,
) -> Note:
    partials = []
    for k in range(1, 11):
        partials.append(Partial(
            k=k, f_hz=100.0 * k,
            A0=A0_scale * 10.0 * k ** (-0.5),
            tau1=tau1_scale * 0.5 * k ** (-0.7),
            tau2=tau1_scale * 5.0 * k ** (-0.7),
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=100.0, B=0.0, partials=tuple(partials))


@pytest.fixture
async def client_and_db(tmp_path: Path):
    db = tmp_path / "st.sqlite"
    await init_schema(db)
    repo = BankRepository(db)
    anchor_repo = AnchorRepository(db)
    dependencies.set_repository(repo)
    dependencies.set_anchor_repository(anchor_repo)

    # Source bank has a bright/long note at (60, 5)
    source = _note(midi=60, vel=5, tau1_scale=2.0, A0_scale=3.0)
    # Target bank has a plain note at (61, 5) — will receive the transferred shape.
    target = _note(midi=61, vel=5, tau1_scale=1.0, A0_scale=1.0)
    await repo.save(Bank(
        id="source_bank",
        notes=(source,),
        metadata={"instrument_name": "Source"},
    ))
    await repo.save(Bank(
        id="target_bank",
        notes=(target,),
        metadata={"instrument_name": "Target"},
    ))

    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=30.0) as c:
        yield c, repo
    dependencies.set_repository(BankRepository())
    dependencies.set_anchor_repository(AnchorRepository())


# ---- happy paths --------------------------------------------------------

async def test_spline_transfer_preview_returns_configs(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/spline-transfer?bank_id=target_bank",
        json={
            "source_bank_id": "source_bank",
            "source_note_id": [60, 5],
            "target_note_ids": [[61, 5]],
            "parameter_configs": [
                {"parameter": "tau1", "mode": "absolute"},
                {"parameter": "A0", "mode": "relative"},
            ],
            "commit": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_bank_id"] is None   # preview
    assert body["source_bank_id"] == "source_bank"
    assert len(body["parameter_configs"]) == 2
    assert {c["parameter"] for c in body["parameter_configs"]} == {"tau1", "A0"}


async def test_spline_transfer_commit_creates_new_bank(client_and_db):
    client, repo = client_and_db
    r = await client.post(
        "/api/ops/spline-transfer?bank_id=target_bank",
        json={
            "source_bank_id": "source_bank",
            "source_note_id": [60, 5],
            "target_note_ids": [[61, 5]],
            "parameter_configs": [{"parameter": "tau1", "mode": "absolute"}],
            "commit": True,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["new_bank_id"] is not None
    assert body["parent_id"] == "target_bank"
    new_bank = await repo.load(body["new_bank_id"])
    assert new_bank is not None
    # target's tau1 should now reflect the 2x-scaled source.
    n = new_bank.get_note(61, 5)
    p2 = next(p for p in n.partials if p.k == 2)
    assert p2.tau1 > 0.5    # default baseline was ~0.3; source-derived should be ~0.6
    assert p2.origin == "derived"


async def test_spline_transfer_legacy_single_parameter_api(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/spline-transfer?bank_id=target_bank",
        json={
            "source_bank_id": "source_bank",
            "source_note_id": [60, 5],
            "target_note_ids": [[61, 5]],
            "legacy_parameter": "tau1",
            "legacy_mode": "absolute",
            "commit": False,
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert len(body["parameter_configs"]) == 1
    assert body["parameter_configs"][0]["parameter"] == "tau1"


async def test_spline_transfer_self_source_when_source_bank_id_omitted(client_and_db):
    client, repo = client_and_db
    # Add a second note to target_bank so we can transfer within it
    notes = (_note(midi=60, vel=5, tau1_scale=2.0), _note(midi=61, vel=5))
    await repo.save(Bank(id="self_bank", notes=notes))
    r = await client.post(
        "/api/ops/spline-transfer?bank_id=self_bank",
        json={
            "source_note_id": [60, 5],
            "target_note_ids": [[61, 5]],
            "parameter_configs": [{"parameter": "tau1", "mode": "absolute"}],
        },
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["source_bank_id"] == "self_bank"


# ---- error paths --------------------------------------------------------

async def test_missing_target_bank_404(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/spline-transfer?bank_id=nope",
        json={
            "source_note_id": [60, 5],
            "target_note_ids": [[61, 5]],
            "parameter_configs": [{"parameter": "tau1", "mode": "absolute"}],
        },
    )
    assert r.status_code == 404


async def test_missing_source_note_404(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/spline-transfer?bank_id=target_bank",
        json={
            "source_bank_id": "source_bank",
            "source_note_id": [99, 0],
            "target_note_ids": [[61, 5]],
            "parameter_configs": [{"parameter": "tau1", "mode": "absolute"}],
        },
    )
    assert r.status_code == 404


async def test_missing_target_note_404(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/spline-transfer?bank_id=target_bank",
        json={
            "source_bank_id": "source_bank",
            "source_note_id": [60, 5],
            "target_note_ids": [[99, 0]],
            "parameter_configs": [{"parameter": "tau1", "mode": "absolute"}],
        },
    )
    assert r.status_code == 404


async def test_invalid_parameter_422(client_and_db):
    client, _ = client_and_db
    r = await client.post(
        "/api/ops/spline-transfer?bank_id=target_bank",
        json={
            "source_bank_id": "source_bank",
            "source_note_id": [60, 5],
            "target_note_ids": [[61, 5]],
            "parameter_configs": [{"parameter": "bogus", "mode": "absolute"}],
        },
    )
    assert r.status_code == 422
