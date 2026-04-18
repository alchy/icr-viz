"""Tests for Phase B — decide_action + apply_correction."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.models.partial import Partial
from piano_core.operators.tone_identify_and_correct import Source, identify_tone
from piano_core.operators.tone_identify_and_correct.decision_tree import (
    Action,
    DecisionParams,
    apply_correction,
    decide_action,
)


# ---- decide_action rules --------------------------------------------------

def test_preserve_fundamental_blocks_k1():
    action = decide_action(
        k=1, parameter="tau1", current=1.0,
        reference=5.0, reference_sigma=0.1, reference_quality=1.0,
        current_sigma=0.01, is_anchored_reference=False,
        params=DecisionParams(preserve_fundamental=True),
    )
    assert action.kind == "none"
    assert action.reason == "preserve_fundamental"


def test_preserve_fundamental_disabled_allows_k1_change():
    action = decide_action(
        k=1, parameter="tau1", current=1.0,
        reference=5.0, reference_sigma=0.1, reference_quality=1.0,
        current_sigma=0.01, is_anchored_reference=False,
        params=DecisionParams(preserve_fundamental=False),
    )
    assert action.kind in ("soft_blend", "hard_replace")


def test_hard_anchor_reference_forces_hard_replace():
    """Virtual_test §7.1 #3: even if d < correction_threshold, a hard-anchor reference
    must land as a hard_replace."""
    action = decide_action(
        k=5, parameter="tau1", current=0.3,
        reference=0.29, reference_sigma=1e-9, reference_quality=1.0,
        current_sigma=0.05, is_anchored_reference=True,
        params=DecisionParams(),
    )
    assert action.kind == "hard_replace"
    assert action.reason == "hard_anchor_reference"


def test_missing_partial_fills_when_quality_sufficient():
    action = decide_action(
        k=5, parameter="A0", current=None,
        reference=2.0, reference_sigma=0.1, reference_quality=0.8,
        current_sigma=None, is_anchored_reference=False,
        params=DecisionParams(),
    )
    assert action.kind == "fill"
    assert action.new_value == pytest.approx(2.0)


def test_missing_partial_refuses_when_quality_low():
    action = decide_action(
        k=5, parameter="A0", current=None,
        reference=2.0, reference_sigma=0.5, reference_quality=0.1,
        current_sigma=None, is_anchored_reference=False,
        params=DecisionParams(fill_quality_threshold=0.3),
    )
    assert action.kind == "none"
    assert action.reason == "fill_quality_below_threshold"


def test_small_deviation_returns_none():
    action = decide_action(
        k=5, parameter="tau1", current=0.5,
        reference=0.52, reference_sigma=0.1, reference_quality=0.8,
        current_sigma=0.1, is_anchored_reference=False,
        params=DecisionParams(),
    )
    assert action.kind == "none"
    assert "within_noise" in action.reason


def test_moderate_deviation_soft_blends():
    # d ≈ 1.4 — between noise (1.0) and correction (2.5)
    action = decide_action(
        k=5, parameter="tau1", current=0.5, reference=0.3,
        reference_sigma=0.1, reference_quality=0.8,
        current_sigma=0.1, is_anchored_reference=False,
        params=DecisionParams(),
    )
    assert action.kind == "soft_blend"
    assert 0 < (action.blend_weight or 0) <= 1
    assert 0.3 < (action.new_value or 0) < 0.5


def test_strong_deviation_hard_replaces():
    action = decide_action(
        k=5, parameter="tau1", current=5.0, reference=0.3,
        reference_sigma=0.1, reference_quality=0.9,
        current_sigma=0.1, is_anchored_reference=False,
        params=DecisionParams(),
    )
    assert action.kind == "hard_replace"
    assert action.new_value == pytest.approx(0.3)


# ---- apply_correction integration ----------------------------------------

def _synth_note(*, noise_scale: float = 0.0, rng_seed: int = 0) -> list[Partial]:
    rng = np.random.default_rng(rng_seed)
    out = []
    for k in range(1, 11):
        tau = 0.5 * k ** (-0.7)
        a = 10.0 * k ** (-0.5)
        if noise_scale > 0:
            tau *= 1.0 + rng.normal(0.0, noise_scale)
            a *= 1.0 + rng.normal(0.0, noise_scale)
        out.append(Partial(
            k=k, f_hz=100 * k, A0=max(a, 1e-6),
            tau1=max(tau, 1e-6), tau2=5 * max(tau, 1e-6), a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return out


def test_apply_correction_preserves_length_and_logs():
    from piano_core.models.note import Note
    partials = _synth_note()
    note_a = Note(midi=60, vel=5, f0_hz=100.0, B=0.0, partials=tuple(partials))
    note_b = Note(midi=60, vel=5, f0_hz=100.0, B=0.0, partials=tuple(_synth_note(noise_scale=0.1, rng_seed=1)))
    ref = identify_tone(60, 5, [
        Source("a", note_a), Source("b", note_b)
    ])
    outcome = apply_correction(
        partials=partials,
        reference=ref,
        parameters=("tau1", "A0"),
    )
    # Log has one entry per (k, parameter)
    # At k=1 both parameters → 'preserve_fundamental' (2 entries); k=2..10 × 2 params = 18 entries; total 20
    assert len(outcome.log) >= 2 * 10
    # Every partial still present
    assert len(outcome.partials) == len(partials)


def test_apply_correction_skips_k1_under_preserve_fundamental():
    from piano_core.models.note import Note
    partials = _synth_note()
    note = Note(midi=60, vel=5, f0_hz=100.0, B=0.0, partials=tuple(partials))
    ref = identify_tone(60, 5, [Source("a", note)])
    outcome = apply_correction(
        partials=partials, reference=ref, parameters=("tau1",),
    )
    k1_entries = [e for e in outcome.log if e.k == 1 and e.parameter == "tau1"]
    assert all(e.action == "none" for e in k1_entries)
    assert all(e.reason == "preserve_fundamental" for e in k1_entries)
    # The partial at k=1 itself is not modified
    p1 = next(p for p in outcome.partials if p.k == 1)
    orig1 = next(p for p in partials if p.k == 1)
    assert p1.tau1 == orig1.tau1


def test_apply_correction_log_dict_is_json_safe():
    import json
    from piano_core.models.note import Note
    partials = _synth_note()
    note = Note(midi=60, vel=5, f0_hz=100.0, B=0.0, partials=tuple(partials))
    ref = identify_tone(60, 5, [Source("a", note)])
    outcome = apply_correction(partials=partials, reference=ref, parameters=("tau1",))
    for entry in outcome.log[:5]:
        json.dumps(entry.as_dict())


def test_apply_correction_hard_replace_tags_partial_as_derived():
    """When action kind is hard_replace (and not via hard anchor), touched partial gets origin=derived."""
    from piano_core.models.note import Note
    # Build two notes with very different tau1 profiles → consensus disagrees with target hard.
    target = _synth_note()
    # Heavily perturb a middle partial so d is huge
    target[4] = Partial(
        k=5, f_hz=500.0, A0=target[4].A0, tau1=10.0, tau2=target[4].tau2,
        a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
    )
    ref_note_partials = _synth_note(rng_seed=3)
    target_note = Note(midi=60, vel=5, f0_hz=100.0, B=0.0, partials=tuple(target))
    ref_note = Note(midi=60, vel=5, f0_hz=100.0, B=0.0, partials=tuple(ref_note_partials))

    ref = identify_tone(60, 5, [Source("a", ref_note)])
    outcome = apply_correction(
        partials=target, reference=ref, parameters=("tau1",),
        params=DecisionParams(preserve_fundamental=False),
    )
    p5 = next(p for p in outcome.partials if p.k == 5)
    # Either hard_replace or soft_blend should have marked it derived
    assert p5.origin == "derived"
    assert p5.tau1 < 10.0
