"""Canonical anchor_interpolate pipeline (math §12.2).

Given a sequence of partials and (optionally) anchors for a single parameter
on one note, produce a smoothed estimate function and per-k sigma function
that respect all upstream guards.

Pipeline steps:

  1. Collect observations (k, value, fit_quality) from partials;
     promote matching anchors into the observation set with inflated weight.
  2. Log-transform when the parameter naturally lives in log space
     (tau1, tau2, A0).
  3. Fit a penalized B-spline with GCV-selected lambda (or lambda override).
  4. Monotonicity fallback: if the fit violates expected monotonicity (param
     should decrease with k) we replace it with a PCHIP interpolant through
     the raw (or log-transformed) data.
  5. Clamp output to PARAM_BOUNDS.
  6. Bootstrap sigma over the fit function; compose with analytical variance
     if deterministic estimate is preferred.

Hard anchors (weight >= 0.999) are handled by global refit per
`virtual_test §7.1 #2`: anchor values participate in the weighted LS directly
with weight >> 1, rather than running a separate per-k Kalman update.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Callable, Sequence

import numpy as np

from piano_core.completion.edge_cases import (
    EdgeCaseWarning,
    aggregate_warnings,
    check_coverage_gap,
    check_extrapolation,
    check_multi_anchor_conflict,
)
from piano_core.completion.uncertainty import (
    bootstrap_uncertainty,
)
from piano_core.constants import MATH_PARAMS, SIGMA_0
from piano_core.models.partial import Partial
from piano_core.splines.bounds import PARAM_BOUNDS, clamp_to_bounds
from piano_core.splines.log_space import log_space_spline
from piano_core.splines.pchip import monotone_cubic_spline
from piano_core.splines.penalized import fit_penalized_bspline


logger = logging.getLogger(__name__)


# Parameters for which we log-transform before fitting (physics spans orders of magnitude).
_LOG_SPACE_PARAMETERS = frozenset({"tau1", "tau2", "A0"})

# Parameters expected to decrease monotonically in k across the spectrum.
_MONOTONE_DECREASING = frozenset({"tau1", "tau2", "A0"})

# Hard-anchor threshold: weights >= this effectively pin the spline through the anchor.
_HARD_ANCHOR_WEIGHT = 0.999


# ---------------------------------------------------------------------------
# Lightweight Anchor input (decoupled from the full i2.11 Anchor dataclass so
# anchor_interpolate stays usable before persistence code lands).
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class AnchorObservation:
    """Minimal anchor shape used by anchor_interpolate.

    `weight` in [0, 1] controls stiffness: 0 = prior only, 1 = hard pin.
    """

    k: int
    parameter: str
    value: float
    weight: float = 0.5
    sigma: float | None = None


# ---------------------------------------------------------------------------
# Result type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class InterpolationResult:
    estimate: Callable[[float | np.ndarray], float | np.ndarray]
    sigma: Callable[[float | np.ndarray], float | np.ndarray]
    lambda_used: float
    used_pchip: bool
    coverage: tuple[int, int]
    n_observations: int
    n_anchors_used: int
    warnings: tuple[str, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# Pipeline
# ---------------------------------------------------------------------------

def anchor_interpolate(
    partials: Sequence[Partial],
    anchors: Sequence[AnchorObservation],
    parameter: str,
    *,
    prior_weight: float = 0.3,
    smoothing: float | None = None,
    random_seed: int = 0,
    k_range: tuple[int, int] | None = None,
    n_boot: int = 200,
) -> InterpolationResult:
    """Run the unified anchor-aware completion pipeline for one parameter.

    Parameters
    ----------
    partials : observations from one note (one midi, one velocity).
    anchors : user-supplied overrides; only those matching `parameter` are used.
    parameter : one of MATH_PARAMS.
    prior_weight : soft-anchor weight when `weight` is unset on AnchorObservation.
    smoothing : explicit lambda for penalized fit. `None` triggers GCV.
    random_seed : propagates to bootstrap sigma.
    k_range : explicit (k_min, k_max) for the fit's x_range. Default: data range.
    n_boot : bootstrap iterations for sigma.
    """
    if parameter not in MATH_PARAMS:
        raise ValueError(f"unknown parameter {parameter!r}; allowed: {MATH_PARAMS}")

    warnings_collected: list[list[EdgeCaseWarning]] = []

    # --- 1. Collect observations ---------------------------------------
    obs_k: list[int] = []
    obs_y: list[float] = []
    obs_w: list[float] = []
    # We weight by fit_quality (bounded to [0.01, 1.0]) to avoid zero weights
    # that degenerate the LS solve.
    for p in partials:
        val = _extract_parameter_value(p, parameter)
        if val is None:
            continue
        obs_k.append(p.k)
        obs_y.append(val)
        obs_w.append(max(0.01, min(float(p.fit_quality), 1.0)))

    # --- Anchor conflict check before merging ---
    anchors_for_param = [a for a in anchors if a.parameter == parameter]
    by_k: dict[int, list[AnchorObservation]] = {}
    for a in anchors_for_param:
        by_k.setdefault(a.k, []).append(a)
    for k, group in by_k.items():
        if len(group) > 1:
            warnings_collected.append(
                check_multi_anchor_conflict(
                    [(a.value, a.sigma) for a in group],
                    parameter=parameter,
                )
            )

    # --- Merge anchors into observation set (soft → inflated weight, hard → very large weight) ---
    n_hard_anchors = 0
    for a in anchors_for_param:
        effective_weight = _anchor_weight_to_ls_weight(a.weight or prior_weight)
        if a.weight >= _HARD_ANCHOR_WEIGHT:
            n_hard_anchors += 1
        obs_k.append(a.k)
        obs_y.append(a.value)
        obs_w.append(effective_weight)

    if len(obs_k) < 3:
        # Can't fit — return a constant-at-mean estimator
        if not obs_y:
            warnings_collected.append([
                EdgeCaseWarning(kind="no_observations", detail=f"{parameter}: no data"),
            ])
            constant = 0.0
        else:
            constant = float(np.mean(obs_y))

        def _const_estimate(x):
            xx = np.asarray(x, dtype=float)
            out = np.full_like(xx, constant)
            return float(out.item()) if xx.ndim == 0 else out

        def _const_sigma(x):
            xx = np.asarray(x, dtype=float)
            fallback = SIGMA_0.get(parameter, 1.0)
            out = np.full_like(xx, float(fallback))
            return float(out.item()) if xx.ndim == 0 else out

        return InterpolationResult(
            estimate=_const_estimate,
            sigma=_const_sigma,
            lambda_used=0.0,
            used_pchip=False,
            coverage=(int(np.min(obs_k)) if obs_k else 0, int(np.max(obs_k)) if obs_k else 0),
            n_observations=len(obs_k),
            n_anchors_used=len(anchors_for_param),
            warnings=aggregate_warnings(*warnings_collected),
        )

    # Sort by k, averaging duplicates (multiple anchors on same k collapsed via weighted mean)
    k_arr, y_arr, w_arr = _collapse_duplicate_ks(obs_k, obs_y, obs_w)

    # --- 2. Optional log transform ------------------------------------
    log_transformed = parameter in _LOG_SPACE_PARAMETERS and np.all(y_arr > 0)
    y_fit = np.log(y_arr) if log_transformed else y_arr.copy()

    # --- 3. Penalized fit ----------------------------------------------
    if k_range is not None:
        x_min, x_max = int(k_range[0]), int(k_range[1])
    else:
        x_min = int(k_arr.min())
        x_max = int(k_arr.max())

    # scipy BSpline needs x_min < x_max; if only one unique k made it through, bail to constant.
    if x_max <= x_min:
        mean_y = float(np.average(y_arr, weights=w_arr))
        def _degen_estimate(x):
            xx = np.asarray(x, dtype=float)
            out = np.full_like(xx, mean_y)
            return float(out.item()) if xx.ndim == 0 else out
        return InterpolationResult(
            estimate=_degen_estimate,
            sigma=lambda x: np.full_like(np.asarray(x, dtype=float), SIGMA_0.get(parameter, 1.0)),
            lambda_used=0.0,
            used_pchip=False,
            coverage=(x_min, x_max),
            n_observations=int(k_arr.size),
            n_anchors_used=len(anchors_for_param),
            warnings=aggregate_warnings(*warnings_collected) + ("degenerate: single unique k",),
        )

    # Reduce interior knots when sample count is low.
    n_interior = min(8, max(2, k_arr.size - 3))
    try:
        fit_result = fit_penalized_bspline(
            k_arr.astype(float),
            y_fit,
            weights=w_arr,
            n_interior_knots=n_interior,
            lambda_override=smoothing,
            x_range=(float(x_min), float(x_max)),
        )
    except ValueError as exc:
        warnings_collected.append([EdgeCaseWarning("penalized_fit_failed", str(exc))])
        # PCHIP-only fallback
        return _pchip_only_result(
            k_arr, y_arr, w_arr, parameter, log_transformed,
            n_anchors=len(anchors_for_param),
            warnings_collected=warnings_collected,
            random_seed=random_seed,
            n_boot=n_boot,
            x_min=x_min, x_max=x_max,
        )

    # --- 4. Build primary estimator + monotonicity fallback detection -------
    def primary_estimate(x):
        xx = np.asarray(x, dtype=float)
        raw = fit_result.evaluate(xx)
        if log_transformed:
            raw = np.exp(np.asarray(raw))
        clipped = clamp_to_bounds(raw, parameter)
        return clipped

    used_pchip = False
    estimate_fn = primary_estimate

    if parameter in _MONOTONE_DECREASING:
        test_grid = np.arange(x_min, x_max + 1, dtype=float)
        values = np.asarray(primary_estimate(test_grid))
        # Allow small tolerance to avoid false positives from floating noise.
        tol = 1e-6 * max(1.0, float(np.max(np.abs(values))))
        if np.any(np.diff(values) > tol):
            used_pchip = True
            warnings_collected.append([EdgeCaseWarning(
                kind="monotonicity_fallback",
                detail=f"{parameter}: penalized fit violated decreasing monotonicity; using PCHIP",
            )])
            # PCHIP over the collapsed observation set (in log space if applicable)
            pchip_y = np.log(y_arr) if log_transformed else y_arr
            spline_raw = monotone_cubic_spline(k_arr.astype(float), pchip_y)
            def _pchip_estimate(x):
                xx = np.asarray(x, dtype=float)
                out = spline_raw(xx)
                if log_transformed:
                    out = np.exp(np.asarray(out))
                return clamp_to_bounds(out, parameter)
            estimate_fn = _pchip_estimate

    # --- 5. Bootstrap sigma -------------------------------------------
    grid = np.arange(x_min, x_max + 1, dtype=float)
    sigma_vals = bootstrap_uncertainty(
        k_arr.astype(float), y_arr,
        fit_fn=lambda xb, yb: _fit_plain_pchip_or_penalized(
            xb, yb, log_transformed=log_transformed, parameter=parameter,
            x_min=x_min, x_max=x_max,
        ),
        eval_grid=grid,
        n_boot=n_boot,
        random_seed=random_seed,
    )
    # Floor sigma at the calibrated SIGMA_0 — bootstrap can collapse to zero when
    # data is clean, but downstream Bayesian code needs a non-zero prior.
    fallback = SIGMA_0.get(parameter, 0.0)
    sigma_vals = np.maximum(sigma_vals, fallback * 0.1)
    sigma_spline = monotone_cubic_spline(grid, sigma_vals)

    # --- 6. Edge-case warnings -----------------------------------------
    warnings_collected.append(check_extrapolation(list(k_arr), list(grid)))
    warnings_collected.append(check_coverage_gap(list(k_arr), parameter=parameter))

    logger.info(
        "anchor_interpolate",
        extra={
            "parameter": parameter,
            "n_observations": int(k_arr.size),
            "n_anchors": len(anchors_for_param),
            "lambda_used": fit_result.lambda_used,
            "used_pchip": used_pchip,
            "log_transformed": log_transformed,
            "coverage": (x_min, x_max),
        },
    )

    return InterpolationResult(
        estimate=estimate_fn,
        sigma=sigma_spline,
        lambda_used=fit_result.lambda_used,
        used_pchip=used_pchip,
        coverage=(x_min, x_max),
        n_observations=int(k_arr.size),
        n_anchors_used=len(anchors_for_param),
        warnings=aggregate_warnings(*warnings_collected),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _extract_parameter_value(p: Partial, parameter: str) -> float | None:
    """Return the raw parameter value from a Partial, or None if unavailable."""
    if parameter == "f_coef":
        # f_coef is derived; not stored on Partial. Caller should use f_hz-aware
        # path instead. We return None so anchor_interpolate treats the partial
        # as missing this parameter (anchors still contribute if they target f_coef).
        return None
    if not hasattr(p, parameter):
        return None
    val = getattr(p, parameter)
    if val is None:
        return None
    if isinstance(val, float) and (math.isnan(val) or not math.isfinite(val)):
        return None
    return float(val)


def _anchor_weight_to_ls_weight(w: float) -> float:
    """Map user-facing weight in [0, 1] to a WLS weight.

    w = 0   → 1.0    (same as fit_quality=1.0, non-dominant)
    w = 0.5 → 10.0   (soft anchor — moderate pull)
    w = 1.0 → 1000.0 (hard anchor — essentially pins the spline)
    """
    w_clamped = max(0.0, min(1.0, float(w)))
    return 1.0 + 999.0 * (w_clamped ** 2)


def _collapse_duplicate_ks(
    ks: list[int], ys: list[float], ws: list[float]
) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Group by k; emit weighted mean y and summed weight per unique k."""
    k_arr = np.array(ks, dtype=float)
    y_arr = np.array(ys, dtype=float)
    w_arr = np.array(ws, dtype=float)

    order = np.argsort(k_arr, kind="stable")
    k_sorted = k_arr[order]
    y_sorted = y_arr[order]
    w_sorted = w_arr[order]

    unique_k, inverse = np.unique(k_sorted, return_inverse=True)
    out_y = np.zeros_like(unique_k, dtype=float)
    out_w = np.zeros_like(unique_k, dtype=float)
    for idx in range(unique_k.size):
        mask = inverse == idx
        w_g = w_sorted[mask]
        y_g = y_sorted[mask]
        total_w = float(w_g.sum())
        out_w[idx] = total_w
        if total_w > 0:
            out_y[idx] = float(np.sum(y_g * w_g) / total_w)
        else:
            out_y[idx] = float(np.mean(y_g))
    return unique_k.astype(float), out_y, out_w


