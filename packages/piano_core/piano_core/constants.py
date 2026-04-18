"""Global constants for piano_core.

Values here are canonical defaults for i1-i5. They may be overridden per-bank
via `bank.metadata.sigma_0_override` (planned for i2).
"""

from __future__ import annotations

from typing import Final, Literal

# ---------------------------------------------------------------------------
# Origin of a partial value — tracks where it came from (F-3).
# ---------------------------------------------------------------------------
Origin = Literal["measured", "derived", "extrapolated", "anchored"]

ORIGINS: Final[tuple[Origin, ...]] = (
    "measured",       # directly observed from recording
    "derived",        # computed from other measured quantities
    "extrapolated",   # filled by physics prior beyond observed k
    "anchored",       # forced by a hard anchor
)

# ---------------------------------------------------------------------------
# SIGMA_0 — per-parameter default measurement uncertainty prior (F-1).
#
# Empirically calibrated 2026-04-18 against 5 reference banks in idea/
# (229 766 partial pairs, 10 pairwise combinations).
# Reproducible via: python scripts/calibrate_sigma0.py
#
# Notes on specific parameters:
#   - `f_coef` is a dimensionless residual f_hz/(k*f0*sqrt(1+B*k^2)) - 1;
#     f_hz itself is stored absolutely, so its sigma only makes sense as a
#     coefficient of the inharmonicity model.
#   - `a1` distribution is bimodal (~80% of partials have exactly a1=1.0);
#     MAD collapses to 0, so we fall back to p84-of-differing estimator.
# ---------------------------------------------------------------------------
SIGMA_0: Final[dict[str, float]] = {
    "tau1":    0.174,   # seconds — decay time (fast component)
    "tau2":    0.534,   # seconds — decay time (slow component)
    "A0":      0.075,   # amplitude coefficient
    "a1":      0.452,   # coupling (bimodal; p84-of-differing)
    "f_coef":  0.003,   # inharmonicity residual (dimensionless)
    "beat_hz": 0.23,    # beat frequency — inherently noisy
}

# ---------------------------------------------------------------------------
# Quality threshold (F-6) — unified `min_reference_quality` / `fit_quality_floor`.
# Previously duplicated in multiple Params classes with conflicting defaults.
# ---------------------------------------------------------------------------
DEFAULT_QUALITY_THRESHOLD: Final[float] = 0.3


# ---------------------------------------------------------------------------
# Parameter names used across the math layer.
# ---------------------------------------------------------------------------
# Parameters whose raw values are stored on Partial.
STORAGE_PARAMS: Final[tuple[str, ...]] = (
    "tau1", "tau2", "A0", "a1", "beat_hz",
)

# Parameters addressable in the math layer, including derived views.
# `f_coef` is not stored on Partial — it is a derived view computed on demand
# in the math layer for NLS fits and consensus over inharmonicity residuals.
MATH_PARAMS: Final[tuple[str, ...]] = STORAGE_PARAMS + ("f_coef",)
