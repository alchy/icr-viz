"""Tests for math §11 edge-case guards."""

from __future__ import annotations

import numpy as np
import pytest

from piano_core.completion.edge_cases import (
    EdgeCaseWarning,
    aggregate_warnings,
    check_coverage_gap,
    check_extrapolation,
    check_multi_anchor_conflict,
    check_zero_denominator,
)


# ---- extrapolation -------------------------------------------------------

def test_no_extrapolation_warning_when_targets_within_observed():
    w = check_extrapolation(observed_ks=[1, 2, 3, 4, 5], target_ks=[2, 3, 4])
    assert w == []


def test_extrapolation_warning_fires_beyond_span():
    # Observed span 10, 2x threshold = 20 — target at 35 should flag.
    w = check_extrapolation(observed_ks=[1, 5, 10], target_ks=[35, 5])
    assert len(w) == 1
    assert w[0].kind == "extreme_extrapolation"
    assert "35" in w[0].detail


def test_extrapolation_threshold_configurable():
    w_strict = check_extrapolation([1, 5], [8], max_ratio=0.5)
    w_lenient = check_extrapolation([1, 5], [8], max_ratio=5.0)
    assert len(w_strict) == 1
    assert len(w_lenient) == 0


def test_extrapolation_empty_inputs_no_warning():
    assert check_extrapolation([], [1, 2]) == []
    assert check_extrapolation([1, 2], []) == []


# ---- multi-anchor conflict ----------------------------------------------

def test_no_conflict_when_anchors_agree():
    # tau1 SIGMA_0 = 0.174; two values differ by 0.05 < 3*sigma
    w = check_multi_anchor_conflict(
        [(0.50, None), (0.55, None)],
        parameter="tau1",
    )
    assert w == []


def test_conflict_fires_beyond_threshold():
    # tau1 SIGMA_0 = 0.174; 3*sigma = 0.522, combined 3*sqrt(2)*sigma ≈ 0.738.
    # 1.5 difference = ~6+ sigmas of combined.
    w = check_multi_anchor_conflict(
        [(0.50, None), (2.00, None)],
        parameter="tau1",
    )
    assert len(w) == 1
    assert w[0].kind == "multiple_anchor_conflict"


def test_conflict_uses_per_anchor_sigma_when_given():
    # With tight explicit sigma, smaller value differences count as conflict.
    w = check_multi_anchor_conflict(
        [(0.50, 0.01), (0.55, 0.01)],  # 5 sigma separation
        parameter="tau1",
    )
    assert len(w) == 1


def test_conflict_unknown_parameter_is_silent():
    """No SIGMA_0 entry → skip guard silently rather than raise."""
    w = check_multi_anchor_conflict([(1.0, None), (100.0, None)], parameter="unknown")
    assert w == []


def test_conflict_single_anchor_no_warning():
    w = check_multi_anchor_conflict([(1.0, None)], parameter="tau1")
    assert w == []


# ---- zero denominator ----------------------------------------------------

def test_zero_denominator_fires_on_small_values():
    w = check_zero_denominator([1.0, 1e-25, 2.0], context="test.denom")
    assert len(w) == 1
    assert w[0].kind == "zero_denominator"


def test_zero_denominator_silent_on_healthy_values():
    w = check_zero_denominator([1.0, 2.0, 3.0], context="test.denom")
    assert w == []


# ---- coverage gap --------------------------------------------------------

def test_coverage_gap_no_observations():
    w = check_coverage_gap([], parameter="tau1")
    assert len(w) == 1
    assert w[0].kind == "no_coverage"


def test_coverage_gap_partial_below_and_above():
    w = check_coverage_gap(
        [5, 6, 7, 8], expected_range=(1, 20), parameter="tau2",
    )
    assert len(w) == 1
    assert w[0].kind == "partial_coverage"
    assert "[5, 8]" in w[0].detail
    assert "[1, 20]" in w[0].detail


def test_coverage_gap_full_coverage_no_warning():
    # Dense observations (every k) — expected_range satisfied AND no interior gap > 25% of span
    w = check_coverage_gap(
        list(range(1, 21)), expected_range=(1, 20), parameter="A0",
    )
    assert w == []


def test_coverage_gap_interior_gap_detected():
    # Span 20, largest gap 18 (out of 20) → >25% of span and ≥3 → interior_gap fires
    w = check_coverage_gap([1, 2, 20], parameter="tau1")
    assert len(w) == 1
    assert w[0].kind == "interior_gap"


# ---- aggregator ----------------------------------------------------------

def test_aggregate_warnings_flattens_to_strings():
    a = [EdgeCaseWarning("kindA", "detailA")]
    b = [EdgeCaseWarning("kindB", "detailB1"), EdgeCaseWarning("kindC", "detailC")]
    out = aggregate_warnings(a, b)
    assert out == (
        "kindA: detailA",
        "kindB: detailB1",
        "kindC: detailC",
    )


def test_aggregate_warnings_empty_returns_empty_tuple():
    assert aggregate_warnings() == ()
    assert aggregate_warnings([], []) == ()
