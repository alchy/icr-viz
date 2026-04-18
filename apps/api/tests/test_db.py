"""Tests for piano_web.db — schema DDL, PRAGMAs, generated column behaviour."""

from __future__ import annotations

import json
from pathlib import Path

import aiosqlite
import pytest

from piano_web.db import (
    SCHEMA_SQL,
    bootstrap_in_memory,
    get_db_path,
    init_schema,
    open_connection,
)


# ---- DB path resolution ---------------------------------------------------

def test_get_db_path_respects_env_override(monkeypatch, tmp_path: Path):
    custom = tmp_path / "custom.sqlite"
    monkeypatch.setenv("ICR_VIZ_DB", str(custom))
    assert get_db_path() == custom.resolve()


def test_get_db_path_defaults_under_repo_root(monkeypatch):
    monkeypatch.delenv("ICR_VIZ_DB", raising=False)
    p = get_db_path()
    assert p.name == "icr-viz.sqlite"
    assert p.parent.name == "data"


# ---- Schema creation ------------------------------------------------------

async def test_init_schema_creates_banks_table(tmp_path: Path):
    db = tmp_path / "test.sqlite"
    await init_schema(db)
    assert db.exists()

    async with open_connection(db) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name='banks';"
        )
        row = await cur.fetchone()
        assert row is not None
        assert row["name"] == "banks"


async def test_init_schema_is_idempotent(tmp_path: Path):
    db = tmp_path / "repeat.sqlite"
    await init_schema(db)
    await init_schema(db)  # must not raise
    async with open_connection(db) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name;"
        )
        rows = await cur.fetchall()
    # i1 created `banks`; i2 added `anchors`.
    assert sorted(r["name"] for r in rows) == ["anchors", "banks"]


async def test_idx_banks_instrument_exists(tmp_path: Path):
    db = tmp_path / "idx.sqlite"
    await init_schema(db)
    async with open_connection(db) as conn:
        cur = await conn.execute(
            "SELECT name FROM sqlite_master WHERE type='index' AND name='idx_banks_instrument';"
        )
        row = await cur.fetchone()
        assert row is not None


# ---- PRAGMAs --------------------------------------------------------------

async def test_connection_enables_foreign_keys(tmp_path: Path):
    db = tmp_path / "fk.sqlite"
    await init_schema(db)
    async with open_connection(db) as conn:
        cur = await conn.execute("PRAGMA foreign_keys;")
        row = await cur.fetchone()
        assert row[0] == 1


async def test_connection_uses_wal_journal(tmp_path: Path):
    db = tmp_path / "wal.sqlite"
    await init_schema(db)
    async with open_connection(db) as conn:
        cur = await conn.execute("PRAGMA journal_mode;")
        row = await cur.fetchone()
        assert row[0] == "wal"


# ---- Generated column behaviour ------------------------------------------

async def test_generated_instrument_column_extracts_metadata():
    """STORED generated column must auto-compute from data_json on insert."""
    conn = await bootstrap_in_memory()
    try:
        payload = {
            "icr_version": 2,
            "metadata": {"instrument_name": "AS Black Grand", "k_max": 60},
            "notes": {},
        }
        await conn.execute(
            "INSERT INTO banks (id, data_json) VALUES (?, ?);",
            ("bank_abc", json.dumps(payload)),
        )
        await conn.commit()

        cur = await conn.execute("SELECT id, instrument FROM banks WHERE id = ?;", ("bank_abc",))
        row = await cur.fetchone()
        assert row["id"] == "bank_abc"
        assert row["instrument"] == "AS Black Grand"
    finally:
        await conn.close()


async def test_generated_instrument_column_is_null_when_absent():
    conn = await bootstrap_in_memory()
    try:
        payload = {"icr_version": 2, "metadata": {}, "notes": {}}
        await conn.execute(
            "INSERT INTO banks (id, data_json) VALUES (?, ?);",
            ("no_meta", json.dumps(payload)),
        )
        await conn.commit()
        cur = await conn.execute("SELECT instrument FROM banks WHERE id = ?;", ("no_meta",))
        row = await cur.fetchone()
        assert row["instrument"] is None
    finally:
        await conn.close()


# ---- Foreign key behaviour -----------------------------------------------

async def test_parent_id_rejects_dangling_reference():
    """With PRAGMA foreign_keys = ON, inserting a parent_id that doesn't exist must fail."""
    conn = await bootstrap_in_memory()
    try:
        payload = json.dumps({"icr_version": 2, "metadata": {}, "notes": {}})
        with pytest.raises(aiosqlite.IntegrityError):
            await conn.execute(
                "INSERT INTO banks (id, data_json, parent_id) VALUES (?, ?, ?);",
                ("child", payload, "nonexistent_parent"),
            )
            await conn.commit()
    finally:
        await conn.close()


async def test_parent_id_allows_valid_chain():
    conn = await bootstrap_in_memory()
    try:
        empty_payload = json.dumps({"icr_version": 2, "metadata": {}, "notes": {}})
        await conn.execute(
            "INSERT INTO banks (id, data_json) VALUES (?, ?);",
            ("root", empty_payload),
        )
        await conn.execute(
            "INSERT INTO banks (id, data_json, parent_id) VALUES (?, ?, ?);",
            ("child", empty_payload, "root"),
        )
        await conn.commit()

        cur = await conn.execute(
            "SELECT id, parent_id FROM banks ORDER BY id;"
        )
        rows = await cur.fetchall()
        assert [(r["id"], r["parent_id"]) for r in rows] == [
            ("child", "root"),
            ("root", None),
        ]
    finally:
        await conn.close()


# ---- DDL text inspection --------------------------------------------------

def test_schema_sql_contains_generated_column_clause():
    assert "GENERATED ALWAYS AS" in SCHEMA_SQL
    assert "STORED" in SCHEMA_SQL
    assert "idx_banks_instrument" in SCHEMA_SQL


# ---- created_at default ---------------------------------------------------

async def test_created_at_default_is_iso8601():
    conn = await bootstrap_in_memory()
    try:
        payload = json.dumps({"icr_version": 2, "metadata": {}, "notes": {}})
        await conn.execute("INSERT INTO banks (id, data_json) VALUES (?, ?);", ("t", payload))
        await conn.commit()
        cur = await conn.execute("SELECT created_at FROM banks WHERE id='t';")
        row = await cur.fetchone()
        # strftime('%Y-%m-%dT%H:%M:%fZ','now') -> e.g. "2026-04-18T17:46:00.123Z"
        ts = row["created_at"]
        assert "T" in ts and ts.endswith("Z")
        assert len(ts) >= 20
    finally:
        await conn.close()