def _fit_plain_pchip_or_penalized(
    xb: np.ndarray,
    yb: np.ndarray,
    *,
    log_transformed: bool,
    parameter: str,
    x_min: int,
    x_max: int,
) -> Callable[[np.ndarray | float], np.ndarray | float]:
    """Lightweight fit factory used by the bootstrap pass."""
    if xb.size < 5:
        # Too few for a stable penalized B-spline in this context — use PCHIP.
        spline = log_space_spline(xb, yb) if log_transformed and np.all(yb > 0) else monotone_cubic_spline(xb, yb)
        def _pchip_eval(x):
            xx = np.asarray(x, dtype=float)
            raw = spline(xx)
            if log_transformed and not np.all(yb > 0):
                return clamp_to_bounds(raw, parameter)
            return clamp_to_bounds(raw, parameter)
        return _pchip_eval

    try:
        # Reduce knots to keep the basis well-conditioned for small bootstrap draws.
        n_interior = min(6, max(2, xb.size - 3))
        y_fit = np.log(np.maximum(yb, 1e-15)) if log_transformed else yb
        fit = fit_penalized_bspline(
            xb, y_fit,
            n_interior_knots=n_interior,
            x_range=(float(x_min), float(x_max)),
        )
        def _eval(x):
            xx = np.asarray(x, dtype=float)
            raw = fit.evaluate(xx)
            if log_transformed:
                raw = np.exp(np.asarray(raw))
            return clamp_to_bounds(raw, parameter)
        return _eval
    except ValueError:
        spline = monotone_cubic_spline(xb, yb)
        return lambda x: clamp_to_bounds(spline(np.asarray(x, dtype=float)), parameter)


