"""Tests for the unified anchor_interpolate pipeline (math §12.2)."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.completion.anchor_interpolate import (
    AnchorObservation,
    InterpolationResult,
    anchor_interpolate,
)
from piano_core.constants import SIGMA_0
from piano_core.models.partial import Partial


def _synthetic_partials(
    *, power_law_alpha: float = 0.7, A_alpha: float = 0.5, k_max: int = 30,
) -> list[Partial]:
    """Build a synthetic per-partial sequence with physics-shaped decays."""
    partials = []
    for k in range(1, k_max + 1):
        tau1 = 0.5 * k ** (-power_law_alpha)
        tau2 = 5.0 * k ** (-power_law_alpha)
        A0 = 10.0 * k ** (-A_alpha)
        partials.append(Partial(
            k=k, f_hz=100.0 * k, A0=A0, tau1=tau1, tau2=tau2, a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=1.0,
        ))
    return partials


# ---- basic pipeline ------------------------------------------------------

def test_returns_interpolation_result_with_populated_fields():
    partials = _synthetic_partials()
    result = anchor_interpolate(partials, anchors=[], parameter="tau1", random_seed=0)
    assert isinstance(result, InterpolationResult)
    assert result.n_observations > 0
    assert result.coverage == (1, 30)
    assert callable(result.estimate)
    assert callable(result.sigma)


def test_interpolation_approximates_observed_values():
    partials = _synthetic_partials(k_max=20)
    result = anchor_interpolate(partials, anchors=[], parameter="tau1")
    # Fit should approximate observations at each k within the log-space prior
    for p in partials:
        est = float(result.estimate(p.k))
        assert est > 0
        assert abs(math.log(est) - math.log(p.tau1)) < 0.15   # within ~15% log-space


def test_hard_anchor_pulls_fit_toward_value():
    partials = _synthetic_partials()
    # Force k=10 to a much larger tau1 via a hard anchor
    anchor = AnchorObservation(k=10, parameter="tau1", value=5.0, weight=1.0)
    result = anchor_interpolate(partials, anchors=[anchor], parameter="tau1", random_seed=0)
    # Without anchor, tau1(10) ~ 0.5 * 10^(-0.7) ≈ 0.1; anchor drags it dramatically up.
    assert float(result.estimate(10)) > 1.0


def test_soft_anchor_is_compromise_between_data_and_anchor():
    partials = _synthetic_partials()
    anchor = AnchorObservation(k=10, parameter="tau1", value=5.0, weight=0.3)
    result = anchor_interpolate(partials, anchors=[anchor], parameter="tau1")
    # Soft anchor shouldn't fully pin; estimate should be between unmodified fit and anchor
    est = float(result.estimate(10))
    unmodified = 0.5 * 10 ** (-0.7)
    assert unmodified < est < 5.0


# ---- monotonicity fallback -----------------------------------------------

def test_monotone_decreasing_parameter_fallback_activates_on_violation():
    """Inject non-monotone data in tau1 — fit should trigger PCHIP fallback."""
    partials = []
    # Construct a sequence with an upward bump at k=10 that the smoother can't hide
    for k in range(1, 21):
        base = 1.0 / (k ** 0.5)
        if k == 10:
            base = 2.0   # big bump — fit will try to smooth but PCHIP fallback should hit
        partials.append(Partial(
            k=k, f_hz=100 * k, A0=1.0, tau1=base, tau2=base, a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=1.0,
        ))
    result = anchor_interpolate(partials, anchors=[], parameter="tau1")
    # Either the fit is monotone, or PCHIP flag is set
    test_grid = np.arange(1, 21, dtype=float)
    vals = np.asarray(result.estimate(test_grid))
    if result.used_pchip:
        assert any("monotonicity_fallback" in w for w in result.warnings)
    else:
        # Without fallback, values must be non-increasing
        assert np.all(np.diff(vals) <= 1e-6)


# ---- anchor conflict -----------------------------------------------------

def test_two_conflicting_anchors_emit_warning():
    partials = _synthetic_partials(k_max=15)
    a1 = AnchorObservation(k=8, parameter="tau1", value=0.5, weight=0.5)
    a2 = AnchorObservation(k=8, parameter="tau1", value=3.0, weight=0.5)  # 5σ+ off
    result = anchor_interpolate(partials, anchors=[a1, a2], parameter="tau1")
    assert any("conflict" in w for w in result.warnings)


# ---- edge cases ---------------------------------------------------------

def test_too_few_observations_returns_constant_estimate():
    partials = [
        Partial(k=1, f_hz=100.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
        Partial(k=2, f_hz=200.0, A0=0.5, tau1=0.3, tau2=3.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=1.0),
    ]
    result = anchor_interpolate(partials, anchors=[], parameter="tau1")
    # With just 2 observations, the fit falls back to constant-at-mean
    est_3 = float(result.estimate(3))
    est_10 = float(result.estimate(10))
    assert est_3 == pytest.approx(est_10)


def test_no_partials_no_anchors_returns_zero_estimate():
    result = anchor_interpolate([], anchors=[], parameter="tau1")
    assert float(result.estimate(5)) == pytest.approx(0.0)
    assert any("no_observations" in w or "no data" in w for w in result.warnings)


def test_sigma_floor_is_at_least_tenth_of_sigma0():
    """Bootstrap sigma collapses on clean data; pipeline floors it at SIGMA_0[param]/10."""
    partials = _synthetic_partials(k_max=20)
    result = anchor_interpolate(partials, anchors=[], parameter="tau1", random_seed=0)
    floor = SIGMA_0["tau1"] * 0.1
    # Test at every integer k in coverage
    grid = np.arange(result.coverage[0], result.coverage[1] + 1, dtype=float)
    sigmas = np.asarray(result.sigma(grid))
    assert np.all(sigmas >= floor * 0.99)   # tiny tolerance for float


# ---- parameter validation -----------------------------------------------

def test_unknown_parameter_raises():
    with pytest.raises(ValueError):
        anchor_interpolate([], [], parameter="not_a_param")


# ---- f_coef (derived) ---------------------------------------------------

def test_f_coef_parameter_without_partials_data_runs_on_anchors_only():
    """f_coef is derived — partials contribute nothing directly; only explicit anchors matter."""
    anchors = [
        AnchorObservation(k=k, parameter="f_coef", value=0.003 * k, weight=0.5)
        for k in range(1, 11)
    ]
    result = anchor_interpolate(
        [],  # no partials
        anchors=anchors,
        parameter="f_coef",
    )
    assert result.n_observations >= 3
    # At anchored k=5 the estimator should land somewhere near the anchor value
    est_5 = float(result.estimate(5))
    assert abs(est_5 - 0.015) < 0.01


# ---- determinism --------------------------------------------------------

def test_same_seed_yields_identical_sigma():
    partials = _synthetic_partials()
    r1 = anchor_interpolate(partials, anchors=[], parameter="A0", random_seed=7, n_boot=50)
    r2 = anchor_interpolate(partials, anchors=[], parameter="A0", random_seed=7, n_boot=50)
    grid = np.array([1, 5, 10, 15, 20, 25], dtype=float)
    assert np.allclose(np.asarray(r1.sigma(grid)), np.asarray(r2.sigma(grid)))
