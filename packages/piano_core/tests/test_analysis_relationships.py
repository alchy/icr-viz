"""Tests for keyboard-wide aggregate trends + outlier detection."""

from __future__ import annotations

import math

import pytest

from piano_core.analysis.outlier_detect import detect_outliers
from piano_core.analysis.physical_fit import NoteMathDiag
from piano_core.analysis.relationships import (
    gamma_ratio_stats,
    inharmonicity_trend,
    power_law_distribution,
)


def _diag(midi: int, **overrides) -> NoteMathDiag:
    defaults = dict(
        midi=midi, velocity=5, f0_hz=100.0 * midi / 60.0,
        B_hat=1e-4 * math.exp(0.05 * (midi - 60)),   # typical B growth
        tau1_alpha=0.7,
        tau2_alpha=0.7,
        A0_beta=0.5,
        A0_mu=0.05,
        gamma=5.0,
        fit_rmse={"tau1": 0.05, "A0": 0.1},
        physical_prior_fit_quality=0.9,
    )
    defaults.update(overrides)
    return NoteMathDiag(**defaults)


# ---- inharmonicity trend -----------------------------------------------

def test_inharmonicity_trend_has_high_r_squared_on_synthetic():
    diags = [_diag(m) for m in range(60, 85)]
    trend = inharmonicity_trend(diags)
    assert trend.n == 25
    assert trend.r_squared is not None
    assert trend.r_squared > 0.95    # clean exponential growth fits very well


def test_inharmonicity_trend_empty_returns_nulls():
    trend = inharmonicity_trend([])
    assert trend.n == 0
    assert trend.r_squared is None


def test_inharmonicity_trend_skips_none_values():
    diags = [
        _diag(60, B_hat=None),
        _diag(61, B_hat=2e-4),
        _diag(62, B_hat=3e-4),
        _diag(63, B_hat=4e-4),
    ]
    trend = inharmonicity_trend(diags)
    assert trend.n == 3
    assert trend.median is not None


# ---- power law distribution ---------------------------------------------

def test_power_law_distribution_basic_stats():
    diags = [_diag(m, tau1_alpha=0.6 + 0.01 * (m - 60)) for m in range(60, 75)]
    stats = power_law_distribution(diags, attr="tau1_alpha")
    assert stats.n == 15
    assert stats.median is not None
    # Values range from 0.6 to 0.74 → median around 0.67
    assert 0.6 < stats.median < 0.75


def test_power_law_distribution_handles_missing_attr_gracefully():
    # tau2_alpha = None on every row
    diags = [_diag(m, tau2_alpha=None) for m in range(60, 65)]
    stats = power_law_distribution(diags, attr="tau2_alpha")
    assert stats.n == 0


# ---- gamma ratio stats --------------------------------------------------

def test_gamma_ratio_stats_returns_distribution():
    diags = [_diag(m, gamma=3.0 + 0.1 * (m - 60)) for m in range(60, 70)]
    stats = gamma_ratio_stats(diags)
    assert stats.n == 10
    assert stats.median is not None
    assert stats.min is not None and stats.max is not None
    assert stats.p05 is not None and stats.p95 is not None


def test_gamma_stats_skips_non_positive():
    diags = [
        _diag(60, gamma=None),
        _diag(61, gamma=-1.0),
        _diag(62, gamma=3.2),
    ]
    stats = gamma_ratio_stats(diags)
    assert stats.n == 1
    assert stats.median == pytest.approx(3.2)


# ---- outlier detection --------------------------------------------------

def test_outlier_detection_flags_extreme_B():
    # 10 normal notes, one with B_hat 100x larger
    diags = [_diag(m, B_hat=1e-4) for m in range(60, 70)]
    diags.append(_diag(70, B_hat=1e-2))   # outlier
    outliers = detect_outliers(diags, attributes=("B_hat",))
    assert len(outliers) >= 1
    top = outliers[0]
    assert top.midi == 70
    assert top.attribute == "B_hat"
    assert top.severity in ("warn", "strong")


def test_outlier_detection_skips_when_too_few_samples():
    diags = [_diag(m) for m in range(60, 63)]   # only 3 notes
    outliers = detect_outliers(diags, attributes=("B_hat",))
    assert outliers == []   # below minimum (5) sample count


def test_outlier_detection_sorted_descending_by_z():
    # Use a distribution with non-zero natural spread so MAD doesn't collapse —
    # then both moderate and extreme outliers pass the warn threshold, and we
    # can verify sort order.
    import numpy as np
    rng = np.random.default_rng(0)
    diags = [
        _diag(60 + i, B_hat=float(1e-4 * (1.0 + 0.2 * rng.standard_normal())))
        for i in range(15)
    ]
    diags.append(_diag(80, B_hat=1e-2))     # moderate outlier (~100x baseline)
    diags.append(_diag(81, B_hat=1e-1))     # extreme outlier (~1000x baseline)
    outliers = detect_outliers(diags, attributes=("B_hat",))
    assert len(outliers) >= 2
    assert outliers[0].mad_z >= outliers[1].mad_z


def test_outlier_as_dict_is_json_safe():
    import json
    diags = [_diag(m, B_hat=1e-4) for m in range(60, 70)]
    diags.append(_diag(70, B_hat=1e-2))
    outliers = detect_outliers(diags, attributes=("B_hat",))
    json.dumps([o.as_dict() for o in outliers])
