"""Edge-case guards from `math_partial_completion.md §11`.

Pure predicate / structured-warning helpers. Each function inspects inputs
and returns a list of `EdgeCaseWarning` values that the caller aggregates
into `ApplyDiagnostics.warnings`. Nothing here logs at WARNING level — these
are domain warnings, not runtime issues.

Categories covered:
  - §11.3 B ≈ 0: negligible inharmonicity signal (already in physical_priors)
  - §11.4 Extreme extrapolation: estimating at k far beyond observed range
  - §11.5 Multi-anchor conflict: two anchors at same (k, parameter) with
          inconsistent values
  - §11.6 Zero denominator: guards against division-by-zero in consensus
  - §11.x Coverage gap: parameter has no observations (empty set)
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from piano_core.constants import SIGMA_0


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Warning structure
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class EdgeCaseWarning:
    """Structured diagnostic — `kind` is a stable string for UI / testing,
    `detail` is a human-readable message."""

    kind: str
    detail: str


# ---------------------------------------------------------------------------
# Guards
# ---------------------------------------------------------------------------

def check_extrapolation(
    observed_ks: Sequence[int] | np.ndarray,
    target_ks: Sequence[int] | np.ndarray,
    *,
    max_ratio: float = 2.0,
) -> list[EdgeCaseWarning]:
    """Flag target_ks that extrapolate far beyond the observed range.

    math §11.4: if |target_k - k_observed_edge| > max_ratio * (k_max - k_min),
    the estimator is extrapolating into a regime with no data support. Caller
    should mark those estimates as `origin="extrapolated"` and inflate sigma.
    """
    obs = np.asarray(observed_ks, dtype=int)
    tgt = np.asarray(target_ks, dtype=int)
    if obs.size == 0 or tgt.size == 0:
        return []

    k_min = int(obs.min())
    k_max = int(obs.max())
    observed_span = max(k_max - k_min, 1)
    threshold = max_ratio * observed_span

    warnings: list[EdgeCaseWarning] = []
    offenders: list[int] = []
    for t in tgt:
        dist_below = k_min - int(t) if t < k_min else 0
        dist_above = int(t) - k_max if t > k_max else 0
        if max(dist_below, dist_above) > threshold:
            offenders.append(int(t))
    if offenders:
        warnings.append(EdgeCaseWarning(
            kind="extreme_extrapolation",
            detail=(
                f"{len(offenders)} target k values lie > {max_ratio}x the observed span "
                f"[{k_min}, {k_max}] away: {offenders[:10]}"
                + ("..." if len(offenders) > 10 else "")
            ),
        ))
    return warnings


def check_multi_anchor_conflict(
    anchors_at_same_k: Iterable[tuple[float, float | None]],
    parameter: str,
    *,
    sigma_multiplier: float = 3.0,
) -> list[EdgeCaseWarning]:
    """Detect two anchors on the same (k, parameter) with disagreeing values.

    Input: iterable of ``(value, sigma_or_None)`` tuples for the same k.

    math §11.5: if |v_i - v_j| > sigma_multiplier * sigma_0[parameter] for any
    pair, the operator must either raise or surface a conflict for the user to
    resolve. We emit a warning; the caller decides whether to raise.
    """
    pairs = list(anchors_at_same_k)
    if len(pairs) < 2:
        return []

    # Use per-anchor sigma where available; fall back to SIGMA_0 for the parameter.
    fallback_sigma = SIGMA_0.get(parameter)
    if fallback_sigma is None:
        return []   # parameter has no calibrated SIGMA_0 — silent skip

    conflicts: list[str] = []
    for i in range(len(pairs)):
        for j in range(i + 1, len(pairs)):
            v_i, s_i = pairs[i]
            v_j, s_j = pairs[j]
            sigma_i = s_i if s_i is not None else fallback_sigma
            sigma_j = s_j if s_j is not None else fallback_sigma
            effective = math.sqrt(sigma_i * sigma_i + sigma_j * sigma_j)
            if effective <= 0:
                continue
            separation = abs(v_i - v_j) / effective
            if separation > sigma_multiplier:
                conflicts.append(
                    f"|{v_i:.4g} - {v_j:.4g}| = {abs(v_i - v_j):.3g} "
                    f"({separation:.2f} sigma, limit {sigma_multiplier})"
                )
    if conflicts:
        return [EdgeCaseWarning(
            kind="multiple_anchor_conflict",
            detail=(
                f"{parameter}: {len(conflicts)} conflicting anchor pair(s); "
                + "; ".join(conflicts[:3])
                + ("..." if len(conflicts) > 3 else "")
            ),
        )]
    return []


def check_zero_denominator(
    values: Sequence[float] | np.ndarray,
    *,
    context: str,
    eps: float = 1e-20,
) -> list[EdgeCaseWarning]:
    """Flag arrays containing values too small to safely divide by.

    math §11.6: operators that compute ratios (BLUE consensus, f_coef
    normalization) must guard against denominators underflowing to zero.
    """
    arr = np.asarray(values, dtype=float)
    n_bad = int(np.sum(np.abs(arr) < eps))
    if n_bad == 0:
        return []
    return [EdgeCaseWarning(
        kind="zero_denominator",
        detail=f"{context}: {n_bad} value(s) below eps={eps:.1e} would produce division-by-zero",
    )]


def check_coverage_gap(
    observed_ks: Sequence[int] | np.ndarray,
    *,
    expected_range: tuple[int, int] | None = None,
    parameter: str | None = None,
) -> list[EdgeCaseWarning]:
    """Emit a warning if the observed k set is empty or has a large internal gap."""
    obs = np.asarray(observed_ks, dtype=int)
    label = parameter or "parameter"

    if obs.size == 0:
        return [EdgeCaseWarning(
            kind="no_coverage",
            detail=f"{label}: no observations available; fit depends entirely on priors",
        )]

    if expected_range is not None:
        lo, hi = expected_range
        missing_below = lo < obs.min()
        missing_above = hi > obs.max()
        if missing_below or missing_above:
            covered = f"[{int(obs.min())}, {int(obs.max())}]"
            expected = f"[{lo}, {hi}]"
            return [EdgeCaseWarning(
                kind="partial_coverage",
                detail=f"{label}: observed {covered} inside expected {expected}; out-of-range estimates are extrapolations",
            )]
    # Interior gap detection: if the largest step between observations is
    # > 25% of the total span, flag it — consensus will interpolate across
    # that gap with reduced confidence.
    if obs.size >= 3:
        sorted_obs = np.sort(obs)
        gaps = np.diff(sorted_obs)
        span = int(sorted_obs[-1] - sorted_obs[0])
        if span > 0:
            biggest_gap = int(gaps.max())
            if biggest_gap > 0.25 * span and biggest_gap >= 3:
                return [EdgeCaseWarning(
                    kind="interior_gap",
                    detail=f"{label}: largest gap between observed k is {biggest_gap} (of total span {span})",
                )]
    return []


# ---------------------------------------------------------------------------
# Aggregator — convenience for anchor_interpolate
# ---------------------------------------------------------------------------

def aggregate_warnings(*warning_lists: list[EdgeCaseWarning]) -> tuple[str, ...]:
    """Flatten lists of EdgeCaseWarning into the tuple-of-strings form expected
    by `ApplyDiagnostics.warnings`."""
    out: list[str] = []
    for wl in warning_lists:
        for w in wl:
            out.append(f"{w.kind}: {w.detail}")
    return tuple(out)
