"""Tests for Phase A — identify_tone."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.models.anchor import Anchor
from piano_core.models.note import Note
from piano_core.models.partial import Partial
from piano_core.operators.tone_identify_and_correct import (
    Source,
    TonalReference,
    identify_tone,
)


def _synthesized_note(
    *, midi: int = 60, vel: int = 5,
    f0: float = 261.6, B: float = 4e-4,
    power_alpha: float = 0.7, A_alpha: float = 0.5,
    k_max: int = 15,
    noise_scale: float = 0.0,
    rng_seed: int = 0,
) -> Note:
    rng = np.random.default_rng(rng_seed)
    partials = []
    for k in range(1, k_max + 1):
        f_hz = k * f0 * math.sqrt(1.0 + B * k * k)
        tau1 = 0.5 * k ** (-power_alpha)
        tau2 = 5.0 * k ** (-power_alpha)
        A0 = 10.0 * k ** (-A_alpha)
        if noise_scale > 0:
            tau1 *= 1.0 + rng.normal(0.0, noise_scale)
            tau2 *= 1.0 + rng.normal(0.0, noise_scale)
            A0 *= 1.0 + rng.normal(0.0, noise_scale)
        partials.append(Partial(
            k=k, f_hz=f_hz, A0=max(A0, 1e-6),
            tau1=max(tau1, 1e-6), tau2=max(tau2, 1e-6),
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=f0, B=B, partials=tuple(partials))


def _source(bank_id: str, note: Note, anchors: tuple[Anchor, ...] = ()) -> Source:
    return Source(bank_id=bank_id, note=note, anchors=anchors)


# ---- basic pipeline ------------------------------------------------------

def test_identify_tone_returns_tonal_reference():
    note_a = _synthesized_note()
    note_b = _synthesized_note(noise_scale=0.05, rng_seed=1)
    ref = identify_tone(60, 5, [_source("a", note_a), _source("b", note_b)])
    assert isinstance(ref, TonalReference)
    assert ref.midi == 60 and ref.velocity == 5
    assert ref.f0_hz == pytest.approx(261.6, rel=1e-6)
    assert "tau1" in ref.estimates
    assert "tau1" in ref.sigmas


def test_identify_tone_provenance_captures_sources():
    note_a = _synthesized_note()
    note_b = _synthesized_note(noise_scale=0.02, rng_seed=2)
    ref = identify_tone(60, 5, [_source("a", note_a), _source("b", note_b)])
    prov = ref.provenance["tau1"]
    assert "a" in prov.source_bank_ids
    assert "b" in prov.source_bank_ids
    assert prov.consensus_method in ("blue", "huber")


def test_identify_tone_switches_to_huber_on_outlier():
    clean = _synthesized_note()
    clean2 = _synthesized_note(rng_seed=2)
    outlier = _synthesized_note(power_alpha=0.1, rng_seed=5)   # shallow power → very different curve
    ref = identify_tone(60, 5, [
        _source("a", clean),
        _source("b", clean2),
        _source("outlier", outlier),
    ])
    # With 3 sources and a clear outlier, the pipeline should route through Huber
    assert ref.provenance["tau1"].consensus_method == "huber"


def test_identify_tone_quality_score_is_bounded():
    ref = identify_tone(60, 5, [_source("a", _synthesized_note())])
    for p, q in ref.quality.items():
        assert 0.0 <= q <= 1.0


def test_empty_sources_returns_degenerate_reference():
    ref = identify_tone(60, 5, [])
    assert any("no sources" in w for w in ref.warnings)
    # Estimates still callable — they just return zeros
    assert float(ref.estimates["tau1"](5.0)) == pytest.approx(0.0)


def test_mismatched_source_note_is_skipped():
    note_wrong = _synthesized_note(midi=61, vel=5)
    note_right = _synthesized_note(midi=60, vel=5)
    ref = identify_tone(60, 5, [_source("wrong", note_wrong), _source("right", note_right)])
    prov = ref.provenance["tau1"]
    assert "wrong" not in prov.source_bank_ids
    assert "right" in prov.source_bank_ids
    assert any("supplied note" in w for w in ref.warnings)


def test_f0_disagreement_emits_warning():
    a = _synthesized_note(f0=261.6)
    b = _synthesized_note(f0=264.0)   # ~1% shift → triggers warning
    ref = identify_tone(60, 5, [_source("a", a), _source("b", b)])
    assert any("f0 disagreement" in w for w in ref.warnings)


# ---- monotonicity + clamp ------------------------------------------------

def test_tau1_estimate_is_monotone_decreasing():
    """Any well-behaved tau1 curve must be non-increasing in k — spec + physics."""
    ref = identify_tone(
        60, 5,
        [_source("a", _synthesized_note()), _source("b", _synthesized_note(rng_seed=1))],
    )
    ks = np.arange(1, 16, dtype=float)
    vals = np.asarray(ref.estimates["tau1"](ks))
    assert np.all(np.diff(vals) <= 1e-9)


def test_a1_stays_within_bounds():
    """a1 is bounded [0, 1] — pipeline must respect that regardless of input noise."""
    note_a = _synthesized_note()
    note_b = _synthesized_note(rng_seed=1)
    ref = identify_tone(60, 5, [_source("a", note_a), _source("b", note_b)])
    ks = np.arange(1, 16, dtype=float)
    vals = np.asarray(ref.estimates["a1"](ks))
    assert np.all((vals >= 0.0) & (vals <= 1.0))


# ---- anchors influence ---------------------------------------------------

def test_hard_anchor_monotone_consistent_changes_estimate():
    """Anchor tau1 DOWN (faster decay than baseline) and disable the prior
    regularization — isolates the direct anchor-pull effect on the consensus
    curve.

    Physical regularization is validated separately by `test_use_physical_prior_toggle_changes_output`;
    here we want to confirm the anchor path works when regularization isn't
    pulling results back toward the power-law baseline.
    """
    note_a = _synthesized_note()
    baseline_ref = identify_tone(60, 5, [_source("a", note_a)], use_physical_prior=False)
    baseline_val = float(baseline_ref.estimates["tau1"](5.0))

    anchor = Anchor(midi=60, velocity=5, k=5, parameter="tau1", value=baseline_val * 0.3, weight=1.0)
    ref = identify_tone(
        60, 5,
        [_source("a", note_a, anchors=(anchor,))],
        use_physical_prior=False,
    )
    est = float(ref.estimates["tau1"](5.0))
    assert est < baseline_val
    assert est < baseline_val * 0.8     # measurable pull toward anchor
    assert ref.provenance["tau1"].anchor_ids == (anchor.id,)


def test_use_physical_prior_toggle_changes_output():
    """Disabling the prior should produce a less regularised (and often different) curve."""
    note_a = _synthesized_note(noise_scale=0.3, rng_seed=7)
    note_b = _synthesized_note(noise_scale=0.3, rng_seed=8)
    ref_on = identify_tone(60, 5, [_source("a", note_a), _source("b", note_b)], use_physical_prior=True)
    ref_off = identify_tone(60, 5, [_source("a", note_a), _source("b", note_b)], use_physical_prior=False)
    grid = np.arange(1, 16, dtype=float)
    diff = np.abs(ref_on.estimates["tau1"](grid) - ref_off.estimates["tau1"](grid))
    # Regularization should measurably change the output on noisy data
    assert float(np.max(diff)) > 1e-6


# ---- serialization ------------------------------------------------------

def test_summary_dict_is_json_safe():
    import json
    note_a = _synthesized_note()
    ref = identify_tone(60, 5, [_source("a", note_a)])
    payload = ref.as_summary_dict()
    json.dumps(payload)   # must not raise
    assert payload["midi"] == 60
    assert "tau1" in payload["coverage"]
    assert "tau1" in payload["provenance"]
