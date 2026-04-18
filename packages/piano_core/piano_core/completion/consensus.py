"""BLUE / Huber consensus across multiple source estimates (math §7).

Given several per-source estimates `(theta_i(k), sigma_i(k))` from
anchor_interpolate runs on different banks, fuse them into a single
`(theta_consensus(k), sigma_consensus(k))` with optional robustness against
outlier sources.

Two flavors:
  - `blue_consensus` — inverse-variance weighted mean (classical BLUE).
    Diagonal Σ — cross-source correlation NOT modelled in i2; the i2 spec
    §2.4 flags this as a known limitation for v1.0.
  - `huber_consensus` — same BLUE formula but with Huber-tuned weights that
    down-weight sources diverging more than `delta_factor` MAD from the
    median. Use when |S| ≥ 3 and you suspect an outlier source.

Both return (values, sigmas, warnings) tuples evaluated on an input k-grid.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Sequence

import numpy as np


logger = logging.getLogger(__name__)


# Callable pair returned by anchor_interpolate — (estimate_fn, sigma_fn).
SourceEstimate = tuple[
    Callable[[float | np.ndarray], float | np.ndarray],
    Callable[[float | np.ndarray], float | np.ndarray],
]


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ConsensusResult:
    values: np.ndarray
    sigmas: np.ndarray
    k_grid: np.ndarray
    n_sources: int
    method: str                  # "blue" | "huber"
    warnings: tuple[str, ...] = ()


# ---------------------------------------------------------------------------
# BLUE
# ---------------------------------------------------------------------------

def blue_consensus(
    estimates: Sequence[SourceEstimate],
    k_grid: Sequence[float] | np.ndarray,
    *,
    sigma_floor: float = 1e-6,
) -> ConsensusResult:
    """Diagonal BLUE inverse-variance fusion (math §7.1).

    theta_cons(k) = (sum_i theta_i(k) / sigma_i^2(k)) / (sum_i 1 / sigma_i^2(k))
    sigma_cons^2(k) = 1 / sum_i (1 / sigma_i^2(k))
    """
    k_arr = np.asarray(k_grid, dtype=float)
    if len(estimates) == 0:
        return ConsensusResult(
            values=np.zeros_like(k_arr),
            sigmas=np.zeros_like(k_arr),
            k_grid=k_arr,
            n_sources=0,
            method="blue",
            warnings=("no sources provided",),
        )

    thetas, sigmas = _evaluate_sources(estimates, k_arr, sigma_floor=sigma_floor)
    inv_var = 1.0 / (sigmas * sigmas)
    # Guard: at any k where no source has finite variance, fallback to mean
    total_weight = np.sum(inv_var, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        theta_cons = np.where(
            total_weight > 0,
            np.sum(thetas * inv_var, axis=0) / total_weight,
            np.mean(thetas, axis=0),
        )
        sigma_cons = np.where(
            total_weight > 0,
            np.sqrt(1.0 / total_weight),
            np.inf,
        )
    # Replace any remaining non-finite sigma with a large but finite value
    sigma_cons = np.where(np.isfinite(sigma_cons), sigma_cons, 1e6)

    # Cross-source divergence warning (math §11.5): if cross-source variance exceeds
    # within-source (BLUE-combined) variance by >10x, flag it.
    cross_var = np.var(thetas, axis=0, ddof=0)
    within_var = 1.0 / np.maximum(total_weight, 1e-30)
    warnings: list[str] = []
    divergence_mask = (cross_var > 10.0 * within_var) & (within_var > 0)
    if np.any(divergence_mask):
        warnings.append(
            f"BLUE: cross-source variance exceeds within-source variance at "
            f"{int(np.sum(divergence_mask))} of {k_arr.size} k-points "
            "(diagonal fusion may be inadequate; see i2 spec §2.4)"
        )

    logger.debug(
        "consensus.blue",
        extra={
            "n_sources": len(estimates),
            "grid_size": k_arr.size,
            "mean_sigma": float(np.mean(sigma_cons)),
        },
    )

    return ConsensusResult(
        values=theta_cons,
        sigmas=sigma_cons,
        k_grid=k_arr,
        n_sources=len(estimates),
        method="blue",
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Huber-robust variant
# ---------------------------------------------------------------------------

def huber_consensus(
    estimates: Sequence[SourceEstimate],
    k_grid: Sequence[float] | np.ndarray,
    *,
    delta_factor: float = 1.345,
    sigma_floor: float = 1e-6,
    random_seed: int = 0,
) -> ConsensusResult:
    """Huber-weighted consensus for |sources| ≥ 3.

    Procedure (per math §7.2):
      1. Compute per-k median across sources.
      2. Compute per-k MAD across sources (robust scale).
      3. For each source i, compute its per-k deviation d_i = (theta_i - median) / MAD.
      4. Apply Huber weight function: w_i = 1 if |d_i| < delta_factor else delta_factor/|d_i|.
      5. BLUE-combine with composite weight = (1/sigma_i^2) * Huber weight.

    `random_seed` is accepted for API uniformity — the routine itself is
    deterministic, but downstream code often passes seeds for traceability.
    """
    k_arr = np.asarray(k_grid, dtype=float)
    n_sources = len(estimates)
    if n_sources < 3:
        # Fall through to plain BLUE — Huber tuning needs at least 3 sources to be meaningful.
        result = blue_consensus(estimates, k_arr, sigma_floor=sigma_floor)
        return ConsensusResult(
            values=result.values,
            sigmas=result.sigmas,
            k_grid=result.k_grid,
            n_sources=result.n_sources,
            method="huber",
            warnings=result.warnings + (
                f"Huber requested but only {n_sources} source(s) available; fell back to BLUE",
            ),
        )

    _ = random_seed  # acknowledged for signature parity

    thetas, sigmas = _evaluate_sources(estimates, k_arr, sigma_floor=sigma_floor)
    medians = np.median(thetas, axis=0)
    mad = np.median(np.abs(thetas - medians), axis=0)
    # Scaled MAD to match std under normality. Floor avoids division by zero on perfect consensus.
    scale = 1.4826 * np.maximum(mad, 1e-12)

    deviations = np.abs(thetas - medians) / scale
    huber_w = np.where(deviations < delta_factor, 1.0, delta_factor / np.maximum(deviations, 1e-12))

    # Composite weight combines inverse variance and Huber down-weighting
    composite_w = huber_w / (sigmas * sigmas)
    total_w = np.sum(composite_w, axis=0)
    with np.errstate(divide="ignore", invalid="ignore"):
        theta_cons = np.where(
            total_w > 0,
            np.sum(thetas * composite_w, axis=0) / total_w,
            medians,  # fallback to median if weights collapse
        )
        sigma_cons = np.where(
            total_w > 0,
            np.sqrt(1.0 / total_w),
            scale,
        )
    sigma_cons = np.where(np.isfinite(sigma_cons), sigma_cons, 1e6)

    warnings: list[str] = []
    n_outliers = int(np.sum(deviations >= delta_factor))
    if n_outliers > 0:
        warnings.append(
            f"Huber: {n_outliers} source-k observations down-weighted (|d| >= {delta_factor})"
        )

    logger.debug(
        "consensus.huber",
        extra={
            "n_sources": n_sources,
            "grid_size": k_arr.size,
            "n_outliers": n_outliers,
            "delta_factor": delta_factor,
        },
    )

    return ConsensusResult(
        values=theta_cons,
        sigmas=sigma_cons,
        k_grid=k_arr,
        n_sources=n_sources,
        method="huber",
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Outlier detection — surfaces whether Huber is worth running
# ---------------------------------------------------------------------------

def has_outlier(
    estimates: Sequence[SourceEstimate],
    k_grid: Sequence[float] | np.ndarray,
    *,
    delta_factor: float = 1.345,
    sigma_floor: float = 1e-6,
) -> bool:
    """Cheap screening: does any source diverge by > delta_factor MAD at any k?"""
    if len(estimates) < 3:
        return False
    k_arr = np.asarray(k_grid, dtype=float)
    thetas, _ = _evaluate_sources(estimates, k_arr, sigma_floor=sigma_floor)
    medians = np.median(thetas, axis=0)
    mad = np.median(np.abs(thetas - medians), axis=0)
    scale = 1.4826 * np.maximum(mad, 1e-12)
    deviations = np.abs(thetas - medians) / scale
    return bool(np.any(deviations >= delta_factor))


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _evaluate_sources(
    estimates: Sequence[SourceEstimate],
    k_arr: np.ndarray,
    *,
    sigma_floor: float,
) -> tuple[np.ndarray, np.ndarray]:
    """Evaluate each (estimate, sigma) pair on the k grid — returns stacked arrays."""
    thetas = np.empty((len(estimates), k_arr.size), dtype=float)
    sigmas = np.empty((len(estimates), k_arr.size), dtype=float)
    for i, (theta_fn, sigma_fn) in enumerate(estimates):
        thetas[i] = np.asarray(theta_fn(k_arr), dtype=float)
        sigmas[i] = np.asarray(sigma_fn(k_arr), dtype=float)
    # Sigmas must be strictly positive — avoid division blow-up.
    sigmas = np.maximum(sigmas, sigma_floor)
    # Non-finite thetas → mask out by setting sigma to huge
    bad = ~np.isfinite(thetas)
    if np.any(bad):
        thetas = np.where(bad, 0.0, thetas)
        sigmas = np.where(bad, 1e6, sigmas)
    return thetas, sigmas
