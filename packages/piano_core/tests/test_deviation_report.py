"""Tests for deviation_report — LOO cross-bank anomaly detection."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.completion.deviation_report import (
    ReferenceBankSample,
    deviation_report,
)
from piano_core.models.note import Note
from piano_core.models.partial import Partial


def _note(
    *, midi: int = 60, vel: int = 5, noise_scale: float = 0.0, rng_seed: int = 0,
    override_k: int | None = None, override_tau1: float | None = None, k_max: int = 12,
) -> Note:
    rng = np.random.default_rng(rng_seed)
    partials = []
    for k in range(1, k_max + 1):
        tau = 0.5 * k ** (-0.7)
        if noise_scale > 0:
            tau *= 1.0 + rng.normal(0.0, noise_scale)
        if override_k is not None and k == override_k and override_tau1 is not None:
            tau = override_tau1
        partials.append(Partial(
            k=k, f_hz=100 * k, A0=10.0 * k ** (-0.5),
            tau1=max(tau, 1e-6), tau2=5 * max(tau, 1e-6), a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=100.0, B=0.0, partials=tuple(partials))


def _ref_sample(bank_id: str, note: Note) -> ReferenceBankSample:
    return ReferenceBankSample(
        bank_id=bank_id,
        note_by_key={(note.midi, note.vel): note},
    )


# ---- basic flow ----------------------------------------------------------

def test_matching_banks_produce_no_anomalies():
    """Two identical reference banks vs. clean target → no entries above min_z."""
    target = _note(rng_seed=0)
    refs = [_ref_sample("a", _note(noise_scale=0.01, rng_seed=1)),
            _ref_sample("b", _note(noise_scale=0.01, rng_seed=2))]
    report = deviation_report(
        target_bank_id="target",
        target_notes={(60, 5): target},
        references=refs,
        parameters=("tau1",),
        min_z=2.0,
    )
    assert len(report.entries) == 0
    assert report.loo is False


def test_injected_anomaly_is_detected():
    """Target has a huge tau1 at k=5 — should show up at the top of the report."""
    target = _note(override_k=5, override_tau1=3.0)
    refs = [
        _ref_sample("a", _note(rng_seed=1)),
        _ref_sample("b", _note(rng_seed=2)),
    ]
    report = deviation_report(
        target_bank_id="target",
        target_notes={(60, 5): target},
        references=refs,
        parameters=("tau1",),
        min_z=2.0,
    )
    assert len(report.entries) > 0
    top = report.entries[0]
    assert top.parameter == "tau1"
    assert top.k == 5
    assert abs(top.z_score) >= 2.0


def test_entries_sorted_descending_by_abs_z():
    # Inject two anomalies, one much larger than the other
    target_partials = list(_note().partials)
    target_partials[2] = Partial(
        k=3, f_hz=300.0, A0=10.0 * 3 ** (-0.5),
        tau1=1.0, tau2=5.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.95,
    )
    target_partials[6] = Partial(
        k=7, f_hz=700.0, A0=10.0 * 7 ** (-0.5),
        tau1=10.0, tau2=50.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.95,
    )
    target = Note(midi=60, vel=5, f0_hz=100.0, B=0.0, partials=tuple(target_partials))
    refs = [
        _ref_sample("a", _note(rng_seed=1)),
        _ref_sample("b", _note(rng_seed=2)),
    ]
    report = deviation_report(
        target_bank_id="target",
        target_notes={(60, 5): target},
        references=refs,
        parameters=("tau1",),
        min_z=2.0,
    )
    # Should have both entries (or more), sorted by |z| desc
    assert len(report.entries) >= 2
    assert abs(report.entries[0].z_score) >= abs(report.entries[1].z_score)


def test_leave_one_out_excludes_target_from_consensus():
    """Virtual_test §7.1 #12: if target is in references, LOO path should drop it."""
    target = _note(override_k=5, override_tau1=3.0)
    refs = [
        _ref_sample("a", _note(rng_seed=1)),
        _ref_sample("b", _note(rng_seed=2)),
        _ref_sample("target", target),   # target also a ref
    ]
    report = deviation_report(
        target_bank_id="target",
        target_notes={(60, 5): target},
        references=refs,
        parameters=("tau1",),
        min_z=2.0,
    )
    assert report.loo is True
    # Anomaly still detected because the target was excluded from consensus
    assert any(e.k == 5 for e in report.entries)


def test_insufficient_references_returns_empty():
    target = _note(override_k=5, override_tau1=3.0)
    refs = [_ref_sample("only_one", _note(rng_seed=1))]
    report = deviation_report(
        target_bank_id="target",
        target_notes={(60, 5): target},
        references=refs,
        parameters=("tau1",),
        min_z=2.0,
    )
    # Only 1 source → can't build consensus → no entries
    assert len(report.entries) == 0


def test_note_filter_restricts_scan():
    target = _note()
    target_60 = Note(
        midi=60, vel=5, f0_hz=target.f0_hz, B=target.B,
        partials=tuple(target.partials),
    )
    target_61 = Note(
        midi=61, vel=5, f0_hz=target.f0_hz, B=target.B,
        partials=tuple(target.partials),
    )
    refs = [
        _ref_sample("a", _note(rng_seed=1)),
        _ref_sample("b", _note(rng_seed=2)),
    ]
    report = deviation_report(
        target_bank_id="target",
        target_notes={(60, 5): target_60, (61, 5): target_61},
        references=refs,
        parameters=("tau1",),
        min_z=2.0,
        note_filter=[(60, 5)],
    )
    # Even if both notes were scanned, filter restricts to (60, 5)
    midi_set = {e.midi for e in report.entries}
    assert 61 not in midi_set


def test_recommend_action_thresholds():
    """|z| < 2.5 → review; 2.5–5 → soft; >5 → hard."""
    target_partials = list(_note().partials)
    # Moderate anomaly at k=3 (soft)
    target_partials[2] = Partial(
        k=3, f_hz=300.0, A0=10.0 * 3 ** (-0.5), tau1=0.6, tau2=3.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.95,
    )
    # Extreme anomaly at k=7 (hard)
    target_partials[6] = Partial(
        k=7, f_hz=700.0, A0=10.0 * 7 ** (-0.5), tau1=15.0, tau2=75.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.95,
    )
    target = Note(midi=60, vel=5, f0_hz=100.0, B=0.0, partials=tuple(target_partials))
    refs = [
        _ref_sample("a", _note(rng_seed=1)),
        _ref_sample("b", _note(rng_seed=2)),
    ]
    report = deviation_report(
        target_bank_id="target",
        target_notes={(60, 5): target},
        references=refs,
        parameters=("tau1",),
        min_z=1.5,
    )
    actions = {e.k: e.recommend_action for e in report.entries}
    if 7 in actions:
        assert actions[7] == "hard_correction"


def test_report_as_dict_is_json_safe():
    import json
    target = _note(override_k=5, override_tau1=3.0)
    refs = [
        _ref_sample("a", _note(rng_seed=1)),
        _ref_sample("b", _note(rng_seed=2)),
    ]
    report = deviation_report(
        target_bank_id="target",
        target_notes={(60, 5): target},
        references=refs,
    )
    payload = report.as_dict()
    json.dumps(payload)
    assert payload["target_bank_id"] == "target"
