"""AnchorRepository — SQLite-backed persistence for Anchor overrides.

Anchors are stored in a separate table so cross-bank queries (planned for i3
anchor propagation) stay O(n) on indexed columns rather than forcing JSON
path scans over bank blobs. The repository is intentionally thin: all the
interesting logic (weight-to-WLS conversion, pipeline invocation) lives in
piano_core — this layer just persists.

Immutable-chain discipline applies to anchor mutations too. An anchor CRUD
operation should be paired with a new Bank version in the calling layer;
this repository does not enforce that coupling (the router does).
"""

from __future__ import annotations

import logging
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Sequence

import aiosqlite

from piano_core.models.anchor import Anchor

from .db import get_db_path, open_connection


logger = logging.getLogger(__name__)


class AnchorRepository:
    def __init__(self, db_path: Path | str | None = None) -> None:
        self._db_path = Path(db_path) if db_path is not None else get_db_path()

    # ---- queries ------------------------------------------------------

    async def list_for_bank(self, bank_id: str) -> list[Anchor]:
        async with open_connection(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT * FROM anchors WHERE bank_id = ? ORDER BY midi, velocity, k, parameter;",
                (bank_id,),
            )
            rows = await cur.fetchall()
        return [_row_to_anchor(r) for r in rows]

    async def list_for_note(
        self, bank_id: str, midi: int, velocity: int,
    ) -> list[Anchor]:
        async with open_connection(self._db_path) as conn:
            cur = await conn.execute(
                "SELECT * FROM anchors "
                "WHERE bank_id = ? AND midi = ? AND velocity = ? "
                "ORDER BY k, parameter;",
                (bank_id, midi, velocity),
            )
            rows = await cur.fetchall()
        return [_row_to_anchor(r) for r in rows]

    async def get(self, anchor_id: str) -> Anchor | None:
        async with open_connection(self._db_path) as conn:
            cur = await conn.execute("SELECT * FROM anchors WHERE id = ?;", (anchor_id,))
            row = await cur.fetchone()
        return _row_to_anchor(row) if row else None

    # ---- mutations ----------------------------------------------------

    async def save(self, anchor: Anchor, *, bank_id: str) -> None:
        """Insert a new anchor. Raises ValueError on id collision or missing bank."""
        t0 = time.perf_counter()
        async with open_connection(self._db_path) as conn:
            try:
                await conn.execute(
                    """
                    INSERT INTO anchors (
                        id, bank_id, midi, velocity, k, parameter, value, weight,
                        origin, created_at, created_by, note
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                    """,
                    (
                        anchor.id, bank_id, anchor.midi, anchor.velocity, anchor.k,
                        anchor.parameter, anchor.value, anchor.weight,
                        anchor.origin, anchor.created_at.isoformat(),
                        anchor.created_by, anchor.note,
                    ),
                )
                await conn.commit()
            except aiosqlite.IntegrityError as e:
                msg = str(e)
                if "UNIQUE" in msg or "PRIMARY KEY" in msg:
                    raise ValueError(f"anchor id {anchor.id!r} already exists") from e
                if "FOREIGN KEY" in msg:
                    raise ValueError(f"bank_id {bank_id!r} does not exist") from e
                raise
        logger.info(
            "anchor_repo.save",
            extra={
                "anchor_id": anchor.id, "bank_id": bank_id,
                "midi": anchor.midi, "velocity": anchor.velocity,
                "k": anchor.k, "parameter": anchor.parameter,
                "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
            },
        )

    async def patch(
        self,
        anchor_id: str,
        *,
        value: float | None = None,
        weight: float | None = None,
        note: str | None = None,
    ) -> bool:
        """Update mutable anchor fields in place. Returns True iff a row was updated.

        ``value``, ``weight``, ``note`` are the only fields that can be patched —
        identity fields (midi/velocity/k/parameter) would break referential meaning
        and are covered by delete+create instead.
        """
        assignments: list[str] = []
        params: list[Any] = []
        if value is not None:
            assignments.append("value = ?")
            params.append(float(value))
        if weight is not None:
            if not (0.0 <= weight <= 1.0):
                raise ValueError(f"weight must lie in [0, 1], got {weight}")
            assignments.append("weight = ?")
            params.append(float(weight))
        if note is not None:
            assignments.append("note = ?")
            params.append(note)
        if not assignments:
            return False
        params.append(anchor_id)
        sql = f"UPDATE anchors SET {', '.join(assignments)} WHERE id = ?;"
        async with open_connection(self._db_path) as conn:
            cur = await conn.execute(sql, tuple(params))
            await conn.commit()
            rowcount = cur.rowcount
        logger.info("anchor_repo.patch", extra={"anchor_id": anchor_id, "updated": rowcount > 0})
        return rowcount > 0

    async def delete(self, anchor_id: str) -> bool:
        async with open_connection(self._db_path) as conn:
            cur = await conn.execute("DELETE FROM anchors WHERE id = ?;", (anchor_id,))
            await conn.commit()
            rowcount = cur.rowcount
        logger.info("anchor_repo.delete", extra={"anchor_id": anchor_id, "deleted": rowcount > 0})
        return rowcount > 0

    # ---- bulk --------------------------------------------------------

    async def save_many(self, anchors: Sequence[Anchor], *, bank_id: str) -> None:
        async with open_connection(self._db_path) as conn:
            try:
                for a in anchors:
                    await conn.execute(
                        """
                        INSERT INTO anchors (
                            id, bank_id, midi, velocity, k, parameter, value, weight,
                            origin, created_at, created_by, note
                        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?);
                        """,
                        (
                            a.id, bank_id, a.midi, a.velocity, a.k,
                            a.parameter, a.value, a.weight,
                            a.origin, a.created_at.isoformat(),
                            a.created_by, a.note,
                        ),
                    )
                await conn.commit()
            except Exception:
                await conn.rollback()
                raise


# ---------------------------------------------------------------------------
# row -> Anchor mapping (tolerate missing tz on stored timestamps)
# ---------------------------------------------------------------------------

def _row_to_anchor(row: Any) -> Anchor:
    if row is None:
        raise ValueError("cannot build Anchor from None row")
    ts_raw = row["created_at"]
    try:
        ts = datetime.fromisoformat(ts_raw)
    except (ValueError, TypeError):
        ts = datetime.now(timezone.utc)
    if ts.tzinfo is None:
        ts = ts.replace(tzinfo=timezone.utc)

    return Anchor(
        id=row["id"],
        midi=int(row["midi"]),
        velocity=int(row["velocity"]),
        k=int(row["k"]),
        parameter=row["parameter"],
        value=float(row["value"]),
        weight=float(row["weight"]),
        origin=row["origin"],
        created_at=ts,
        created_by=row["created_by"],
        note=row["note"],
    )
