"""Log-space spline — interpolate in log(|y|) and exponentiate back.

For parameters that span orders of magnitude (tau1, tau2, A0 across the
keyboard; inharmonicity B; excitation positions) linear splines introduce
implausible dips into regions where the underlying physics varies
exponentially. Log transform + PCHIP gives scale-invariant, non-overshooting
interpolation.

Ported from the TS implementation in
``__legacy_code__/src/lib/analysis.ts:371-381``.
"""

from __future__ import annotations

from typing import Callable, Sequence

import numpy as np

from .pchip import monotone_cubic_spline


_LOG_FLOOR = 1e-15


def _safe_log(vals: np.ndarray) -> np.ndarray:
    """log(max(|y|, 1e-15)) — avoids -inf for zero/near-zero inputs."""
    return np.log(np.maximum(np.abs(vals), _LOG_FLOOR))


def log_space_spline(
    xs: Sequence[float] | np.ndarray,
    ys: Sequence[float] | np.ndarray,
) -> Callable[[float | np.ndarray], float | np.ndarray]:
    """Build a PCHIP interpolant in log-space with sign preservation.

    Typical use: for strictly-positive physical quantities the sign track is a
    no-op; it exists so the function degrades gracefully if someone hands it
    mixed-sign data (e.g. residuals around zero).
    """
    x_arr = np.asarray(xs, dtype=float)
    y_arr = np.asarray(ys, dtype=float)

    if y_arr.size == 0:
        return monotone_cubic_spline(x_arr, y_arr)   # empty-function sentinel

    log_mag_spline = monotone_cubic_spline(x_arr, _safe_log(y_arr))
    signs = np.where(y_arr >= 0.0, 1.0, -1.0)
    sign_spline = monotone_cubic_spline(x_arr, signs)

    def _eval(x):
        xx = np.asarray(x, dtype=float)
        scalar_input = xx.ndim == 0
        mag = np.exp(log_mag_spline(xx))
        sign_val = sign_spline(xx)
        sign = np.where(np.asarray(sign_val) >= 0.0, 1.0, -1.0)
        out = sign * mag
        if scalar_input and not isinstance(out, np.ndarray):
            return float(out)
        if scalar_input:
            return float(np.asarray(out).item())
        return out

    return _eval
