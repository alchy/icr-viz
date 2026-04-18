"""Tests for log-space spline wrapper."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.splines.log_space import log_space_spline


def test_passes_through_positive_knots():
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [0.01, 0.1, 1.0, 10.0]   # 4 orders of magnitude
    f = log_space_spline(xs, ys)
    for x, y in zip(xs, ys):
        assert f(x) == pytest.approx(y, rel=1e-10)


def test_exponential_growth_interpolation_is_geometric_midpoint():
    """Between knots at y=1 and y=100, log-spline midpoint = sqrt(100) = 10, NOT 50.5.

    Linear PCHIP would give ~50; we want geometric interpolation for decay times
    and amplitudes. (PCHIP in log-space with 2-point fallback = linear in log-space
    = geometric mean at the midpoint.)
    """
    f = log_space_spline([0.0, 1.0], [1.0, 100.0])
    assert f(0.5) == pytest.approx(10.0, rel=1e-6)


def test_negative_values_preserve_sign():
    xs = [0.0, 1.0, 2.0, 3.0]
    ys = [-0.1, -1.0, -10.0, -100.0]   # all negative
    f = log_space_spline(xs, ys)
    for x, y in zip(xs, ys):
        assert f(x) == pytest.approx(y, rel=1e-10)
    # Midpoint is geometric in magnitude, sign stays negative
    assert f(0.5) == pytest.approx(-math.sqrt(0.1), rel=1e-6)


def test_zero_value_handled_without_infinity():
    """A zero knot should not blow up the log; _safe_log clamps at 1e-15."""
    xs = [0.0, 1.0, 2.0]
    ys = [0.0, 1.0, 10.0]
    f = log_space_spline(xs, ys)
    # At x=0 the spline returns value close to 0 (log floor 1e-15 exponentiated)
    assert math.isfinite(f(0.0))
    assert f(0.0) <= 1e-10


def test_array_input_returns_ndarray():
    f = log_space_spline([0.0, 1.0, 2.0, 3.0], [1.0, 2.0, 4.0, 8.0])
    xi = np.array([0.0, 0.5, 1.0, 2.0])
    out = f(xi)
    assert isinstance(out, np.ndarray)
    assert out.shape == (4,)


def test_extrapolation_clamps_like_pchip():
    f = log_space_spline([1.0, 2.0], [10.0, 100.0])
    assert f(-999.0) == pytest.approx(10.0, rel=1e-10)
    assert f(999.0) == pytest.approx(100.0, rel=1e-10)
