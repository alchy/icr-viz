"""Tests for YAML settings + /api/settings router."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest
import yaml
from httpx import ASGITransport

from piano_web import settings as settings_module
from piano_web.db import init_schema
from piano_web.main import create_app


@pytest.fixture
def settings_file(tmp_path: Path, monkeypatch):
    path = tmp_path / "icr-viz-settings.yaml"
    monkeypatch.setenv("ICR_VIZ_SETTINGS", str(path))
    yield path


# ---- settings module --------------------------------------------------

def test_load_returns_defaults_when_file_missing(settings_file):
    data = settings_module.load()
    assert data["icr_path"] is None
    assert data["bank_dir"] is None
    assert data["midi"]["default_core"] == "active"
    assert data["midi"]["input"] is None
    assert data["midi"]["output"] is None
    assert data["engine"]["ir_file"] is None
    assert data["engine"]["config_file"] is None


def test_save_persists_yaml(settings_file):
    settings_module.save({"icr_path": r"C:\icr.exe"})
    raw = settings_file.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert parsed["icr_path"] == r"C:\icr.exe"


def test_save_deep_merges_nested_dicts(settings_file):
    settings_module.save({"midi": {"input": "loopMIDI Port 0"}})
    settings_module.save({"midi": {"output": "loopMIDI Port 1"}})
    data = settings_module.load()
    assert data["midi"]["input"] == "loopMIDI Port 0"
    assert data["midi"]["output"] == "loopMIDI Port 1"
    assert data["midi"]["default_core"] == "active"    # default survives


def test_save_deep_merges_engine_block(settings_file):
    settings_module.save({"engine": {"ir_file": "D:/ir/hall.wav"}})
    settings_module.save({"engine": {"config_file": "D:/icr-config.json"}})
    data = settings_module.load()
    assert data["engine"]["ir_file"] == "D:/ir/hall.wav"
    assert data["engine"]["config_file"] == "D:/icr-config.json"


def test_legacy_json_migrates_to_yaml(tmp_path: Path, monkeypatch):
    yaml_path = tmp_path / "icr-viz-settings.yaml"
    json_path = tmp_path / "icr-viz-settings.json"
    json_path.write_text(json.dumps({"icr_path": r"C:\legacy.exe"}), encoding="utf-8")
    monkeypatch.setenv("ICR_VIZ_SETTINGS", str(yaml_path))

    data = settings_module.load()
    assert data["icr_path"] == r"C:\legacy.exe"
    # The migration should have written the YAML file
    assert yaml_path.exists()


def test_save_returns_full_merged_dict(settings_file):
    merged = settings_module.save({"bank_dir": "D:/banks"})
    assert merged["bank_dir"] == "D:/banks"
    assert merged["icr_path"] is None     # default preserved


# ---- schema migration -------------------------------------------------

def test_migrate_bank_dirs_list_to_bank_dir(settings_file: Path):
    # Seed an old-format file the user may still have on disk.
    settings_file.write_text(yaml.safe_dump({
        "bank_dirs": ["D:/banks", "D:/extra"],
    }), encoding="utf-8")
    data = settings_module.load()
    assert data["bank_dir"] == "D:/banks"     # first entry wins
    assert "bank_dirs" not in data
    # Rewritten on disk — next read is clean.
    on_disk = yaml.safe_load(settings_file.read_text(encoding="utf-8"))
    assert "bank_dirs" not in on_disk
    assert on_disk["bank_dir"] == "D:/banks"


def test_migrate_soundbank_dir_wins_over_bank_dirs(settings_file: Path):
    settings_file.write_text(yaml.safe_dump({
        "soundbank_dir": "D:/primary",
        "bank_dirs": ["D:/fallback"],
    }), encoding="utf-8")
    data = settings_module.load()
    assert data["bank_dir"] == "D:/primary"


def test_migrate_engine_flags_to_namespace(settings_file: Path):
    settings_file.write_text(yaml.safe_dump({
        "ir_file": "D:/ir/hall.wav",
        "ir_dir": "D:/ir",
        "engine_config_file": "D:/icr-config.json",
        "engine_config_dir": "D:/engine",
        "core_config_file": "D:/core.json",
    }), encoding="utf-8")
    data = settings_module.load()
    assert data["engine"]["ir_file"] == "D:/ir/hall.wav"
    assert data["engine"]["ir_dir"] == "D:/ir"
    assert data["engine"]["config_file"] == "D:/icr-config.json"
    assert data["engine"]["config_dir"] == "D:/engine"
    assert data["engine"]["core_config_file"] == "D:/core.json"
    for legacy in ("ir_file", "ir_dir", "engine_config_file",
                   "engine_config_dir", "core_config_file"):
        assert legacy not in data


def test_migrate_midi_default_keys(settings_file: Path):
    settings_file.write_text(yaml.safe_dump({
        "midi": {
            "default_input": "loopMIDI Port 0",
            "default_output": "loopMIDI Port 1",
            "default_core": "additive",
        },
    }), encoding="utf-8")
    data = settings_module.load()
    assert data["midi"]["input"] == "loopMIDI Port 0"
    assert data["midi"]["output"] == "loopMIDI Port 1"
    assert data["midi"]["default_core"] == "additive"
    assert "default_input" not in data["midi"]


def test_migrate_is_idempotent(settings_file: Path):
    settings_file.write_text(yaml.safe_dump({
        "bank_dir": "D:/banks",
        "engine": {"ir_file": "D:/ir/hall.wav"},
        "midi": {"input": "loopMIDI Port 0"},
    }), encoding="utf-8")
    before = settings_file.read_text(encoding="utf-8")
    settings_module.load()
    after = settings_file.read_text(encoding="utf-8")
    # Clean file: no rewrite expected (migration produced no change).
    assert before == after


# ---- /api/settings --------------------------------------------------

@pytest.fixture
async def api_client(tmp_path: Path, monkeypatch):
    settings_path = tmp_path / "icr-viz-settings.yaml"
    db = tmp_path / "s.sqlite"
    monkeypatch.setenv("ICR_VIZ_SETTINGS", str(settings_path))
    await init_schema(db)
    app = create_app()
    transport = ASGITransport(app=app)
    async with httpx.AsyncClient(transport=transport, base_url="http://testserver") as c:
        yield c


async def test_get_settings_returns_defaults_on_empty(api_client):
    r = await api_client.get("/api/settings")
    assert r.status_code == 200
    body = r.json()
    assert body["icr_path"] is None
    assert body["bank_dir"] is None
    assert body["engine"]["ir_file"] is None
    assert body["midi"]["input"] is None


async def test_post_settings_persists_update(api_client):
    r = await api_client.post(
        "/api/settings",
        json={"icr_path": "/usr/local/bin/icr", "bank_dir": "/data/banks"},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["icr_path"] == "/usr/local/bin/icr"
    assert body["bank_dir"] == "/data/banks"

    # Subsequent GET returns the same values
    r = await api_client.get("/api/settings")
    assert r.json()["icr_path"] == "/usr/local/bin/icr"


async def test_post_settings_deep_merges(api_client):
    await api_client.post("/api/settings", json={"midi": {"input": "A"}})
    await api_client.post("/api/settings", json={"midi": {"output": "B"}})
    r = await api_client.get("/api/settings")
    body = r.json()
    assert body["midi"]["input"] == "A"
    assert body["midi"]["output"] == "B"


async def test_get_settings_schema_returns_defaults(api_client):
    r = await api_client.get("/api/settings/schema")
    assert r.status_code == 200
    body = r.json()
    assert "icr_path" in body
    assert "midi" in body
    assert "engine" in body
    assert "bank_dir" in body
