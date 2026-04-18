"""Tests for PARAM_BOUNDS + clamp helpers."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.constants import MATH_PARAMS
from piano_core.splines.bounds import (
    PARAM_BOUNDS,
    clamp_to_bounds,
    is_within_bounds,
)


def test_bounds_exist_for_every_math_param():
    """Every parameter the math layer can address must have a clamp rule."""
    for p in MATH_PARAMS:
        assert p in PARAM_BOUNDS, f"missing bounds entry for {p}"


def test_bounds_are_sane_intervals():
    for name, (lo, hi) in PARAM_BOUNDS.items():
        assert lo < hi, f"{name}: lo ({lo}) must be < hi ({hi})"
        assert math.isfinite(lo) and math.isfinite(hi), f"{name}: non-finite bound"


def test_clamp_scalar_within_bounds_returns_value_unchanged():
    assert clamp_to_bounds(0.5, "a1") == pytest.approx(0.5)


def test_clamp_scalar_above_upper_clipped():
    assert clamp_to_bounds(2.5, "a1") == pytest.approx(1.0)


def test_clamp_scalar_below_lower_clipped():
    assert clamp_to_bounds(-0.3, "a1") == pytest.approx(0.0)


def test_clamp_returns_scalar_for_scalar_input():
    out = clamp_to_bounds(0.7, "a1")
    assert isinstance(out, float)


def test_clamp_array_preserves_shape():
    arr = np.array([-0.5, 0.0, 0.5, 1.0, 2.0])
    out = clamp_to_bounds(arr, "a1")
    assert out.shape == (5,)
    assert np.allclose(out, [0.0, 0.0, 0.5, 1.0, 1.0])


def test_clamp_tau_rejects_zero_exactly():
    """tau1 = 0 is rejected; lower bound is 1e-6 to keep log/exp stable downstream."""
    out = clamp_to_bounds(0.0, "tau1")
    assert out == pytest.approx(1e-6, rel=1e-3)


def test_clamp_f_coef_symmetric_bounds():
    assert clamp_to_bounds(0.7, "f_coef") == pytest.approx(0.5)
    assert clamp_to_bounds(-0.7, "f_coef") == pytest.approx(-0.5)


def test_clamp_unknown_parameter_raises():
    with pytest.raises(KeyError):
        clamp_to_bounds(0.5, "not_a_param")


def test_is_within_bounds_basic():
    assert is_within_bounds(0.5, "a1")
    assert is_within_bounds(0.0, "a1")   # inclusive lo
    assert is_within_bounds(1.0, "a1")   # inclusive hi
    assert not is_within_bounds(1.01, "a1")
    assert not is_within_bounds(-0.01, "a1")
