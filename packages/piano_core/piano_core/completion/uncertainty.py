"""Uncertainty quantification for smoothed curves.

Two complementary estimators, used by anchor_interpolate and downstream
consensus flows:

  1. `bootstrap_uncertainty` — resample (x, y) pairs with replacement,
     refit the supplied function N times, collect per-evaluation-point
     standard deviation across the ensemble. Deterministic when given a seed.

  2. `analytical_variance_from_penalized_fit` — closed-form prediction variance
     from the penalized B-spline hat matrix. Faster than bootstrap but
     underestimates uncertainty in regions with few samples (the hat matrix
     trace doesn't capture sampling variability).

Both return a callable `sigma(k)` mirroring the shape of the fit callable
(`estimate(k)`), so they plug into InterpolationResult uniformly.
"""

from __future__ import annotations

import logging
import math
from typing import Callable, Sequence

import numpy as np

from piano_core.splines.penalized import (
    PenalizedFitResult,
    _build_basis,
    _fit_for_lambda,
    _second_difference_penalty,
    fit_penalized_bspline,
)


logger = logging.getLogger(__name__)


DEFAULT_N_BOOT = 200


# ---------------------------------------------------------------------------
# Bootstrap
# ---------------------------------------------------------------------------

def bootstrap_uncertainty(
    x: Sequence[float] | np.ndarray,
    y: Sequence[float] | np.ndarray,
    *,
    fit_fn: Callable[[np.ndarray, np.ndarray], Callable[[np.ndarray], np.ndarray]],
    eval_grid: Sequence[float] | np.ndarray,
    n_boot: int = DEFAULT_N_BOOT,
    random_seed: int = 0,
    weights: Sequence[float] | np.ndarray | None = None,
) -> np.ndarray:
    """Bootstrap standard deviation across `n_boot` refits.

    Parameters
    ----------
    x, y : observed data (same length)
    fit_fn : (x, y) -> evaluable callable.  Must accept arrays, return arrays.
             For weighted refits, supply weights separately via `weights` and
             have `fit_fn` ignore them (or replace with a wrapper).
    eval_grid : points at which to estimate variance
    n_boot : number of bootstrap resamples. 200 is the spec default (§6.1 R-5).
    random_seed : seeds numpy.default_rng for determinism (F-4).

    Returns
    -------
    sigmas : array of shape (len(eval_grid),) with per-point sample standard
             deviation across the bootstrap ensemble.
    """
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    grid = np.asarray(eval_grid, dtype=float)
    if x_arr.size != y_arr.size:
        raise ValueError("x and y must have equal length")
    if x_arr.size < 3:
        # Too little data to bootstrap meaningfully; degenerate to zero sigma.
        return np.zeros_like(grid)

    rng = np.random.default_rng(random_seed)
    n = x_arr.size
    samples = np.empty((n_boot, grid.size), dtype=float)

    for b in range(n_boot):
        idx = rng.integers(0, n, size=n)
        xb, yb = x_arr[idx], y_arr[idx]
        # Many fit_fn implementations (ours included) require strictly increasing
        # x — sort and deduplicate resampled data before fitting.
        order = np.argsort(xb, kind="stable")
        xb, yb = xb[order], yb[order]
        # Deduplicate ties (strict-increase requirement) by averaging their y values.
        if np.any(np.diff(xb) == 0):
            unique_x, inverse = np.unique(xb, return_inverse=True)
            counts = np.bincount(inverse)
            sums = np.bincount(inverse, weights=yb)
            yb = sums / counts
            xb = unique_x
        if xb.size < 3:
            # Resample collapsed too far; treat as no contribution
            samples[b] = np.nan
            continue
        fit = fit_fn(xb, yb)
        samples[b] = fit(grid)

    # Ignore bootstrap runs that failed (NaN rows) when computing std.
    sigmas = np.nanstd(samples, axis=0, ddof=1)
    sigmas = np.where(np.isfinite(sigmas), sigmas, 0.0)
    logger.debug(
        "uncertainty.bootstrap",
        extra={
            "n_samples": n,
            "n_boot": n_boot,
            "grid_size": grid.size,
            "sigma_mean": float(np.mean(sigmas)),
            "seed": random_seed,
        },
    )
    return sigmas


