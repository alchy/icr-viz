"""Tests for penalized B-spline + GCV."""

from __future__ import annotations

import numpy as np
import pytest

from piano_core.splines.penalized import (
    DEFAULT_LAMBDA_GRID,
    PenalizedFitResult,
    fit_penalized_bspline,
)


# ---- basic behaviour ------------------------------------------------------

def test_fit_returns_populated_result():
    x = np.linspace(0, 10, 30)
    y = np.sin(x)
    res = fit_penalized_bspline(x, y)
    assert isinstance(res, PenalizedFitResult)
    assert res.lambda_used > 0
    assert res.gcv > 0
    assert res.rmse >= 0
    assert res.effective_df > 0
    assert len(res.coefficients) > 0


def test_evaluate_matches_smooth_curve_on_noise_free_data():
    x = np.linspace(0, 1, 40)
    y = 2.0 * x + 0.5              # linear data — smoother should reproduce it
    res = fit_penalized_bspline(x, y)
    xi = np.linspace(0, 1, 101)
    yhat = res.evaluate(xi)
    assert yhat.shape == (101,)
    # Fit should be very close to the true linear function
    truth = 2.0 * xi + 0.5
    assert np.max(np.abs(yhat - truth)) < 5e-2, "linear function should fit within 5% tolerance"


def test_fit_smooths_noisy_data_toward_underlying_trend():
    rng = np.random.default_rng(0)
    x = np.linspace(0, 2 * np.pi, 120)
    truth = np.sin(x)
    noisy = truth + rng.normal(0, 0.2, size=x.size)
    res = fit_penalized_bspline(x, noisy)
    yhat = res.evaluate(x)
    # Fit should be much closer to truth than noisy data is
    noise_rmse = float(np.sqrt(np.mean((noisy - truth) ** 2)))
    fit_rmse = float(np.sqrt(np.mean((yhat - truth) ** 2)))
    assert fit_rmse < noise_rmse * 0.5, (
        f"smoother should halve the noise RMSE at minimum; noise={noise_rmse:.3f}, fit={fit_rmse:.3f}"
    )


def test_explicit_lambda_override_skips_gcv():
    x = np.linspace(0, 10, 40)
    y = np.sin(x)
    res = fit_penalized_bspline(x, y, lambda_override=1e-3)
    assert res.lambda_used == pytest.approx(1e-3)


def test_huge_lambda_produces_near_linear_fit():
    """As λ → ∞, the penalty dominates → fit approaches a straight line."""
    x = np.linspace(0, 10, 60)
    y = np.sin(x)
    res = fit_penalized_bspline(x, y, lambda_override=1e8)
    xi = np.linspace(0, 10, 121)
    yhat = res.evaluate(xi)
    # Residuals vs. a linear fit should be small
    A = np.vstack([xi, np.ones_like(xi)]).T
    lin_coef, *_ = np.linalg.lstsq(A, yhat, rcond=None)
    lin_fit = A @ lin_coef
    assert np.max(np.abs(yhat - lin_fit)) < 5e-2


def test_tiny_lambda_interpolates_closely():
    """As λ → 0, fit should closely follow the data."""
    x = np.linspace(0, 1, 15)
    y = np.cos(2 * x)
    res = fit_penalized_bspline(x, y, lambda_override=1e-10)
    yhat = res.evaluate(x)
    assert res.rmse < 1e-3


# ---- determinism ---------------------------------------------------------

def test_gcv_tie_breaker_is_deterministic():
    """Two back-to-back fits on identical data must produce identical lambda_used."""
    x = np.linspace(0, 10, 50)
    y = np.sin(x) + np.cos(3 * x)
    res1 = fit_penalized_bspline(x, y)
    res2 = fit_penalized_bspline(x, y)
    assert res1.lambda_used == res2.lambda_used
    assert res1.gcv == res2.gcv
    assert np.array_equal(res1.coefficients, res2.coefficients)


# ---- input validation ----------------------------------------------------

def test_mismatched_x_y_raises():
    with pytest.raises(ValueError):
        fit_penalized_bspline([0, 1, 2], [0, 1])


def test_too_few_samples_raises():
    with pytest.raises(ValueError):
        fit_penalized_bspline([0, 1, 2], [0, 1, 2])   # order 3 needs 5+


def test_negative_weights_raise():
    x = np.linspace(0, 1, 10)
    y = x ** 2
    with pytest.raises(ValueError):
        fit_penalized_bspline(x, y, weights=np.array([1.0] * 9 + [-1.0]))


# ---- weighted fits --------------------------------------------------------

def test_weights_bias_fit_toward_heavily_weighted_points():
    """Heavily weighting the last point should pull the smoother toward it."""
    x = np.linspace(0, 1, 30)
    y = np.zeros_like(x)
    y[-1] = 1.0          # single non-zero anchor at x=1
    w = np.ones_like(y)
    w[-1] = 1000.0       # dominant weight
    res = fit_penalized_bspline(x, y, weights=w, lambda_override=1e-4)
    yhat_end = res.evaluate(1.0)
    # Should be near 1.0, much closer than the unweighted fit would give
    assert yhat_end > 0.5


# ---- extrapolation --------------------------------------------------------

def test_evaluation_clamps_outside_domain():
    x = np.linspace(0, 1, 20)
    y = np.cos(x)
    res = fit_penalized_bspline(x, y)
    # Outside [0, 1]: clipped to boundary, should be finite
    assert np.isfinite(res.evaluate(-5.0))
    assert np.isfinite(res.evaluate(5.0))


# ---- array shape ---------------------------------------------------------

def test_scalar_input_returns_scalar():
    x = np.linspace(0, 1, 20)
    y = x ** 2
    res = fit_penalized_bspline(x, y)
    out = res.evaluate(0.5)
    assert isinstance(out, float)


def test_default_lambda_grid_spans_reasonable_range():
    assert DEFAULT_LAMBDA_GRID.min() <= 1e-6
    assert DEFAULT_LAMBDA_GRID.max() >= 1e2
