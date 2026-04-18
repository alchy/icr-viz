"""Tests for export + bank hash round-trip."""

from __future__ import annotations

import csv
import io
import json
import math

import pytest

from piano_core.io.export import (
    ANALYSIS_CSV_EXTRA,
    SYNTH_CSV_COLUMNS,
    bank_hash,
    to_analysis_csv,
    to_ndjson,
    to_synth_csv,
)
from piano_core.io.icr import dump_bank_dict, load_bank_dict
from piano_core.models.anchor import Anchor
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial


def _synth_note(midi: int = 60) -> Note:
    partials = []
    for k in range(1, 6):
        partials.append(Partial(
            k=k, f_hz=100.0 * k, A0=10.0 * k ** (-0.5),
            tau1=0.5 * k ** (-0.7), tau2=5.0 * k ** (-0.7),
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
            origin="measured",
        ))
    # one extrapolated partial for the exclude test
    partials.append(Partial(
        k=6, f_hz=600.0, A0=0.1, tau1=0.1, tau2=1.0,
        a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.5,
        origin="extrapolated",
    ))
    return Note(midi=midi, vel=5, f0_hz=100.0, B=5e-4, partials=tuple(partials))


# ---- synth_csv ---------------------------------------------------------

def test_synth_csv_has_expected_columns():
    bank = Bank(id="demo", notes=(_synth_note(),))
    csv_text = to_synth_csv(bank)
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader)
    assert tuple(header) == SYNTH_CSV_COLUMNS


def test_synth_csv_row_per_partial():
    bank = Bank(id="demo", notes=(_synth_note(60), _synth_note(61)))
    csv_text = to_synth_csv(bank)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    # 6 partials per note × 2 notes = 12
    assert len(rows) == 12


def test_synth_csv_excludes_extrapolated_when_requested():
    bank = Bank(id="demo", notes=(_synth_note(),))
    csv_text = to_synth_csv(bank, exclude_extrapolated=True)
    rows = list(csv.DictReader(io.StringIO(csv_text)))
    # 5 measured, 1 extrapolated → 5 after exclusion
    assert len(rows) == 5
    assert all(r["origin"] == "measured" for r in rows)


# ---- analysis_csv -------------------------------------------------------

def test_analysis_csv_extends_synth_columns():
    bank = Bank(id="demo", notes=(_synth_note(),))
    csv_text = to_analysis_csv(bank)
    reader = csv.reader(io.StringIO(csv_text))
    header = next(reader)
    assert tuple(header) == SYNTH_CSV_COLUMNS + ANALYSIS_CSV_EXTRA


def test_analysis_csv_per_partial_has_note_level_stats():
    bank = Bank(id="demo", notes=(_synth_note(),))
    rows = list(csv.DictReader(io.StringIO(to_analysis_csv(bank))))
    # Same note stats attached to every row from that note
    note_B_hat_values = {r["note_B_hat"] for r in rows}
    assert len(note_B_hat_values) == 1    # same B_hat on every row
    assert rows[0]["f0_hz"] == "100.0"


# ---- ndjson --------------------------------------------------------------

def test_ndjson_emits_header_plus_notes():
    bank = Bank(
        id="demo",
        notes=(_synth_note(60), _synth_note(61)),
        metadata={"instrument_name": "Demo"},
    )
    lines = list(to_ndjson(bank))
    # 1 header + 2 notes
    assert len(lines) == 3
    header = json.loads(lines[0])
    assert header["bank_id"] == "demo"
    assert header["n_notes"] == 2
    note_obj = json.loads(lines[1])
    assert note_obj["__note__"] is True


def test_ndjson_includes_anchors_when_present():
    note = _synth_note(60)
    anchor = Anchor(midi=60, velocity=5, k=3, parameter="tau1", value=0.4)
    bank = Bank(id="demo", notes=(note,), anchors=(anchor,))
    lines = list(to_ndjson(bank, include_anchors=True))
    assert any(json.loads(l).get("__anchor__") for l in lines[2:])


# ---- bank_hash & round-trip ----------------------------------------------

def test_bank_hash_stable_across_identical_serialisations():
    note = _synth_note()
    bank = Bank(id="h1", notes=(note,), metadata={"instrument_name": "Same"})
    bank2 = Bank(id="h1", notes=(note,), metadata={"instrument_name": "Same"})
    assert bank_hash(bank) == bank_hash(bank2)


def test_bank_hash_ignores_timestamp_fields():
    note = _synth_note()
    bank1 = Bank(id="h", notes=(note,), metadata={"instrument_name": "X", "created": "2026-01-01"})
    bank2 = Bank(id="h", notes=(note,), metadata={"instrument_name": "X", "created": "2026-12-31"})
    assert bank_hash(bank1) == bank_hash(bank2)


def test_bank_hash_ignores_anchor_created_at():
    from datetime import datetime, timezone
    note = _synth_note()
    a1 = Anchor(
        midi=60, velocity=5, k=3, parameter="tau1", value=0.4,
        created_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )
    a2 = Anchor(
        midi=60, velocity=5, k=3, parameter="tau1", value=0.4,
        id=a1.id,   # same id so they are "the same anchor" at different timestamps
        created_at=datetime(2026, 6, 30, tzinfo=timezone.utc),
    )
    bank1 = Bank(id="h", notes=(note,), anchors=(a1,))
    bank2 = Bank(id="h", notes=(note,), anchors=(a2,))
    assert bank_hash(bank1) == bank_hash(bank2)


def test_bank_hash_changes_on_partial_value_change():
    note = _synth_note()
    bank1 = Bank(id="h", notes=(note,))
    new_partials = list(note.partials)
    p = new_partials[0]
    new_partials[0] = Partial(
        k=p.k, f_hz=p.f_hz + 0.001, A0=p.A0, tau1=p.tau1, tau2=p.tau2,
        a1=p.a1, beat_hz=p.beat_hz, phi=p.phi, fit_quality=p.fit_quality, origin=p.origin,
    )
    note2 = Note(
        midi=note.midi, vel=note.vel, f0_hz=note.f0_hz, B=note.B,
        partials=tuple(new_partials),
    )
    bank2 = Bank(id="h", notes=(note2,))
    assert bank_hash(bank1) != bank_hash(bank2)


def test_icr_roundtrip_preserves_bank_hash():
    note = _synth_note()
    bank = Bank(
        id="rt",
        notes=(note,),
        metadata={"instrument_name": "RT", "k_max": 6},
    )
    h0 = bank_hash(bank)
    dumped = dump_bank_dict(bank)
    restored = load_bank_dict(dumped, bank_id="rt")
    assert bank_hash(restored) == h0
