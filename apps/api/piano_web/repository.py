"""BankRepository — async SQLite-backed persistence for Bank objects.

Loads/saves go through `piano_core.io.icr` so the on-disk JSON blob matches
the ICR v2 schema used by file exports. In-memory LRU cache on `load()` meets
the i1 acceptance target (cache hit < 50 ms for a 88x8 bank).

Immutability rule: `save()` refuses to overwrite an existing bank id. Producing
a new version must use `Bank.with_notes(..., new_id=...)` so the child bank gets
its own id and a `parent_id` pointing to the previous version.
"""

from __future__ import annotations

import json
import logging
import time
from collections import OrderedDict
from pathlib import Path
from typing import Any, Sequence

import aiosqlite

from piano_core.io.icr import dump_bank_dict, load_bank_dict
from piano_core.models.bank import Bank

from .db import get_db_path, open_connection


logger = logging.getLogger(__name__)


DEFAULT_CACHE_SIZE = 8


class BankRepository:
    """Async CRUD over the ``banks`` table."""

    def __init__(
        self,
        db_path: Path | str | None = None,
        *,
        cache_size: int = DEFAULT_CACHE_SIZE,
    ) -> None:
        self._db_path = Path(db_path) if db_path is not None else get_db_path()
        self._cache: "OrderedDict[str, Bank]" = OrderedDict()
        self._cache_size = max(0, cache_size)

    # ---- queries ------------------------------------------------------

    async def list_summaries(self) -> list[dict[str, Any]]:
        """Return compact rows for the /api/banks listing.

        The domain-level summary (including midi_range, velocities, etc.) is
        computed on demand inside `Bank.summary()` after a full load — that's
        slower but done only when the detail endpoint is hit. This list keeps
        the table-scan cost low.
        """
        t0 = time.perf_counter()
        async with open_connection(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT id, parent_id, instrument, created_at "
                "FROM banks ORDER BY created_at DESC, id ASC;"
            )
            rows = await cur.fetchall()
        result = [dict(r) for r in rows]
        logger.info(
            "repo.list_summaries",
            extra={"count": len(result), "duration_ms": round((time.perf_counter() - t0) * 1000, 2)},
        )
        return result

    async def exists(self, bank_id: str) -> bool:
        async with open_connection(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT 1 FROM banks WHERE id = ? LIMIT 1;", (bank_id,)
            )
            row = await cur.fetchone()
        return row is not None

    async def load(self, bank_id: str) -> Bank | None:
        """Fetch and parse a Bank by id. Returns None if not found.

        On cache hit, returns the cached Bank in O(1) without DB roundtrip.
        On miss, parses the JSON blob through `piano_core.io.icr.load_bank_dict`.
        """
        t0 = time.perf_counter()
        cached = self._cache_get(bank_id)
        if cached is not None:
            logger.debug("repo.load.cache_hit", extra={"bank_id": bank_id})
            return cached

        async with open_connection(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT data_json, parent_id FROM banks WHERE id = ?;",
                (bank_id,),
            )
            row = await cur.fetchone()
        if row is None:
            logger.info("repo.load.miss", extra={"bank_id": bank_id})
            return None

        payload = json.loads(row["data_json"])
        bank = load_bank_dict(payload, bank_id=bank_id, parent_id=row["parent_id"])
        self._cache_put(bank_id, bank)
        logger.info(
            "repo.load.cache_miss",
            extra={
                "bank_id": bank_id,
                "n_notes": len(bank.notes),
                "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
            },
        )
        return bank

    # ---- mutations ----------------------------------------------------

    async def save(self, bank: Bank) -> None:
        """Insert a new bank row.

        Raises ValueError if a bank with the same id already exists; immutable
        chain discipline requires creating a new id per edit.
        """
        t0 = time.perf_counter()
        payload = json.dumps(dump_bank_dict(bank), ensure_ascii=False)
        async with open_connection(self._db_path) as conn:
            try:
                await conn.execute(
                    "INSERT INTO banks (id, data_json, parent_id) VALUES (?, ?, ?);",
                    (bank.id, payload, bank.parent_id),
                )
                await conn.commit()
            except aiosqlite.IntegrityError as e:
                msg = str(e)
                # Disambiguate between UNIQUE (id collision) and FK (dangling parent_id)
                if "UNIQUE" in msg or "PRIMARY KEY" in msg:
                    logger.warning("repo.save.duplicate_id", extra={"bank_id": bank.id})
                    raise ValueError(
                        f"bank id {bank.id!r} already exists — banks are immutable; "
                        "create a child bank with a new id and parent_id"
                    ) from e
                if "FOREIGN KEY" in msg:
                    logger.warning(
                        "repo.save.dangling_parent",
                        extra={"bank_id": bank.id, "parent_id": bank.parent_id},
                    )
                    raise ValueError(
                        f"parent_id {bank.parent_id!r} does not exist in banks table"
                    ) from e
                raise
        self._cache.pop(bank.id, None)  # invalidate
        logger.info(
            "repo.save",
            extra={
                "bank_id": bank.id,
                "parent_id": bank.parent_id,
                "n_notes": len(bank.notes),
                "bytes": len(payload),
                "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
            },
        )

    async def delete(self, bank_id: str) -> bool:
        """Remove a bank row. Returns True if deleted, False if not found.

        Primarily for tests and admin tooling; in production flows banks are
        immutable and new versions chain via parent_id.
        """
        async with open_connection(self._db_path) as conn:
            cur = await conn.execute("DELETE FROM banks WHERE id = ?;", (bank_id,))
            await conn.commit()
            rowcount = cur.rowcount
        self._cache.pop(bank_id, None)
        logger.info("repo.delete", extra={"bank_id": bank_id, "deleted": rowcount > 0})
        return rowcount > 0

    # ---- bulk ingest helper ------------------------------------------

    async def save_many(self, banks: Sequence[Bank]) -> None:
        """Atomically insert multiple banks (for initial import).

        Sorted topologically isn't enforced — caller must order by parent->child
        when chains are being established in a single batch.
        """
        async with open_connection(self._db_path) as conn:
            try:
                for bank in banks:
                    payload = json.dumps(dump_bank_dict(bank), ensure_ascii=False)
                    await conn.execute(
                        "INSERT INTO banks (id, data_json, parent_id) VALUES (?, ?, ?);",
                        (bank.id, payload, bank.parent_id),
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise
        for b in banks:
            self._cache.pop(b.id, None)

    # ---- cache helpers ------------------------------------------------

    def _cache_get(self, bank_id: str) -> Bank | None:
        bank = self._cache.get(bank_id)
        if bank is not None:
            self._cache.move_to_end(bank_id)
        return bank

    def _cache_put(self, bank_id: str, bank: Bank) -> None:
        if self._cache_size == 0:
            return
        self._cache[bank_id] = bank
        self._cache.move_to_end(bank_id)
        while len(self._cache) > self._cache_size:
            evicted, _ = self._cache.popitem(last=False)
            logger.debug("repo.cache.evict", extra={"bank_id": evicted})

    def clear_cache(self) -> None:
        self._cache.clear()

    @property
    def cache_size(self) -> int:
        return len(self._cache)
