"""Fritsch-Carlson monotone cubic Hermite interpolation (PCHIP).

Ported from the battle-tested TypeScript implementation in
``__legacy_code__/src/lib/analysis.ts:288-365`` — same arithmetic, same edge
cases, now callable from Python with a ``(xs, ys) -> callable`` API that
accepts numpy arrays *and* Python scalars.

Guarantees:
  - C1 continuity between knots.
  - Monotonicity preserved where the data is monotone (no overshoot).
  - Two-point fallback to linear interpolation.
  - Constant extrapolation beyond the first/last knot (no runaway).
"""

from __future__ import annotations

import logging
from typing import Callable, Sequence

import numpy as np


logger = logging.getLogger(__name__)


def monotone_cubic_spline(
    xs: Sequence[float] | np.ndarray,
    ys: Sequence[float] | np.ndarray,
) -> Callable[[float | np.ndarray], float | np.ndarray]:
    """Build a Fritsch-Carlson monotone cubic Hermite interpolant over (xs, ys).

    Returns a callable that accepts either scalars or arrays and returns values
    of the same shape.
    """
    x_arr = np.asarray(xs, dtype=float)
    y_arr = np.asarray(ys, dtype=float)
    n = x_arr.size
    if y_arr.size != n:
        raise ValueError(f"xs and ys must have equal length, got {n} vs {y_arr.size}")

    # Knots must be strictly increasing. Raise early — silent sorting would mask user bugs.
    if n >= 2 and np.any(np.diff(x_arr) <= 0):
        raise ValueError("xs must be strictly increasing")

    if n == 0:
        def _empty(_x):
            return np.zeros_like(np.asarray(_x, dtype=float))
        return _empty

    if n == 1:
        const_y = float(y_arr[0])
        def _constant(x):
            xx = np.asarray(x, dtype=float)
            return np.full_like(xx, const_y) if xx.shape else np.float64(const_y)
        return _constant

    if n == 2:
        x0, x1 = float(x_arr[0]), float(x_arr[1])
        y0, y1 = float(y_arr[0]), float(y_arr[1])
        slope = (y1 - y0) / (x1 - x0)
        def _linear(x):
            xx = np.asarray(x, dtype=float)
            clipped = np.clip(xx, x0, x1)
            out = y0 + slope * (clipped - x0)
            return out if xx.shape else np.float64(out)
        return _linear

    # --- Full PCHIP path (n >= 3) --------------------------------------

    deltas = np.diff(x_arr)            # shape (n-1,)
    secants = np.diff(y_arr) / deltas  # shape (n-1,)

    # Fritsch-Carlson tangent initialization.
    tangents = np.empty(n, dtype=float)
    tangents[0] = secants[0]
    tangents[-1] = secants[-1]

    # For interior knots: flat tangent on sign change, harmonic mean otherwise.
    sign_prev = secants[:-1]
    sign_next = secants[1:]
    sign_product = sign_prev * sign_next
    # Harmonic mean only where both secants are nonzero and same sign.
    interior = slice(1, n - 1)
    with np.errstate(divide="ignore", invalid="ignore"):
        harmonic = 2.0 / (1.0 / sign_prev + 1.0 / sign_next)
    tangents[interior] = np.where(sign_product <= 0, 0.0, harmonic)
    # Guard: where either secant was exactly zero the harmonic computation returns
    # NaN/Inf; the sign_product <= 0 branch already covers equal-zero via the <= check,
    # but strict equality on either side still produces non-finite values. Force flat.
    tangents[interior] = np.where(np.isfinite(tangents[interior]), tangents[interior], 0.0)

    # Monotonicity enforcement — clamp tangents where α² + β² > 9.
    # Iterate in a vectorised pass per Fritsch & Carlson (1980).
    # Where a secant is essentially zero, both neighbour tangents must be zero (flat segment).
    zero_secant = np.isclose(secants, 0.0, atol=1e-30)
    # First broadcast the zero flag to both adjacent tangents.
    if np.any(zero_secant):
        idx_zero = np.flatnonzero(zero_secant)
        tangents[idx_zero] = 0.0
        tangents[idx_zero + 1] = 0.0

    nonzero = ~zero_secant
    if np.any(nonzero):
        alpha = tangents[:-1][nonzero] / secants[nonzero]
        beta = tangents[1:][nonzero] / secants[nonzero]
        s = alpha * alpha + beta * beta
        overshoot = s > 9.0
        if np.any(overshoot):
            tau = 3.0 / np.sqrt(s[overshoot])
            # Back into the global arrays
            idx_base = np.flatnonzero(nonzero)[overshoot]
            tangents[idx_base] = tau * alpha[overshoot] * secants[idx_base]
            tangents[idx_base + 1] = tau * beta[overshoot] * secants[idx_base]

    # Precompute bind-state for the closure
    x_lo = x_arr[0]
    x_hi = x_arr[-1]
    y_lo = float(y_arr[0])
    y_hi = float(y_arr[-1])

    def _eval(x):
        xx = np.asarray(x, dtype=float)
        scalar_input = xx.ndim == 0
        xv = np.atleast_1d(xx)

        out = np.empty_like(xv)

        below = xv <= x_lo
        above = xv >= x_hi
        interior_mask = ~(below | above)

        out[below] = y_lo
        out[above] = y_hi

        if np.any(interior_mask):
            xi = xv[interior_mask]
            # searchsorted returns the index of the first knot > xi; the interval start
            # index is (idx - 1). Using 'right' ensures xi == xs[i] lands in interval [i, i+1).
            idx = np.searchsorted(x_arr, xi, side="right") - 1
            idx = np.clip(idx, 0, n - 2)
            h = deltas[idx]
            t = (xi - x_arr[idx]) / h
            t2 = t * t
            t3 = t2 * t
            h00 = 2.0 * t3 - 3.0 * t2 + 1.0
            h10 = t3 - 2.0 * t2 + t
            h01 = -2.0 * t3 + 3.0 * t2
            h11 = t3 - t2
            out[interior_mask] = (
                h00 * y_arr[idx]
                + h10 * h * tangents[idx]
                + h01 * y_arr[idx + 1]
                + h11 * h * tangents[idx + 1]
            )

        return out.item() if scalar_input else out

    return _eval
