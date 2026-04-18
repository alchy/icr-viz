"""Smoke tests for Bank model — parsing, accessors, immutable updates, summary."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial


REPO_ROOT = Path(__file__).resolve().parents[3]
IDEA_DIR = REPO_ROOT / "idea"


# ---- basic construction ---------------------------------------------------

def _demo_partial(k: int = 1) -> Partial:
    return Partial(
        k=k, f_hz=440.0 * k, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.95,
    )


def _demo_note(midi: int = 60, vel: int = 5) -> Note:
    return Note(
        midi=midi, vel=vel, f0_hz=261.6, B=0.0,
        partials=(_demo_partial(1), _demo_partial(2)),
    )


def test_bank_empty_is_valid():
    b = Bank(id="empty")
    assert b.notes == ()
    assert b.midi_range is None
    assert b.instrument is None


def test_bank_accessors_work_on_populated_bank():
    notes = (_demo_note(60, 5), _demo_note(61, 5))
    b = Bank(id="x", notes=notes)
    assert b.get_note(60, 5) is notes[0]
    assert b.get_note(99, 0) is None
    assert b.midi_range == (60, 61)
    assert b.velocities == (5,)
    assert b.note_ids == ((60, 5), (61, 5))


def test_bank_immutability():
    b = Bank(id="x")
    with pytest.raises(Exception):
        b.id = "y"  # type: ignore[misc]


# ---- immutable updates ----------------------------------------------------

def test_with_updated_note_replaces_matching_id_and_chains_parent():
    n = _demo_note(60, 5)
    b = Bank(id="v1", notes=(n,))
    # Build a new note with same id but different f0
    n2 = Note(midi=60, vel=5, f0_hz=999.0, B=0.0, partials=())
    b2 = b.with_updated_note(n2, new_id="v2")
    assert b2.id == "v2"
    assert b2.parent_id == "v1"
    assert b2.get_note(60, 5).f0_hz == pytest.approx(999.0)
    # Original unchanged
    assert b.get_note(60, 5).f0_hz == pytest.approx(261.6)


def test_with_updated_note_appends_if_not_matching():
    b = Bank(id="v1", notes=(_demo_note(60, 5),))
    new_note = _demo_note(61, 5)
    b2 = b.with_updated_note(new_note, new_id="v2")
    assert len(b2.notes) == 2
    assert b2.parent_id == "v1"


def test_with_notes_without_new_id_keeps_chain_unchanged():
    b = Bank(id="v1", notes=(_demo_note(60, 5),))
    b2 = b.with_notes((_demo_note(60, 5), _demo_note(61, 5)))
    assert b2.id == "v1"
    assert b2.parent_id is None
    assert len(b2.notes) == 2


# ---- summary --------------------------------------------------------------

def test_bank_summary_for_api_listing():
    notes = tuple(_demo_note(m, v) for m in (60, 61) for v in (0, 5))
    b = Bank(
        id="demo",
        notes=notes,
        metadata={"instrument_name": "Demo Grand", "k_max": 60,
                  "source": "test", "created": "2026-04-18"},
    )
    s = b.summary()
    assert s["id"] == "demo"
    assert s["instrument"] == "Demo Grand"
    assert s["n_notes"] == 4
    assert s["velocities"] == [0, 5]
    assert s["midi_range"] == [60, 61]
    assert s["k_max"] == 60
    assert s["source"] == "test"


# ---- ICR roundtrip --------------------------------------------------------

def test_from_icr_dict_parses_minimal_bank():
    raw = {
        "metadata": {"instrument_name": "Tiny", "k_max": 60},
        "notes": {
            "m021_vel0": {
                "midi": 21, "vel": 0, "f0_hz": 27.42, "B": 0.0,
                "partials": [
                    {"k": 1, "f_hz": 27.42, "A0": 1.4, "tau1": 14.3, "tau2": 14.3,
                     "a1": 1.0, "beat_hz": 0.0, "phi": 0.0, "fit_quality": 0.87},
                ],
            },
        },
    }
    b = Bank.from_icr_dict(raw, bank_id="tiny")
    assert b.id == "tiny"
    assert b.instrument == "Tiny"
    assert len(b.notes) == 1
    assert b.get_note(21, 0).partials[0].k == 1


def test_icr_roundtrip_v2_schema_includes_version_and_sigma_origin():
    partial = Partial(
        k=1, f_hz=440.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.99,
        sigma=0.01, origin="anchored",
    )
    note = Note(midi=60, vel=5, f0_hz=261.6, B=0.0, partials=(partial,))
    bank = Bank(id="v2test", notes=(note,), metadata={"k_max": 60})

    dumped = bank.to_icr_dict()
    assert dumped["icr_version"] == 2
    assert dumped["bank_id"] == "v2test"
    p_dict = dumped["notes"]["m060_vel5"]["partials"][0]
    assert p_dict["sigma"] == pytest.approx(0.01)
    assert p_dict["origin"] == "anchored"

    restored = Bank.from_icr_dict(dumped, bank_id="v2test")
    assert restored.get_note(60, 5).partials[0] == partial


# ---- integration with real reference banks --------------------------------

@pytest.mark.parametrize("bank_file", [
    "icr-bank-sample1.json",
    "icr-bank-sample2.json",
    "ks-grand-04130018-raw.json",
    "ks-grand-2604161547-icr.json",
    "as-blackgrand-04162340-raw-icr.json",
])
def test_load_real_reference_bank(bank_file: str):
    """Every ICR bank in idea/ must load without errors and produce a non-empty summary."""
    path = IDEA_DIR / bank_file
    if not path.exists():
        pytest.skip(f"{bank_file} not present")
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    bank = Bank.from_icr_dict(raw, bank_id=path.stem)
    assert bank.id == path.stem
    assert len(bank.notes) > 0
    summary = bank.summary()
    assert summary["n_notes"] == len(bank.notes)
    # Every note should have parseable partials
    for note in bank.notes:
        for p in note.partials:
            assert p.k >= 1
            assert p.f_hz >= 0
            assert p.origin in ("measured", "derived", "extrapolated", "anchored")
