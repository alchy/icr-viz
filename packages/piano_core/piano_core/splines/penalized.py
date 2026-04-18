"""Penalized B-spline smoother with GCV-selected smoothness.

Why this exists next to PCHIP:
  - PCHIP passes through every knot exactly and preserves monotonicity. That
    is great for interpolating *clean* anchor values, but not for smoothing
    noisy per-partial measurements into a trend curve.
  - The math-layer pipeline (i2 §2.2) needs a regularized regression: fit a
    smooth curve with controllable stiffness (lambda), pick lambda via GCV,
    and fall back to PCHIP only when monotonicity is violated downstream.

Design:
  - B-spline basis of fixed order (cubic by default) with `n_knots` equally
    spaced interior knots.
  - Second-difference penalty on basis coefficients — the canonical P-spline
    formulation (Eilers & Marx 1996).
  - GCV grid search over log-spaced lambda values; ties broken by smallest
    lambda (deterministic, aligns with virtual_test §7.3 replay fix).
  - Returns a callable evaluator + diagnostics (lambda_used, rmse, gcv).

Not a dependency of PCHIP — both spline flavors live side by side.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np
from scipy.interpolate import BSpline


logger = logging.getLogger(__name__)


DEFAULT_ORDER = 3        # cubic
DEFAULT_N_INTERIOR = 8   # number of equally-spaced interior knots
DEFAULT_LAMBDA_GRID = np.logspace(-6, 3, 40)


@dataclass(frozen=True)
class PenalizedFitResult:
    """Output of `fit_penalized_bspline`.

    - `evaluate` — callable that accepts a scalar or array and returns the
      smoothed value(s).
    - `coefficients` — fitted B-spline coefficients (useful for downstream
      variance estimation in completion/uncertainty.py).
    - `knots` — full knot vector used by the BSpline basis.
    - `order` — spline order (3 for cubic).
    - `lambda_used` — λ picked by GCV (or supplied explicitly).
    - `gcv` — GCV score at the chosen λ (smaller is better).
    - `rmse` — weighted RMSE of the fit on the input data.
    - `effective_df` — trace of the hat matrix — effective degrees of freedom.
    """

    evaluate: Callable[[float | np.ndarray], float | np.ndarray]
    coefficients: np.ndarray
    knots: np.ndarray
    order: int
    lambda_used: float
    gcv: float
    rmse: float
    effective_df: float


def _build_basis(
    x: np.ndarray,
    *,
    x_min: float,
    x_max: float,
    n_interior: int,
    order: int,
) -> tuple[np.ndarray, np.ndarray]:
    """Build the B-spline basis matrix B (n_samples, n_basis) and knot vector.

    Uses uniformly-spaced interior knots over [x_min, x_max] plus (order+1)-multiple
    boundary knots — the standard clamped-spline form.
    """
    interior = np.linspace(x_min, x_max, n_interior + 2)[1:-1]
    knots = np.concatenate([
        np.full(order + 1, x_min),
        interior,
        np.full(order + 1, x_max),
    ])
    n_basis = len(knots) - order - 1
    B = np.zeros((x.size, n_basis), dtype=float)
    for j in range(n_basis):
        coef = np.zeros(n_basis)
        coef[j] = 1.0
        spline = BSpline(knots, coef, order, extrapolate=False)
        vals = spline(x)
        # Outside-domain points produce NaN under extrapolate=False — clamp to 0.
        B[:, j] = np.where(np.isfinite(vals), vals, 0.0)
    return B, knots


def _second_difference_penalty(n: int) -> np.ndarray:
    """Second-difference penalty matrix D^T D for coefficient differences."""
    D = np.zeros((n - 2, n))
    for i in range(n - 2):
        D[i, i] = 1.0
        D[i, i + 1] = -2.0
        D[i, i + 2] = 1.0
    return D.T @ D


def _fit_for_lambda(
    B: np.ndarray,
    y: np.ndarray,
    w: np.ndarray,
    P: np.ndarray,
    lam: float,
) -> tuple[np.ndarray, float, float, float]:
    """Solve (B'WB + λP) c = B'Wy; return (coefficients, gcv, rmse, effective_df)."""
    W = np.diag(w)
    BtWB = B.T @ W @ B
    BtWy = B.T @ W @ y
    A = BtWB + lam * P

    # Solve — SPD-ish, use Cholesky if possible, fall back to lstsq.
    try:
        c = np.linalg.solve(A, BtWy)
    except np.linalg.LinAlgError:
        c, *_ = np.linalg.lstsq(A, BtWy, rcond=None)

    # Hat matrix trace (effective df) via trace(B · (B'WB + λP)^{-1} · B'W).
    # Avoid materialising the full hat matrix — compute row-by-row via solve.
    try:
        inv_A_BtW = np.linalg.solve(A, B.T @ W)
    except np.linalg.LinAlgError:
        inv_A_BtW, *_ = np.linalg.lstsq(A, B.T @ W, rcond=None)
    # trace(B @ M) = sum(B * M.T) element-wise summed
    hat_trace = float(np.sum(B * inv_A_BtW.T))

    residual = y - B @ c
    rss = float(np.sum(w * residual * residual))
    n = y.size
    denom = max(n - hat_trace, 1e-12)
    gcv = (rss / n) / ((denom / n) ** 2)
    rmse = math.sqrt(rss / max(np.sum(w), 1e-12))
    return c, gcv, rmse, hat_trace


def fit_penalized_bspline(
    x: Sequence[float] | np.ndarray,
    y: Sequence[float] | np.ndarray,
    *,
    weights: Sequence[float] | np.ndarray | None = None,
    order: int = DEFAULT_ORDER,
    n_interior_knots: int = DEFAULT_N_INTERIOR,
    lambdas: Sequence[float] | np.ndarray | None = None,
    x_range: tuple[float, float] | None = None,
    lambda_override: float | None = None,
) -> PenalizedFitResult:
    """Fit a smoothed spline to (x, y) with optional per-sample `weights`.

    Parameters
    ----------
    weights : per-sample inverse-variance weights. Missing → uniform.
    order : spline order (3 = cubic).
    n_interior_knots : number of equally-spaced interior knots.
    lambdas : λ grid for GCV. Default: 40 log-spaced from 1e-6 to 1e3.
    x_range : (x_min, x_max) for knot placement. Default: data range.
    lambda_override : skip GCV and use this λ directly (useful when the math
        layer has a prior preference).

    Deterministic tie-break: when multiple λ have the same GCV score to within
    float precision, the *smallest* is picked — matches the spec's replay
    stability requirement.
    """
    x_arr = np.asarray(x, dtype=float)
    y_arr = np.asarray(y, dtype=float)
    if x_arr.size != y_arr.size:
        raise ValueError("x and y must have equal length")
    if x_arr.size < order + 2:
        raise ValueError(
            f"need at least order+2 = {order + 2} samples for a stable fit, got {x_arr.size}"
        )

    w_arr = (
        np.ones_like(y_arr) if weights is None
        else np.asarray(weights, dtype=float)
    )
    if w_arr.size != y_arr.size:
        raise ValueError("weights must match length of x/y")
    if np.any(w_arr < 0) or not np.all(np.isfinite(w_arr)):
        raise ValueError("weights must be non-negative and finite")

    x_min, x_max = x_range if x_range is not None else (float(x_arr.min()), float(x_arr.max()))
    B, knots = _build_basis(
        x_arr, x_min=x_min, x_max=x_max, n_interior=n_interior_knots, order=order,
    )
    P = _second_difference_penalty(B.shape[1])

    if lambda_override is not None:
        lam = float(lambda_override)
        coef, gcv, rmse, eff_df = _fit_for_lambda(B, y_arr, w_arr, P, lam)
    else:
        grid = np.asarray(lambdas if lambdas is not None else DEFAULT_LAMBDA_GRID, dtype=float)
        grid = np.sort(grid)   # ensures deterministic tie-break in favor of smallest lambda
        best = None
        for lam in grid:
            coef, gcv, rmse, eff_df = _fit_for_lambda(B, y_arr, w_arr, P, float(lam))
            if best is None or gcv < best[1] - 1e-14:
                best = (float(lam), gcv, coef, rmse, eff_df)
        assert best is not None
        lam, gcv, coef, rmse, eff_df = best

    spline_final = BSpline(knots, coef, order, extrapolate=False)

    # Cache x_min/x_max for the evaluator (BSpline with extrapolate=False returns NaN outside).
    lo, hi = x_min, x_max

    def _eval(xi):
        xx = np.asarray(xi, dtype=float)
        scalar_input = xx.ndim == 0
        clipped = np.clip(xx, lo, hi)
        out = spline_final(clipped)
        # Replace any residual NaN (shouldn't happen post-clip) with boundary values.
        out = np.where(np.isfinite(out), out, 0.0)
        return out.item() if scalar_input else out

    logger.debug(
        "spline.penalized.fit",
        extra={
            "n_samples": int(x_arr.size),
            "n_basis": int(B.shape[1]),
            "lambda_used": lam,
            "gcv": gcv,
            "rmse": rmse,
            "effective_df": eff_df,
        },
    )
    return PenalizedFitResult(
        evaluate=_eval,
        coefficients=coef,
        knots=knots,
        order=order,
        lambda_used=float(lam),
        gcv=float(gcv),
        rmse=float(rmse),
        effective_df=float(eff_df),
    )
