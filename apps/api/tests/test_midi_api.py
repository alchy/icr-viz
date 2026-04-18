"""Integration tests for /api/midi/* — uses a fake MidiBridge that captures
sent bytes instead of touching a real MIDI port."""

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
from piano_web.midi_bridge import MidiBridgeStatus
from piano_web.repository import BankRepository
from piano_web.routers import midi as midi_router


class FakeMidiBridge:
    """Stub MidiBridge — no rtmidi, just records calls."""

    def __init__(self) -> None:
        self._input_open = False
        self._output_open = False
        self._input_port_name: str | None = None
        self._output_port_name: str | None = None
        self._last_pong_ts: float | None = None
        self.sent_note_on: list[tuple[int, int, int]] = []
        self.sent_note_off: list[tuple[int, int]] = []
        self.sent_sysex: list[bytes] = []
        self.pong_rtt: float | None = 0.005     # default 5 ms
        self.push_bank_calls: list[tuple[int, str]] = []

    @staticmethod
    def list_input_ports() -> list[str]:
        return ["TestInput A", "TestInput B"]

    @staticmethod
    def list_output_ports() -> list[str]:
        return ["TestOutput A"]

    def open(self, *, input_port_index: int | None, output_port_index: int | None) -> None:
        if input_port_index is not None:
            self._input_open = True
            self._input_port_name = f"TestInput [{input_port_index}]"
        if output_port_index is not None:
            self._output_open = True
            self._output_port_name = f"TestOutput [{output_port_index}]"

    def close(self) -> None:
        self._input_open = False
        self._output_open = False
        self._input_port_name = None
        self._output_port_name = None

    def status(self) -> MidiBridgeStatus:
        return MidiBridgeStatus(
            input_open=self._input_open,
            output_open=self._output_open,
            input_port_name=self._input_port_name,
            output_port_name=self._output_port_name,
            last_pong_ts=self._last_pong_ts,
        )

    def send_note_on(self, *, channel: int, midi: int, velocity: int) -> None:
        self.sent_note_on.append((channel, midi, velocity))

    def send_note_off(self, *, channel: int, midi: int, velocity: int = 0) -> None:
        self.sent_note_off.append((channel, midi))

    def send_sysex(self, message: bytes) -> None:
        self.sent_sysex.append(bytes(message))

    def ping(self, timeout_s: float = 0.5) -> float | None:
        return self.pong_rtt

    def push_bank(self, *, core_id: int, bank_json: str) -> int:
        self.push_bank_calls.append((core_id, bank_json))
        # Simulate 3-frame chunk split for any non-trivial bank
        return max(1, len(bank_json) // 200)


@pytest.fixture
async def client_bridge_repo(tmp_path: Path):
    db = tmp_path / "midi.sqlite"
    await init_schema(db)
    repo = BankRepository(db)
    anchor_repo = AnchorRepository(db)
    dependencies.set_repository(repo)
    dependencies.set_anchor_repository(anchor_repo)

    # Seed a small bank for push-bank tests
    note = Note(
        midi=60, vel=5, f0_hz=261.6, B=1e-4,
        partials=(Partial(
            k=1, f_hz=261.6, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=0.99,
        ),),
    )
    await repo.save(Bank(id="seed", notes=(note,), metadata={"instrument_name": "Seed"}))

    fake = FakeMidiBridge()
    midi_router.set_bridge(fake)

    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver", timeout=5.0) as c:
        yield c, fake, repo

    dependencies.set_repository(BankRepository())
    dependencies.set_anchor_repository(AnchorRepository())
    midi_router.set_bridge(None)


# ---- ports + status -----------------------------------------------------

async def test_list_ports_returns_fake_ports(client_bridge_repo):
    client, _, _ = client_bridge_repo
    r = await client.get("/api/midi/ports")
    assert r.status_code == 200
    body = r.json()
    assert "TestInput A" in body["input_ports"]
    assert "TestOutput A" in body["output_ports"]


async def test_status_reflects_open_ports(client_bridge_repo):
    client, bridge, _ = client_bridge_repo
    bridge.open(input_port_index=0, output_port_index=0)
    r = await client.get("/api/midi/status")
    body = r.json()
    assert body["input_open"] is True
    assert body["output_open"] is True


# ---- connect ------------------------------------------------------------

async def test_connect_opens_both_ports(client_bridge_repo):
    client, bridge, _ = client_bridge_repo
    r = await client.post(
        "/api/midi/connect",
        json={"input_port_index": 0, "output_port_index": 0},
    )
    assert r.status_code == 200
    assert bridge._input_open
    assert bridge._output_open


async def test_connect_rejects_empty_body(client_bridge_repo):
    client, _, _ = client_bridge_repo
    r = await client.post("/api/midi/connect", json={})
    assert r.status_code == 400


async def test_disconnect_closes_ports(client_bridge_repo):
    client, bridge, _ = client_bridge_repo
    bridge.open(input_port_index=0, output_port_index=0)
    r = await client.post("/api/midi/disconnect")
    assert r.status_code == 200
    assert not bridge._input_open
    assert not bridge._output_open


# ---- play-note ----------------------------------------------------------

async def test_play_note_sends_note_on(client_bridge_repo):
    client, bridge, _ = client_bridge_repo
    bridge.open(input_port_index=None, output_port_index=0)
    r = await client.post(
        "/api/midi/play-note",
        json={"midi": 60, "velocity": 100, "duration_ms": 50},
    )
    assert r.status_code == 200
    assert len(bridge.sent_note_on) == 1
    assert bridge.sent_note_on[0] == (0, 60, 100)

    # Give the background note-off task a moment to run
    import asyncio
    await asyncio.sleep(0.15)
    assert len(bridge.sent_note_off) == 1


async def test_play_note_409_without_output_port(client_bridge_repo):
    client, _, _ = client_bridge_repo
    r = await client.post(
        "/api/midi/play-note",
        json={"midi": 60, "velocity": 100},
    )
    assert r.status_code == 409


# ---- ping ---------------------------------------------------------------

async def test_ping_returns_rtt(client_bridge_repo):
    client, bridge, _ = client_bridge_repo
    bridge.open(input_port_index=None, output_port_index=0)
    bridge.pong_rtt = 0.012      # 12 ms
    r = await client.post("/api/midi/ping")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is True
    assert body["rtt_ms"] == pytest.approx(12.0, abs=0.5)


async def test_ping_timeout_reports_not_ok(client_bridge_repo):
    client, bridge, _ = client_bridge_repo
    bridge.open(input_port_index=None, output_port_index=0)
    bridge.pong_rtt = None
    r = await client.post("/api/midi/ping")
    assert r.status_code == 200
    body = r.json()
    assert body["ok"] is False
    assert body["rtt_ms"] is None


async def test_ping_409_without_output_port(client_bridge_repo):
    client, _, _ = client_bridge_repo
    r = await client.post("/api/midi/ping")
    assert r.status_code == 409


# ---- push-bank ----------------------------------------------------------

async def test_push_bank_sends_frames_and_reports_count(client_bridge_repo):
    client, bridge, _ = client_bridge_repo
    bridge.open(input_port_index=None, output_port_index=0)
    r = await client.post(
        "/api/midi/push-bank",
        json={"bank_id": "seed", "core": "additive"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["bank_id"] == "seed"
    assert body["core"] == "additive"
    assert body["n_frames"] >= 1
    # FakeMidiBridge.push_bank records one call per invocation
    assert len(bridge.push_bank_calls) == 1


async def test_push_bank_404_when_bank_missing(client_bridge_repo):
    client, bridge, _ = client_bridge_repo
    bridge.open(input_port_index=None, output_port_index=0)
    r = await client.post(
        "/api/midi/push-bank",
        json={"bank_id": "nope", "core": "additive"},
    )
    assert r.status_code == 404


async def test_push_bank_409_without_output_port(client_bridge_repo):
    client, _, _ = client_bridge_repo
    r = await client.post(
        "/api/midi/push-bank",
        json={"bank_id": "seed", "core": "additive"},
    )
    assert r.status_code == 409
