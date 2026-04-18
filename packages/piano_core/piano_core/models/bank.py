"""Bank — immutable container for a full ICR soundbank.

In i1 Bank holds `notes` plus `metadata`. Fields `anchors` and `edit_history`
exist but are empty tuples until i2 / i3 populate them. Bank is frozen —
mutations (add anchor, apply operator) return a new Bank linked via `parent_id`,
forming an immutable chain that supports deterministic replay (i5).
"""

from __future__ import annotations

from dataclasses import dataclass, field, replace
from types import MappingProxyType
from typing import Any, Mapping

from .anchor import Anchor
from .note import Note


@dataclass(frozen=True, slots=True)
class Bank:
    """One versioned soundbank."""

    id: str                                       # "as-blackgrand-04162340-raw-icr" or UUID
    parent_id: str | None = None                  # prior version in immutable chain
    metadata: Mapping[str, Any] = field(
        default_factory=lambda: MappingProxyType({})
    )
    notes: tuple[Note, ...] = ()

    # i2: anchors are now strongly typed. Still a tuple (immutable) for hashability.
    anchors: tuple[Anchor, ...] = ()
    # i3 will narrow this to tuple[EditRecord, ...] — kept opaque here so i1 stays green
    # while the operator pipeline is still being built.
    edit_history: tuple = ()

    # ---- accessors -----------------------------------------------------

    def get_note(self, midi: int, vel: int) -> Note | None:
        for n in self.notes:
            if n.midi == midi and n.vel == vel:
                return n
        return None

    @property
    def note_ids(self) -> tuple[tuple[int, int], ...]:
        return tuple(n.id for n in self.notes)

    @property
    def velocities(self) -> tuple[int, ...]:
        return tuple(sorted({n.vel for n in self.notes}))

    @property
    def midi_range(self) -> tuple[int, int] | None:
        if not self.notes:
            return None
        midis = [n.midi for n in self.notes]
        return (min(midis), max(midis))

    @property
    def instrument(self) -> str | None:
        """Short-hand for metadata.instrument_name (used by /api/banks summary)."""
        v = self.metadata.get("instrument_name") if self.metadata else None
        return v or None

    @property
    def k_max(self) -> int | None:
        v = self.metadata.get("k_max") if self.metadata else None
        return int(v) if v is not None else None

    def anchors_for_note(self, midi: int, vel: int) -> tuple[Anchor, ...]:
        return tuple(a for a in self.anchors if a.midi == midi and a.velocity == vel)

    def anchors_for_parameter(
        self, midi: int, vel: int, parameter: str,
    ) -> tuple[Anchor, ...]:
        return tuple(
            a for a in self.anchors
            if a.midi == midi and a.velocity == vel and a.parameter == parameter
        )

    def anchor_by_id(self, anchor_id: str) -> Anchor | None:
        for a in self.anchors:
            if a.id == anchor_id:
                return a
        return None

    # ---- immutable anchor mutations (return a new Bank) ---------------

    def with_added_anchor(self, anchor: Anchor, *, new_id: str | None = None) -> "Bank":
        """Return a child bank with `anchor` appended. Raises on duplicate id."""
        if any(a.id == anchor.id for a in self.anchors):
            raise ValueError(f"anchor id {anchor.id!r} already exists in this bank")
        return replace(
            self,
            id=new_id if new_id is not None else self.id,
            parent_id=self.id if new_id is not None else self.parent_id,
            anchors=self.anchors + (anchor,),
        )

    def with_patched_anchor(self, anchor: Anchor, *, new_id: str | None = None) -> "Bank":
        """Return a child bank with the anchor matching anchor.id replaced by the new version."""
        updated: list[Anchor] = []
        found = False
        for a in self.anchors:
            if a.id == anchor.id:
                updated.append(anchor)
                found = True
            else:
                updated.append(a)
        if not found:
            raise ValueError(f"anchor id {anchor.id!r} not present in bank")
        return replace(
            self,
            id=new_id if new_id is not None else self.id,
            parent_id=self.id if new_id is not None else self.parent_id,
            anchors=tuple(updated),
        )

    def with_removed_anchor(self, anchor_id: str, *, new_id: str | None = None) -> "Bank":
        """Return a child bank with the anchor having this id dropped."""
        filtered = tuple(a for a in self.anchors if a.id != anchor_id)
        if len(filtered) == len(self.anchors):
            raise ValueError(f"anchor id {anchor_id!r} not present in bank")
        return replace(
            self,
            id=new_id if new_id is not None else self.id,
            parent_id=self.id if new_id is not None else self.parent_id,
            anchors=filtered,
        )

    # ---- immutable updates --------------------------------------------

    def with_notes(self, notes: tuple[Note, ...], *, new_id: str | None = None) -> "Bank":
        """Produce a child bank with replaced notes; forms parent/child chain."""
        return replace(
            self,
            id=new_id if new_id is not None else self.id,
            parent_id=self.id if new_id is not None else self.parent_id,
            notes=notes,
        )

    def with_updated_note(self, note: Note, *, new_id: str | None = None) -> "Bank":
        """Return a new Bank with `note` replacing the matching (midi, vel)."""
        replaced = False
        new_notes: list[Note] = []
        for n in self.notes:
            if n.id == note.id:
                new_notes.append(note)
                replaced = True
            else:
                new_notes.append(n)
        if not replaced:
            new_notes.append(note)
        return self.with_notes(tuple(new_notes), new_id=new_id)

    # ---- serialization -------------------------------------------------

    def summary(self) -> dict[str, Any]:
        """Compact dict for /api/banks listing (no partials)."""
        rng = self.midi_range
        return {
            "id": self.id,
            "parent_id": self.parent_id,
            "instrument": self.instrument,
            "n_notes": len(self.notes),
            "velocities": list(self.velocities),
            "midi_range": list(rng) if rng else None,
            "k_max": self.k_max,
            "created_at": self.metadata.get("created") if self.metadata else None,
            "source": self.metadata.get("source") if self.metadata else None,
        }

    def to_icr_dict(self) -> dict[str, Any]:
        """Full ICR JSON serialization. ICR v2 schema — includes sigma/origin on partials
        and the anchors array. Anchors survive ICR round-trips so an exported bank replayed
        into a fresh DB still holds its override points.
        """
        payload: dict[str, Any] = {
            "icr_version": 2,
            "bank_id": self.id,
            "parent_id": self.parent_id,
            "metadata": dict(self.metadata),
            "notes": {n.note_key: n.to_icr_dict() for n in self.notes},
        }
        if self.anchors:
            payload["anchors"] = [a.as_dict() for a in self.anchors]
        return payload

    @classmethod
    def from_icr_dict(
        cls,
        data: dict[str, Any],
        *,
        bank_id: str,
        parent_id: str | None = None,
    ) -> "Bank":
        """Parse an ICR bank JSON. Handles both v1 (missing sigma/origin) and v2.

        `bank_id` is taken from the caller because legacy files don't carry it;
        for v2 exports it will match ``data['bank_id']`` when present.
        """
        metadata = dict(data.get("metadata") or {})
        raw_notes = data.get("notes") or {}
        if not isinstance(raw_notes, dict):
            raise TypeError(f"`notes` must be a dict, got {type(raw_notes).__name__}")

        notes = tuple(
            Note.from_icr_dict(note_dict, note_key=note_key)
            for note_key, note_dict in raw_notes.items()
        )
        notes = tuple(sorted(notes, key=lambda n: (n.midi, n.vel)))

        raw_anchors = data.get("anchors") or []
        if not isinstance(raw_anchors, list):
            raise TypeError(f"`anchors` must be a list, got {type(raw_anchors).__name__}")
        anchors = tuple(Anchor.from_dict(a) for a in raw_anchors)

        return cls(
            id=bank_id,
            parent_id=parent_id or data.get("parent_id"),
            metadata=MappingProxyType(metadata),
            notes=notes,
            anchors=anchors,
        )
