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
    assert data["bank_dirs"] == []
    assert data["midi"]["default_core"] == "active"


def test_save_persists_yaml(settings_file):
    settings_module.save({"icr_path": r"C:\icr.exe"})
    raw = settings_file.read_text(encoding="utf-8")
    parsed = yaml.safe_load(raw)
    assert parsed["icr_path"] == r"C:\icr.exe"


def test_save_deep_merges_nested_dicts(settings_file):
    settings_module.save({"midi": {"default_input": 2}})
    settings_module.save({"midi": {"default_output": 4}})
    data = settings_module.load()
    assert data["midi"]["default_input"] == 2
    assert data["midi"]["default_output"] == 4
    assert data["midi"]["default_core"] == "active"     # default survives


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
    merged = settings_module.save({"bank_dirs": ["idea"]})
    assert merged["bank_dirs"] == ["idea"]
    assert merged["icr_path"] is None     # default preserved


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
    assert body["bank_dirs"] == []


async def test_post_settings_persists_update(api_client):
    r = await api_client.post(
        "/api/settings",
        json={"icr_path": "/usr/local/bin/icr", "bank_dirs": ["/data/banks"]},
    )
    assert r.status_code == 200
    body = r.json()
    assert body["icr_path"] == "/usr/local/bin/icr"
    assert body["bank_dirs"] == ["/data/banks"]

    # Subsequent GET returns the same values
    r = await api_client.get("/api/settings")
    assert r.json()["icr_path"] == "/usr/local/bin/icr"


async def test_post_settings_deep_merges(api_client):
    await api_client.post("/api/settings", json={"midi": {"default_input": 2}})
    await api_client.post("/api/settings", json={"midi": {"default_output": 5}})
    r = await api_client.get("/api/settings")
    body = r.json()
    assert body["midi"]["default_input"] == 2
    assert body["midi"]["default_output"] == 5


async def test_get_settings_schema_returns_defaults(api_client):
    r = await api_client.get("/api/settings/schema")
    assert r.status_code == 200
    body = r.json()
    assert "icr_path" in body
    assert "midi" in body
