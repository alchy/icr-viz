"""Tests for BLUE and Huber consensus fusion."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.completion.consensus import (
    ConsensusResult,
    blue_consensus,
    has_outlier,
    huber_consensus,
)


# ---- helpers -------------------------------------------------------------

def _const_source(value: float, sigma: float):
    """Build a (estimate, sigma) pair that returns constants across any k."""
    def _theta(x):
        xx = np.asarray(x, dtype=float)
        return np.full_like(xx, value) if xx.shape else float(value)
    def _sigma(x):
        xx = np.asarray(x, dtype=float)
        return np.full_like(xx, sigma) if xx.shape else float(sigma)
    return _theta, _sigma


# ---- BLUE ----------------------------------------------------------------

def test_blue_with_identical_sources_returns_same_value():
    src = [_const_source(5.0, 1.0) for _ in range(4)]
    result = blue_consensus(src, k_grid=np.arange(1, 6))
    assert isinstance(result, ConsensusResult)
    assert np.allclose(result.values, 5.0)
    # sigma shrinks by 1/sqrt(N)
    assert np.allclose(result.sigmas, 1.0 / math.sqrt(4))


def test_blue_weights_by_inverse_variance():
    # Two sources, one much more precise — result leans to the precise one.
    src = [
        _const_source(10.0, 10.0),   # imprecise
        _const_source(20.0, 1.0),    # precise
    ]
    result = blue_consensus(src, k_grid=[1.0])
    # Weight ratio 1:100 → consensus close to 20
    assert float(result.values[0]) == pytest.approx(
        (10.0 / 100 + 20.0 / 1) / (1 / 100 + 1 / 1),
        rel=1e-9,
    )


def test_blue_empty_source_list_returns_zeros_with_warning():
    result = blue_consensus([], k_grid=[1.0, 2.0])
    assert result.n_sources == 0
    assert np.allclose(result.values, 0.0)
    assert any("no sources" in w for w in result.warnings)


def test_blue_emits_cross_source_divergence_warning():
    # Three sources with tight sigmas but wildly different values
    src = [
        _const_source(0.0, 0.1),
        _const_source(10.0, 0.1),
        _const_source(20.0, 0.1),
    ]
    result = blue_consensus(src, k_grid=[1.0])
    assert any("cross-source variance" in w for w in result.warnings)


# ---- Huber ---------------------------------------------------------------

def test_huber_falls_back_to_blue_when_fewer_than_three_sources():
    src = [_const_source(5.0, 1.0), _const_source(6.0, 1.0)]
    result = huber_consensus(src, k_grid=[1.0])
    assert result.method == "huber"
    assert any("fell back to BLUE" in w for w in result.warnings)


def test_huber_down_weights_outlier_source():
    # 4 sources in tight cluster, 1 outlier far away
    src = [
        _const_source(5.0, 0.5),
        _const_source(5.1, 0.5),
        _const_source(4.9, 0.5),
        _const_source(5.05, 0.5),
        _const_source(50.0, 0.5),  # outlier, 100+ sigma away
    ]
    result_blue = blue_consensus(src, k_grid=[1.0])
    result_huber = huber_consensus(src, k_grid=[1.0])
    # Huber should be much closer to 5 than BLUE
    assert abs(float(result_huber.values[0]) - 5.0) < abs(float(result_blue.values[0]) - 5.0)
    assert any("down-weighted" in w for w in result_huber.warnings)


def test_huber_tight_cluster_equals_blue_behaviour():
    # No outlier — Huber should behave like BLUE
    src = [_const_source(v, 1.0) for v in (4.95, 5.0, 5.05, 5.1, 4.9)]
    blue = blue_consensus(src, k_grid=[1.0])
    huber = huber_consensus(src, k_grid=[1.0])
    assert abs(float(blue.values[0]) - float(huber.values[0])) < 1e-6


# ---- has_outlier predicate ----------------------------------------------

def test_has_outlier_detects_deviant_source():
    src = [
        _const_source(5.0, 0.5),
        _const_source(5.1, 0.5),
        _const_source(4.9, 0.5),
        _const_source(100.0, 0.5),
    ]
    assert has_outlier(src, k_grid=[1.0])


def test_has_outlier_says_no_when_all_sources_identical():
    """MAD-relative: identical values produce MAD=0 (clamped to floor); no source can then
    be > delta_factor MADs away from the median. The screen returns False."""
    src = [_const_source(5.0, 0.5) for _ in range(5)]
    # Perfectly identical → scale = 1.4826 * 1e-12, deviations all zero → |d| = 0 < delta_factor
    assert not has_outlier(src, k_grid=[1.0])


def test_has_outlier_false_when_fewer_than_three_sources():
    src = [_const_source(5.0, 0.5), _const_source(100.0, 0.5)]
    assert not has_outlier(src, k_grid=[1.0])


# ---- vector-valued sources ----------------------------------------------

def test_blue_handles_per_k_varying_sources():
    """Sources that vary with k should give a per-k consensus."""

    def linear_source(slope: float, intercept: float, sigma: float):
        def _theta(x):
            xx = np.asarray(x, dtype=float)
            return slope * xx + intercept
        def _sigma(x):
            xx = np.asarray(x, dtype=float)
            return np.full_like(xx, sigma)
        return _theta, _sigma

    src = [
        linear_source(1.0, 0.0, 1.0),
        linear_source(1.05, 0.0, 1.0),
        linear_source(0.95, 0.0, 1.0),
    ]
    grid = np.array([1.0, 5.0, 10.0])
    result = blue_consensus(src, k_grid=grid)
    # Values should be close to grid * 1.0
    assert np.allclose(result.values, grid, rtol=0.1)


def test_sigma_floor_prevents_division_blowup():
    """Source with sigma=0 shouldn't produce inf weights."""
    def zero_sigma_source(value):
        def _theta(x):
            xx = np.asarray(x, dtype=float)
            return np.full_like(xx, value)
        def _sigma(x):
            xx = np.asarray(x, dtype=float)
            return np.zeros_like(xx)
        return _theta, _sigma

    src = [zero_sigma_source(5.0), _const_source(6.0, 1.0)]
    result = blue_consensus(src, k_grid=[1.0], sigma_floor=0.01)
    # Zero-sigma source dominates via tight floor (sigma=0.01 → weight 10000)
    assert float(result.values[0]) == pytest.approx(5.0, abs=0.01)
    assert np.isfinite(result.sigmas[0])
