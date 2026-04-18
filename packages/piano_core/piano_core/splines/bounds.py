"""Physical/numerical bounds for partial parameters.

Every parameter has a physically (or definitionally) admissible range. After
the interpolation pipeline — penalized fit, PCHIP monotonicity fallback,
consensus fusion — the final step clamps into these bounds. This catches:

  - Numerical overshoot (penalized spline producing a tiny negative tau near k=1).
  - Physically impossible values (a1 > 1 breaks the coupling coefficient meaning).
  - Extrapolation runaways (f_coef residuals exploding far from observed k).

Sources for specific bounds:
  - tau1 / tau2 in seconds: conservative upper bound (60 s) covers the longest
    decays we see in reference banks (typically <20 s). Lower bound is strict
    positivity (decay times cannot be zero/negative).
  - A0: amplitude coefficient, non-negative; no physical upper limit but a
    soft cap at 1e6 catches obvious serialization bugs.
  - a1 in [0, 1]: by definition a coupling ratio.
  - beat_hz: non-negative; no theoretical upper limit, but > 50 Hz would be
    suspicious (beat frequencies in piano strings rarely exceed a few Hz).
  - f_coef (dimensionless tuning residual): ±0.5 caps total inharmonicity
    model failure.
  - phi in (-pi, pi]: phase wraps, but consumers generally don't clamp phase.
  - fit_quality in [0, 1]: by definition.
"""

from __future__ import annotations

import logging
from typing import Final

import numpy as np


logger = logging.getLogger(__name__)


PARAM_BOUNDS: Final[dict[str, tuple[float, float]]] = {
    "tau1":       (1e-6, 60.0),
    "tau2":       (1e-6, 60.0),
    "A0":         (0.0,  1e6),
    "a1":         (0.0,  1.0),
    "beat_hz":    (0.0,  50.0),
    "f_coef":     (-0.5, 0.5),
    "phi":        (-np.pi, np.pi),
    "fit_quality": (0.0, 1.0),
}


def clamp_to_bounds(values: float | np.ndarray, parameter: str) -> float | np.ndarray:
    """Clip value(s) to the admissible range for `parameter`.

    Raises KeyError if the parameter has no declared bounds — better to crash
    loudly than silently skip sanitization.
    """
    if parameter not in PARAM_BOUNDS:
        raise KeyError(f"no declared bounds for parameter {parameter!r}; add one to PARAM_BOUNDS")
    lo, hi = PARAM_BOUNDS[parameter]
    arr = np.asarray(values, dtype=float)
    clamped = np.clip(arr, lo, hi)

    if np.any(arr != clamped):
        n_clamped = int(np.sum(arr != clamped))
        logger.debug(
            "bounds.clamp",
            extra={
                "parameter": parameter,
                "n_clamped": n_clamped,
                "min_seen": float(np.min(arr)),
                "max_seen": float(np.max(arr)),
                "lo": lo,
                "hi": hi,
            },
        )

    if np.isscalar(values) or arr.ndim == 0:
        return float(clamped)
    return clamped


def is_within_bounds(value: float, parameter: str) -> bool:
    """Quick inclusive check; used by validation code that shouldn't silently clamp."""
    lo, hi = PARAM_BOUNDS[parameter]
    return lo <= value <= hi
