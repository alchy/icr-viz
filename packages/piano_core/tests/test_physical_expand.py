"""Tests for physical_expand (math §12.1)."""

from __future__ import annotations

import math

import pytest

from piano_core.completion.physical_expand import (
    EXTRAPOLATION_FIT_QUALITY,
    physical_expand,
)
from piano_core.models.partial import Partial


def _synth_partials(
    *,
    ks: list[int],
    f0: float = 261.6,
    B: float = 5e-4,
    power_alpha: float = 0.7,
    A_alpha: float = 0.5,
) -> list[Partial]:
    """Generate ideal partials at the given k indices."""
    out = []
    for k in ks:
        f_hz = k * f0 * math.sqrt(1.0 + B * k * k)
        out.append(Partial(
            k=k,
            f_hz=f_hz,
            A0=10.0 * k ** (-A_alpha),
            tau1=0.5 * k ** (-power_alpha),
            tau2=5.0 * k ** (-power_alpha),
            a1=0.9,
            beat_hz=0.0,
            phi=0.0,
            fit_quality=1.0,
            origin="measured",
        ))
    return out


# ---- basic behaviour ----------------------------------------------------

def test_expands_to_full_k_max():
    partials = _synth_partials(ks=[1, 2, 3, 5, 8, 13, 21])
    expanded, diag = physical_expand(partials, f0=261.6, k_max=25)
    assert len(expanded) == 25
    assert [p.k for p in expanded] == list(range(1, 26))
    assert diag.n_observed == 7
    assert diag.n_extrapolated == 18


def test_observed_partials_pass_through_unchanged():
    partials = _synth_partials(ks=[1, 5, 10, 15, 20])
    expanded, _ = physical_expand(partials, f0=261.6, k_max=20)
    observed_by_k = {p.k: p for p in partials}
    for p in expanded:
        if p.k in observed_by_k:
            assert p == observed_by_k[p.k]


def test_extrapolated_partials_carry_sentinel_flags():
    partials = _synth_partials(ks=[1, 2, 3])
    expanded, _ = physical_expand(partials, f0=261.6, k_max=10, random_seed=0)
    extra = [p for p in expanded if p.k > 3]
    assert all(p.origin == "extrapolated" for p in extra)
    assert all(p.fit_quality == EXTRAPOLATION_FIT_QUALITY for p in extra)
    assert all(p.beat_hz == 0.0 for p in extra)


def test_extrapolated_frequencies_follow_inharmonicity_model():
    f0 = 220.0
    B = 4e-4
    partials = _synth_partials(ks=[1, 2, 3, 4, 5], f0=f0, B=B)
    expanded, diag = physical_expand(partials, f0=f0, k_max=15)
    assert diag.B_hat == pytest.approx(B, rel=1e-3)

    for p in expanded:
        if p.origin == "extrapolated":
            expected = p.k * f0 * math.sqrt(1.0 + diag.B_hat * p.k * p.k)
            assert p.f_hz == pytest.approx(expected, rel=1e-8)


def test_extrapolated_tau_follows_power_law():
    """Extrapolated tau values should approximate the underlying power law."""
    alpha = 0.7
    partials = _synth_partials(ks=list(range(1, 11)), power_alpha=alpha)
    expanded, _ = physical_expand(partials, f0=261.6, k_max=20)
    for p in expanded:
        if p.origin == "extrapolated":
            expected = 0.5 * p.k ** (-alpha)
            assert p.tau1 == pytest.approx(expected, rel=0.05)


# ---- determinism --------------------------------------------------------

def test_same_seed_produces_identical_phase():
    partials = _synth_partials(ks=[1, 2, 3])
    e1, _ = physical_expand(partials, f0=261.6, k_max=10, random_seed=42)
    e2, _ = physical_expand(partials, f0=261.6, k_max=10, random_seed=42)
    for a, b in zip(e1, e2):
        assert a.phi == b.phi


def test_different_seed_produces_different_phase():
    partials = _synth_partials(ks=[1, 2, 3])
    e1, _ = physical_expand(partials, f0=261.6, k_max=10, random_seed=1)
    e2, _ = physical_expand(partials, f0=261.6, k_max=10, random_seed=2)
    diffs = [abs(a.phi - b.phi) > 1e-9 for a, b in zip(e1, e2) if a.origin == "extrapolated"]
    assert any(diffs)


# ---- overrides / edge cases --------------------------------------------

def test_B_override_skips_inharmonicity_fit():
    partials = _synth_partials(ks=[1, 2, 3])
    _, diag = physical_expand(partials, f0=261.6, k_max=10, B_override=0.002)
    assert diag.B_hat == pytest.approx(0.002)
    assert diag.B_var == 0.0


def test_k_max_below_1_raises():
    with pytest.raises(ValueError):
        physical_expand([], f0=261.6, k_max=0)


def test_zero_f0_raises():
    with pytest.raises(ValueError):
        physical_expand([], f0=0.0, k_max=5)


def test_empty_partials_still_produces_k_max_entries():
    """With no observations, priors are degenerate but the function still runs."""
    expanded, diag = physical_expand([], f0=261.6, k_max=5)
    assert len(expanded) == 5
    assert diag.n_observed == 0
    assert diag.n_extrapolated == 5
    for p in expanded:
        assert p.origin == "extrapolated"
        # Frequencies still follow inharmonicity (B defaults to 0 in degenerate case)
        assert p.f_hz > 0


def test_a1_prior_uses_median_of_observed():
    partials = [
        Partial(k=1, f_hz=261.6, A0=1.0, tau1=0.5, tau2=5.0, a1=0.5,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
        Partial(k=2, f_hz=523.2, A0=0.7, tau1=0.4, tau2=4.0, a1=0.7,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
        Partial(k=3, f_hz=784.8, A0=0.4, tau1=0.3, tau2=3.0, a1=0.9,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
    ]
    expanded, _ = physical_expand(partials, f0=261.6, k_max=8)
    a1_values_for_extrapolated = [p.a1 for p in expanded if p.origin == "extrapolated"]
    # Median of [0.5, 0.7, 0.9] = 0.7
    assert all(abs(a - 0.7) < 1e-9 for a in a1_values_for_extrapolated)


def test_partials_outside_k_max_ignored():
    partials = _synth_partials(ks=[1, 2, 30, 60])   # k=30, 60 are beyond our range
    expanded, diag = physical_expand(partials, f0=261.6, k_max=10)
    assert len(expanded) == 10
    # k=30 and k=60 not counted as observed
    assert diag.n_observed == 2
    assert diag.n_extrapolated == 8
