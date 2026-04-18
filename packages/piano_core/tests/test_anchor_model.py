"""Tests for Anchor dataclass + Bank anchor-mutation helpers."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest

from piano_core.models.anchor import Anchor
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial


# ---- construction + validation ------------------------------------------

def test_anchor_has_defaults():
    a = Anchor(midi=60, velocity=5, k=3, parameter="tau1", value=0.4)
    assert a.weight == 0.5
    assert a.origin == "manual"
    assert a.created_by == "system"
    assert a.note == ""
    assert len(a.id) == 36    # uuid4
    assert a.created_at.tzinfo == timezone.utc


def test_anchor_rejects_invalid_parameter():
    with pytest.raises(ValueError):
        Anchor(midi=60, velocity=5, k=1, parameter="bogus", value=0.1)


def test_anchor_rejects_weight_outside_unit_interval():
    with pytest.raises(ValueError):
        Anchor(midi=60, velocity=5, k=1, parameter="tau1", value=0.1, weight=1.5)
    with pytest.raises(ValueError):
        Anchor(midi=60, velocity=5, k=1, parameter="tau1", value=0.1, weight=-0.1)


def test_anchor_rejects_zero_k():
    with pytest.raises(ValueError):
        Anchor(midi=60, velocity=5, k=0, parameter="tau1", value=0.1)


def test_anchor_rejects_unknown_origin():
    with pytest.raises(ValueError):
        Anchor(
            midi=60, velocity=5, k=1, parameter="tau1", value=0.1,
            origin="hacked",  # type: ignore[arg-type]
        )


# ---- roundtrip ----------------------------------------------------------

def test_anchor_as_dict_roundtrip():
    original = Anchor(
        midi=60, velocity=5, k=3, parameter="tau1", value=0.42,
        weight=0.9, origin="imported", created_by="tester", note="sanity",
    )
    d = original.as_dict()
    restored = Anchor.from_dict(d)
    assert restored == original


def test_anchor_from_dict_defaults_missing_fields():
    a = Anchor.from_dict({
        "midi": 60, "velocity": 5, "k": 1, "parameter": "A0", "value": 1.0,
    })
    assert a.weight == 0.5
    assert a.origin == "manual"
    assert a.created_by == "system"
    assert a.id   # auto-generated


def test_anchor_patched_preserves_id_and_timestamp():
    a = Anchor(midi=60, velocity=5, k=1, parameter="A0", value=1.0)
    patched = a.patched(value=2.0, weight=0.8, note="revised")
    assert patched.id == a.id
    assert patched.created_at == a.created_at
    assert patched.value == 2.0
    assert patched.weight == 0.8
    assert patched.note == "revised"


# ---- Bank integration ---------------------------------------------------

def _demo_bank() -> Bank:
    p = Partial(k=1, f_hz=440.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=0.99)
    n = Note(midi=60, vel=5, f0_hz=261.6, B=0.0, partials=(p,))
    return Bank(id="v1", notes=(n,))


def test_with_added_anchor_chains_parent():
    bank = _demo_bank()
    anchor = Anchor(midi=60, velocity=5, k=3, parameter="tau1", value=0.4)
    child = bank.with_added_anchor(anchor, new_id="v2")
    assert child.id == "v2"
    assert child.parent_id == "v1"
    assert len(child.anchors) == 1
    assert child.anchors[0] is anchor
    # Original untouched
    assert bank.anchors == ()


def test_with_added_anchor_rejects_duplicate_id():
    bank = _demo_bank()
    a = Anchor(midi=60, velocity=5, k=1, parameter="tau1", value=0.5)
    child = bank.with_added_anchor(a)
    duplicate = Anchor(midi=60, velocity=5, k=2, parameter="tau1", value=0.1, id=a.id)
    with pytest.raises(ValueError, match="already exists"):
        child.with_added_anchor(duplicate)


def test_with_patched_anchor_replaces_matching_id():
    bank = _demo_bank()
    a = Anchor(midi=60, velocity=5, k=1, parameter="tau1", value=0.5)
    b1 = bank.with_added_anchor(a)
    patched = a.patched(value=0.9)
    b2 = b1.with_patched_anchor(patched)
    assert b2.anchor_by_id(a.id).value == pytest.approx(0.9)


def test_with_patched_anchor_raises_on_missing_id():
    bank = _demo_bank()
    orphan = Anchor(midi=60, velocity=5, k=1, parameter="tau1", value=0.1)
    with pytest.raises(ValueError, match="not present"):
        bank.with_patched_anchor(orphan)


def test_with_removed_anchor_filters_by_id():
    bank = _demo_bank()
    a1 = Anchor(midi=60, velocity=5, k=1, parameter="tau1", value=0.5)
    a2 = Anchor(midi=60, velocity=5, k=2, parameter="tau1", value=0.3)
    intermediate = bank.with_added_anchor(a1).with_added_anchor(a2)
    after = intermediate.with_removed_anchor(a1.id)
    assert len(after.anchors) == 1
    assert after.anchors[0].id == a2.id


def test_with_removed_anchor_raises_on_missing_id():
    bank = _demo_bank()
    with pytest.raises(ValueError, match="not present"):
        bank.with_removed_anchor("does-not-exist")


def test_anchors_for_note_filters_by_midi_velocity():
    bank = _demo_bank()
    a1 = Anchor(midi=60, velocity=5, k=1, parameter="tau1", value=0.5)
    a2 = Anchor(midi=61, velocity=5, k=1, parameter="tau1", value=0.3)
    populated = bank.with_added_anchor(a1).with_added_anchor(a2)
    assert populated.anchors_for_note(60, 5) == (a1,)
    assert populated.anchors_for_note(61, 5) == (a2,)
    assert populated.anchors_for_note(99, 0) == ()


def test_anchors_for_parameter_narrows_further():
    bank = _demo_bank()
    a_tau = Anchor(midi=60, velocity=5, k=1, parameter="tau1", value=0.5)
    a_a0 = Anchor(midi=60, velocity=5, k=1, parameter="A0", value=1.5)
    populated = bank.with_added_anchor(a_tau).with_added_anchor(a_a0)
    assert populated.anchors_for_parameter(60, 5, "tau1") == (a_tau,)
    assert populated.anchors_for_parameter(60, 5, "A0") == (a_a0,)


def test_anchor_survives_icr_roundtrip():
    bank = _demo_bank()
    a = Anchor(
        midi=60, velocity=5, k=1, parameter="tau1", value=0.5,
        weight=0.8, origin="imported", note="restore",
    )
    chained = bank.with_added_anchor(a, new_id="v2")

    dumped = chained.to_icr_dict()
    assert "anchors" in dumped
    assert len(dumped["anchors"]) == 1

    restored = Bank.from_icr_dict(dumped, bank_id="v2", parent_id="v1")
    assert restored.anchors == chained.anchors
    # Anchor id preserved
    assert restored.anchor_by_id(a.id) == a


def test_bank_without_anchors_round_trips_without_anchors_key():
    bank = _demo_bank()
    dumped = bank.to_icr_dict()
    assert "anchors" not in dumped   # suppressed when empty
    restored = Bank.from_icr_dict(dumped, bank_id=bank.id)
    assert restored.anchors == ()
