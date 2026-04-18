"""Tests for Fritsch-Carlson monotone cubic spline."""

from __future__ import annotations

import numpy as np
import pytest

from piano_core.splines.pchip import monotone_cubic_spline


# ---- trivial cases --------------------------------------------------------

def test_empty_inputs_return_zero_function():
    f = monotone_cubic_spline([], [])
    assert f(5.0) == pytest.approx(0.0)


def test_single_point_returns_constant():
    f = monotone_cubic_spline([3.0], [7.5])
    assert f(0.0) == pytest.approx(7.5)
    assert f(999.0) == pytest.approx(7.5)


def test_two_points_fall_back_to_linear():
    f = monotone_cubic_spline([0.0, 1.0], [0.0, 2.0])
    assert f(0.0) == pytest.approx(0.0)
    assert f(0.5) == pytest.approx(1.0)
    assert f(1.0) == pytest.approx(2.0)
    # Extrapolation clamps to endpoints
    assert f(-1.0) == pytest.approx(0.0)
    assert f(2.0) == pytest.approx(2.0)


def test_mismatched_lengths_raise():
    with pytest.raises(ValueError):
        monotone_cubic_spline([0.0, 1.0, 2.0], [0.0, 1.0])


def test_non_monotone_xs_raise():
    with pytest.raises(ValueError):
        monotone_cubic_spline([0.0, 2.0, 1.0], [0.0, 1.0, 2.0])


def test_duplicate_xs_raise():
    with pytest.raises(ValueError):
        monotone_cubic_spline([0.0, 1.0, 1.0], [0.0, 1.0, 2.0])


# ---- knot interpolation ---------------------------------------------------

def test_passes_through_knots_exactly():
    xs = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    ys = np.array([0.0, 1.0, 4.0, 9.0, 16.0])  # quadratic-ish
    f = monotone_cubic_spline(xs, ys)
    for x, y in zip(xs, ys):
        assert f(float(x)) == pytest.approx(float(y))


def test_array_input_returns_same_shape():
    xs = np.array([0.0, 1.0, 2.0, 3.0])
    ys = np.array([10.0, 20.0, 30.0, 40.0])
    f = monotone_cubic_spline(xs, ys)
    xi = np.array([0.5, 1.5, 2.5])
    out = f(xi)
    assert out.shape == (3,)
    # Linear because data is linear — all interior values should fall on the line.
    assert np.allclose(out, [15.0, 25.0, 35.0])


# ---- monotonicity preservation -------------------------------------------

def test_monotone_increasing_data_stays_monotone_when_sampled_finely():
    # Classic Fritsch-Carlson test: should not overshoot between knots.
    xs = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    ys = np.array([0.0, 0.1, 0.2, 5.0, 5.1])  # near-flat then jump then near-flat
    f = monotone_cubic_spline(xs, ys)
    sample = np.linspace(0.0, 4.0, 401)
    vals = f(sample)
    # Monotone non-decreasing
    assert np.all(np.diff(vals) >= -1e-12)
    # No overshoot above max knot value
    assert np.max(vals) <= ys.max() + 1e-12
    assert np.min(vals) >= ys.min() - 1e-12


def test_monotone_decreasing_data_preserved():
    xs = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    ys = np.array([10.0, 8.0, 3.0, 2.5, 2.0])
    f = monotone_cubic_spline(xs, ys)
    sample = np.linspace(0.0, 4.0, 201)
    vals = f(sample)
    assert np.all(np.diff(vals) <= 1e-12)
    assert np.max(vals) <= ys.max() + 1e-12
    assert np.min(vals) >= ys.min() - 1e-12


def test_local_extremum_flattens_tangent():
    # Up-down-up creates a local max at the middle knot; PCHIP forces tangent=0 there.
    xs = np.array([0.0, 1.0, 2.0, 3.0, 4.0])
    ys = np.array([0.0, 1.0, 3.0, 1.0, 0.0])
    f = monotone_cubic_spline(xs, ys)
    # Right at the extremum knot, derivative should be near zero — sampling around it
    # shouldn't overshoot the knot value.
    # Probe just above and below x=2 — values must be ≤ y at x=2.
    assert f(1.9) <= 3.0 + 1e-10
    assert f(2.1) <= 3.0 + 1e-10


# ---- extrapolation --------------------------------------------------------

def test_below_range_returns_left_endpoint():
    xs = np.array([1.0, 2.0, 3.0, 4.0])
    ys = np.array([5.0, 6.0, 7.0, 8.0])
    f = monotone_cubic_spline(xs, ys)
    assert f(-100.0) == pytest.approx(5.0)


def test_above_range_returns_right_endpoint():
    xs = np.array([1.0, 2.0, 3.0, 4.0])
    ys = np.array([5.0, 6.0, 7.0, 8.0])
    f = monotone_cubic_spline(xs, ys)
    assert f(100.0) == pytest.approx(8.0)


# ---- parity with legacy JS implementation --------------------------------

def test_interior_extremum_segments_midpoint_equals_mean():
    """For an alternating 0-1-0-1-0 sequence, the two *interior* segments have
    both endpoints set as local extrema → both tangents zero → midpoint is the
    arithmetic mean of the segment endpoints.
    """
    xs = [0.0, 1.0, 2.0, 3.0, 4.0]
    ys = [0.0, 1.0, 0.0, 1.0, 0.0]
    f = monotone_cubic_spline(xs, ys)
    # Only check segments 1..2 and 2..3 (interior, both tangents zero).
    for x0, y0, y1 in zip(xs[1:-2], ys[1:-2], ys[2:-1]):
        midx = x0 + 0.5
        assert f(midx) == pytest.approx((y0 + y1) / 2.0, rel=1e-6)


def test_first_and_last_segments_use_secant_as_boundary_tangent():
    """Fritsch-Carlson sets tangent[0] = secant[0] and tangent[-1] = secant[-1].

    That means boundary segments are NOT midpoint-averages even when the other
    tangent is zero. Locking this behaviour in so future refactors don't silently
    change endpoint derivatives.
    """
    xs = [0.0, 1.0, 2.0, 3.0, 4.0]
    ys = [0.0, 1.0, 0.0, 1.0, 0.0]
    f = monotone_cubic_spline(xs, ys)
    # Hand-computed: first segment has tangent_left = 1, tangent_right = 0 (interior max).
    # At t=0.5, f = 0.5*y0 + 0.125*h*t_left + 0.5*y1 - 0.125*h*t_right
    #            = 0*0.5 + 0.125*1*1 + 1*0.5 + (-0.125*1*0)
    #            = 0.625
    assert f(0.5) == pytest.approx(0.625, rel=1e-6)
    # Last segment: y0=1, y1=0, t_left=0, t_right=-1, h=1.
    # f(mid) = 0.5*1 + 0.125*1*0 + 0.5*0 + (-0.125)*1*(-1) = 0.5 + 0.125 = 0.625
    # Symmetric with first segment in magnitude — both boundary segments carry a bump.
    assert f(3.5) == pytest.approx(0.625, rel=1e-6)