# ---------------------------------------------------------------------------
# Analytical variance from penalized B-spline fit
# ---------------------------------------------------------------------------

def analytical_variance_from_penalized_fit(
    result: PenalizedFitResult,
    x_observed: Sequence[float] | np.ndarray,
    y_observed: Sequence[float] | np.ndarray,
    *,
    eval_grid: Sequence[float] | np.ndarray,
    weights: Sequence[float] | np.ndarray | None = None,
) -> np.ndarray:
    """Per-point standard deviation from the penalized-spline covariance.

    Uses the canonical P-spline variance:
        cov(c) = sigma^2 * (B'WB + lambda P)^{-1} B' W B (B'WB + lambda P)^{-1}
        var(y_hat(x)) = b(x) cov(c) b(x)^T
    where sigma^2 = RSS / (n - eff_df).
    """
    x_arr = np.asarray(x_observed, dtype=float)
    y_arr = np.asarray(y_observed, dtype=float)
    grid = np.asarray(eval_grid, dtype=float)

    if weights is None:
        w = np.ones_like(y_arr)
    else:
        w = np.asarray(weights, dtype=float)

    # Reconstruct design matrix at observed x (same basis as the fit).
    order = result.order
    knots = result.knots
    # Interior knots count = len(knots) - 2*(order+1)
    n_interior = len(knots) - 2 * (order + 1)
    # x_min / x_max = boundary-multiple knots
    x_min = float(knots[0])
    x_max = float(knots[-1])
    B_obs, _ = _build_basis(x_arr, x_min=x_min, x_max=x_max, n_interior=n_interior, order=order)
    # Design matrix at evaluation grid
    grid_clipped = np.clip(grid, x_min, x_max)
    B_grid, _ = _build_basis(grid_clipped, x_min=x_min, x_max=x_max, n_interior=n_interior, order=order)

    P = _second_difference_penalty(B_obs.shape[1])
    W = np.diag(w)
    A = B_obs.T @ W @ B_obs + result.lambda_used * P

    try:
        A_inv = np.linalg.inv(A)
    except np.linalg.LinAlgError:
        # Pseudoinverse fallback — rarely needed with positive lambda.
        A_inv = np.linalg.pinv(A)

    cov_c = A_inv @ (B_obs.T @ W @ B_obs) @ A_inv

    # sigma^2 via residual sum of squares and effective df
    residual = y_arr - B_obs @ result.coefficients
    rss = float(np.sum(w * residual * residual))
    dof = max(x_arr.size - result.effective_df, 1e-6)
    sigma2 = rss / dof

    # var(y_hat(x)) at each grid point
    variances = sigma2 * np.einsum("ij,jk,ik->i", B_grid, cov_c, B_grid)
    variances = np.maximum(variances, 0.0)
    return np.sqrt(variances)


# ---------------------------------------------------------------------------
# Convenience wrapper for the most common case
# ---------------------------------------------------------------------------

def bootstrap_sigma_callable(
    x: Sequence[float] | np.ndarray,
    y: Sequence[float] | np.ndarray,
    *,
    eval_grid: Sequence[float] | np.ndarray,
    n_boot: int = DEFAULT_N_BOOT,
    random_seed: int = 0,
    order: int = 3,
    n_interior_knots: int = 8,
) -> Callable[[np.ndarray | float], np.ndarray | float]:
    """Bootstrap the default penalized-spline fitter and return `sigma(k)` callable.

    Intermediate sigmas are sampled on `eval_grid`, a PCHIP interpolant over
    those samples provides the final callable. This keeps callers free of the
    bootstrap ensemble memory footprint.
    """
    from piano_core.splines.pchip import monotone_cubic_spline

    grid = np.asarray(eval_grid, dtype=float)

    def _fit(xb, yb):
        return fit_penalized_bspline(
            xb, yb,
            order=order,
            n_interior_knots=n_interior_knots,
            x_range=(float(np.min(xb)), float(np.max(xb))),
        ).evaluate

    sigmas = bootstrap_uncertainty(
        x, y,
        fit_fn=_fit,
        eval_grid=grid,
        n_boot=n_boot,
        random_seed=random_seed,
    )
    sigma_spline = monotone_cubic_spline(grid, sigmas)
    return sigma_spline
