"""Tests for bootstrap and analytical uncertainty estimators."""

from __future__ import annotations

import numpy as np
import pytest

from piano_core.completion.uncertainty import (
    analytical_variance_from_penalized_fit,
    bootstrap_sigma_callable,
    bootstrap_uncertainty,
)
from piano_core.splines.penalized import fit_penalized_bspline


# ---- bootstrap -----------------------------------------------------------

def _simple_fit_fn(xb, yb):
    return fit_penalized_bspline(
        xb, yb,
        x_range=(float(np.min(xb)), float(np.max(xb))),
    ).evaluate


def test_bootstrap_is_deterministic_with_seed():
    rng = np.random.default_rng(123)
    x = np.linspace(0, 10, 30)
    y = np.sin(x) + rng.normal(0, 0.1, size=x.size)
    eval_grid = np.linspace(0, 10, 21)
    s1 = bootstrap_uncertainty(x, y, fit_fn=_simple_fit_fn, eval_grid=eval_grid, n_boot=50, random_seed=7)
    s2 = bootstrap_uncertainty(x, y, fit_fn=_simple_fit_fn, eval_grid=eval_grid, n_boot=50, random_seed=7)
    assert np.array_equal(s1, s2)


def test_bootstrap_different_seed_gives_different_result():
    rng = np.random.default_rng(123)
    x = np.linspace(0, 10, 30)
    y = np.sin(x) + rng.normal(0, 0.1, size=x.size)
    eval_grid = np.linspace(0, 10, 11)
    s1 = bootstrap_uncertainty(x, y, fit_fn=_simple_fit_fn, eval_grid=eval_grid, n_boot=40, random_seed=1)
    s2 = bootstrap_uncertainty(x, y, fit_fn=_simple_fit_fn, eval_grid=eval_grid, n_boot=40, random_seed=2)
    assert not np.array_equal(s1, s2)


def test_bootstrap_variance_scales_with_noise():
    """More-noisy data → higher bootstrap sigma."""
    rng = np.random.default_rng(0)
    x = np.linspace(0, 10, 40)
    base = np.sin(x)
    y_clean = base + rng.normal(0, 0.05, size=x.size)
    y_noisy = base + rng.normal(0, 0.5, size=x.size)
    eval_grid = np.linspace(0, 10, 21)
    s_clean = bootstrap_uncertainty(x, y_clean, fit_fn=_simple_fit_fn, eval_grid=eval_grid,
                                    n_boot=60, random_seed=99)
    s_noisy = bootstrap_uncertainty(x, y_noisy, fit_fn=_simple_fit_fn, eval_grid=eval_grid,
                                    n_boot=60, random_seed=99)
    assert float(np.mean(s_noisy)) > float(np.mean(s_clean))


def test_bootstrap_handles_tiny_sample_gracefully():
    """With fewer than 3 samples, bootstrap returns zero sigma without raising."""
    s = bootstrap_uncertainty([0.0, 1.0], [0.0, 1.0],
                              fit_fn=_simple_fit_fn, eval_grid=[0.5],
                              n_boot=10, random_seed=0)
    assert np.all(s == 0.0)


def test_bootstrap_mismatched_x_y_raises():
    with pytest.raises(ValueError):
        bootstrap_uncertainty([0.0, 1.0, 2.0], [0.0, 1.0],
                              fit_fn=_simple_fit_fn, eval_grid=[0.5],
                              n_boot=5, random_seed=0)


# ---- analytical variance -------------------------------------------------

def test_analytical_variance_non_negative():
    x = np.linspace(0, 10, 30)
    y = np.sin(x) + 0.05 * np.random.default_rng(0).standard_normal(30)
    result = fit_penalized_bspline(x, y)
    sigma = analytical_variance_from_penalized_fit(
        result, x, y, eval_grid=np.linspace(0, 10, 21),
    )
    assert np.all(sigma >= 0)


def test_analytical_variance_larger_between_knots_than_at_knots():
    """Between observed samples, variance should grow (less information there)."""
    x = np.arange(0, 11, 2).astype(float)   # sparse knots: 0,2,4,6,8,10
    y = np.sin(x)
    result = fit_penalized_bspline(x, y, n_interior_knots=3)
    # Evaluate at observed x and at midpoints
    at_knots = analytical_variance_from_penalized_fit(result, x, y, eval_grid=x)
    mid = x[:-1] + 1.0
    at_mid = analytical_variance_from_penalized_fit(result, x, y, eval_grid=mid)
    # Average variance at mid points should be at least as large as at knots
    assert float(np.mean(at_mid)) >= float(np.mean(at_knots))


# ---- bootstrap_sigma_callable wrapper ------------------------------------

def test_bootstrap_sigma_callable_is_evaluable():
    rng = np.random.default_rng(4)
    x = np.linspace(0, 5, 50)
    y = np.exp(-x / 2) + rng.normal(0, 0.05, size=x.size)
    sigma = bootstrap_sigma_callable(
        x, y,
        eval_grid=np.linspace(0, 5, 11),
        n_boot=30,
        random_seed=11,
    )
    # Scalar input
    s_mid = sigma(2.5)
    assert np.isfinite(s_mid)
    assert s_mid >= 0
    # Array input
    s_arr = sigma(np.array([0.5, 2.5, 4.5]))
    assert s_arr.shape == (3,)
    assert np.all(s_arr >= 0)
