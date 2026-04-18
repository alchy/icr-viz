"""SQLite database layer for piano_web.

Single-file database following the schema in `idea/i1.md` §5:

    CREATE TABLE IF NOT EXISTS banks (
        id          TEXT PRIMARY KEY,
        data_json   TEXT NOT NULL,
        parent_id   TEXT REFERENCES banks(id),
        created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
        instrument  TEXT GENERATED ALWAYS AS
                    (json_extract(data_json, '$.metadata.instrument_name')) STORED
    );
    CREATE INDEX IF NOT EXISTS idx_banks_instrument ON banks (instrument);

Notes on SQLite specifics:
  - `PRAGMA foreign_keys = ON` must be set per-connection (off by default).
  - `PRAGMA journal_mode = WAL` for better concurrent read/write; set once per db.
  - Generated STORED columns require SQLite 3.31+ (Jan 2020). Modern Python 3.11+
    ships with compatible SQLite.
  - Timestamps: stored as ISO 8601 TEXT (UTC). Use `strftime` default, never `DATETIME`.
"""

from __future__ import annotations

import logging
import os
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import AsyncIterator

import aiosqlite


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# DB path resolution
# ---------------------------------------------------------------------------

DEFAULT_DB_FILENAME = "icr-viz.sqlite"


def get_db_path() -> Path:
    """Return the SQLite file path.

    Honors ``ICR_VIZ_DB`` env var for tests / alternate deployments.
    Defaults to ``<repo_root>/data/icr-viz.sqlite``.
    """
    override = os.environ.get("ICR_VIZ_DB")
    if override:
        return Path(override).resolve()
    repo_root = Path(__file__).resolve().parents[3]
    return (repo_root / "data" / DEFAULT_DB_FILENAME).resolve()


# ---------------------------------------------------------------------------
# Schema DDL
# ---------------------------------------------------------------------------

# Kept as a module-level constant so tests can assert on it and future Alembic
# migrations can use it as a baseline snapshot.
SCHEMA_SQL: str = """
CREATE TABLE IF NOT EXISTS banks (
    id          TEXT PRIMARY KEY,
    data_json   TEXT NOT NULL,
    parent_id   TEXT REFERENCES banks(id),
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    instrument  TEXT GENERATED ALWAYS AS
                (NULLIF(json_extract(data_json, '$.metadata.instrument_name'), '')) STORED
);
CREATE INDEX IF NOT EXISTS idx_banks_instrument ON banks (instrument);

-- i2: anchors table
-- One row per override point. Kept outside the bank JSONB blob so we can
-- query cross-bank (needed in i3 for anchor propagation across notes).
-- ON DELETE CASCADE: removing a bank also removes its anchors — they have no
-- meaning without the bank context.
CREATE TABLE IF NOT EXISTS anchors (
    id          TEXT PRIMARY KEY,
    bank_id     TEXT NOT NULL REFERENCES banks(id) ON DELETE CASCADE,
    midi        INTEGER NOT NULL,
    velocity    INTEGER NOT NULL,
    k           INTEGER NOT NULL,
    parameter   TEXT NOT NULL,
    value       REAL NOT NULL,
    weight      REAL NOT NULL CHECK (weight BETWEEN 0 AND 1),
    origin      TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    created_by  TEXT NOT NULL DEFAULT 'system',
    note        TEXT NOT NULL DEFAULT ''
);
CREATE INDEX IF NOT EXISTS idx_anchors_bank_note ON anchors (bank_id, midi, velocity);
CREATE INDEX IF NOT EXISTS idx_anchors_bank_param ON anchors (bank_id, parameter);
"""


# ---------------------------------------------------------------------------
# Per-connection pragmas (run every time aiosqlite opens a connection).
# ---------------------------------------------------------------------------

async def _apply_connection_pragmas(conn: aiosqlite.Connection) -> None:
    await conn.execute("PRAGMA foreign_keys = ON;")
    # journal_mode is a database-level setting but setting per-connection is a no-op
    # after the first time, and lets fresh DB files pick it up transparently.
    await conn.execute("PRAGMA journal_mode = WAL;")
    await conn.execute("PRAGMA synchronous = NORMAL;")  # WAL + NORMAL is safe for this workload


async def init_schema(db_path: Path | str | None = None) -> None:
    """Create the schema on disk if missing.

    Safe to call on every app startup — all DDL uses ``IF NOT EXISTS``.
    """
    target = Path(db_path) if db_path is not None else get_db_path()
    target.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    async with aiosqlite.connect(target) as conn:
        await _apply_connection_pragmas(conn)
        await conn.executescript(SCHEMA_SQL)
        await conn.commit()
    logger.info(
        "db.init_schema",
        extra={"db_path": str(target), "duration_ms": round((time.perf_counter() - t0) * 1000, 2)},
    )


@asynccontextmanager
async def open_connection(
    db_path: Path | str | None = None,
) -> AsyncIterator[aiosqlite.Connection]:
    """Async context manager yielding a configured aiosqlite connection.

    Uses ``row_factory = aiosqlite.Row`` so results are dict-like.
    """
    target = Path(db_path) if db_path is not None else get_db_path()
    async with aiosqlite.connect(target) as conn:
        conn.row_factory = aiosqlite.Row
        await _apply_connection_pragmas(conn)
        yield conn


# ---------------------------------------------------------------------------
# Helpers for tests
# ---------------------------------------------------------------------------

async def bootstrap_in_memory() -> aiosqlite.Connection:
    """Create an in-memory DB with schema applied — convenient for unit tests.

    Returns a persistent connection (not context-managed) — caller closes it.
    """
    conn = await aiosqlite.connect(":memory:")
    conn.row_factory = aiosqlite.Row
    await _apply_connection_pragmas(conn)
    await conn.executescript(SCHEMA_SQL)
    await conn.commit()
    return conn