def _pchip_only_result(
    k_arr: np.ndarray,
    y_arr: np.ndarray,
    w_arr: np.ndarray,
    parameter: str,
    log_transformed: bool,
    *,
    n_anchors: int,
    warnings_collected: list[list[EdgeCaseWarning]],
    random_seed: int,
    n_boot: int,
    x_min: int,
    x_max: int,
) -> InterpolationResult:
    """Fallback path when penalized fit fails — use PCHIP and bootstrap for sigma."""
    spline_raw = (
        log_space_spline(k_arr, y_arr)
        if log_transformed and np.all(y_arr > 0)
        else monotone_cubic_spline(k_arr, y_arr)
    )
    def _estimate(x):
        xx = np.asarray(x, dtype=float)
        raw = spline_raw(xx)
        return clamp_to_bounds(raw, parameter)

    grid = np.arange(x_min, x_max + 1, dtype=float)
    sigma_vals = bootstrap_uncertainty(
        k_arr, y_arr,
        fit_fn=lambda xb, yb: (
            log_space_spline(xb, yb) if log_transformed and np.all(yb > 0) else monotone_cubic_spline(xb, yb)
        ),
        eval_grid=grid,
        n_boot=n_boot,
        random_seed=random_seed,
    )
    fallback = SIGMA_0.get(parameter, 0.0)
    sigma_vals = np.maximum(sigma_vals, fallback * 0.1)
    sigma_spline = monotone_cubic_spline(grid, sigma_vals)

    return InterpolationResult(
        estimate=_estimate,
        sigma=sigma_spline,
        lambda_used=0.0,
        used_pchip=True,
        coverage=(x_min, x_max),
        n_observations=int(k_arr.size),
        n_anchors_used=n_anchors,
        warnings=aggregate_warnings(*warnings_collected),
    )
