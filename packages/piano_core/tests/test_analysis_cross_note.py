"""Tests for cross-note monotonicity checks."""

from __future__ import annotations

import math

import pytest

from piano_core.analysis.cross_note import (
    EXPECTED_DIRECTION,
    MonotonicityViolation,
    check_monotonicity,
)
from piano_core.models.note import Note
from piano_core.models.partial import Partial


def _note(midi: int, *, tau1_scale: float = 1.0, vel: int = 5, k_max: int = 10) -> Note:
    partials = []
    for k in range(1, k_max + 1):
        partials.append(Partial(
            k=k, f_hz=100.0 * k,
            A0=10.0 * k ** (-0.5) * tau1_scale,
            tau1=0.5 * k ** (-0.7) * tau1_scale,
            tau2=5.0 * k ** (-0.7) * tau1_scale,
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=100.0 * midi / 60.0, B=0.0, partials=tuple(partials))


def test_monotone_descending_tau1_no_violations():
    # tau1 decreases as midi goes up (lower scale at higher pitches)
    notes = [
        _note(60, tau1_scale=1.0),
        _note(61, tau1_scale=0.9),
        _note(62, tau1_scale=0.8),
        _note(63, tau1_scale=0.7),
    ]
    violations = check_monotonicity(notes, parameters=("tau1",))
    assert violations == []


def test_inverted_tau1_flagged_as_violation():
    # tau1 INCREASES with midi — against physics — should flag at every k
    notes = [
        _note(60, tau1_scale=0.5),
        _note(61, tau1_scale=1.0),
        _note(62, tau1_scale=1.5),
    ]
    violations = check_monotonicity(notes, parameters=("tau1",))
    # Every (k, pair) should be flagged
    assert len(violations) >= 5   # k=1..10 × 2 adjacent pairs = potentially 20
    assert all(v.parameter == "tau1" for v in violations)
    assert all(v.expected_direction == "decreasing" for v in violations)


def test_severity_major_on_long_run():
    # Long run of violations → severity should escalate to "major"
    notes = [
        _note(m, tau1_scale=0.5 + 0.1 * (m - 60))
        for m in range(60, 66)
    ]
    violations = check_monotonicity(
        notes, parameters=("tau1",), run_length_major=3,
    )
    assert any(v.severity == "major" for v in violations)


def test_single_pair_violation_is_minor():
    # One bumpy pair in an otherwise-descending series
    notes = [
        _note(60, tau1_scale=1.0),
        _note(61, tau1_scale=0.9),
        _note(62, tau1_scale=0.95),    # bump up from 0.9 to 0.95
        _note(63, tau1_scale=0.7),
    ]
    violations = check_monotonicity(notes, parameters=("tau1",), run_length_major=3)
    assert all(v.severity == "minor" for v in violations)


def test_violations_per_velocity_scope_independently():
    """Violations at one velocity should NOT cross-contaminate another velocity."""
    notes = [
        # vel=5 monotone, vel=0 violating
        _note(60, vel=5, tau1_scale=1.0),
        _note(61, vel=5, tau1_scale=0.8),
        _note(60, vel=0, tau1_scale=0.5),
        _note(61, vel=0, tau1_scale=1.0),
    ]
    violations = check_monotonicity(notes, parameters=("tau1",))
    assert all(v.velocity == 0 for v in violations)


def test_unknown_parameter_is_silently_skipped():
    notes = [_note(60), _note(61)]
    # Parameter with no entry in EXPECTED_DIRECTION → skipped without raising.
    violations = check_monotonicity(notes, parameters=("some_random_param",))
    assert violations == []


def test_as_dict_is_json_safe():
    import json
    notes = [
        _note(60, tau1_scale=0.5),
        _note(61, tau1_scale=1.0),
    ]
    violations = check_monotonicity(notes, parameters=("tau1",))
    json.dumps([v.as_dict() for v in violations])
