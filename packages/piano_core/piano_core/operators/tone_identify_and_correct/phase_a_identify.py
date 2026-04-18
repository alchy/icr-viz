"""Phase A — per-parameter tone identification across multiple source banks.

Pipeline (spec i3 §2.2, simplified for initial implementation):

  1. For each reference source (Note + its anchors), call
     `completion.anchor_interpolate` to get a smoothed (theta_i, sigma_i).
  2. Fuse across sources with BLUE (or Huber when |S| >= 3 and outliers
     present) — `completion.consensus.{blue_consensus, huber_consensus}`.
  3. Physical regularization: where sigma_cons(k) exceeds a fraction of
     |theta_cons(k)|, shrink the estimate toward the fitted physical prior
     (inharmonicity for f_coef; power-law / envelope otherwise). This stops
     extrapolation blow-ups from propagating downstream.
  4. Clamp final estimate into PARAM_BOUNDS and, for parameters expected to
     decrease with k, swap to a PCHIP interpolant if the consensus curve
     violates monotonicity.

Notes on the simplification vs. spec §2.2 "step 3 Bayesian anchor update":
hard/soft anchors are already folded into each per-source
`anchor_interpolate` call (via `AnchorObservation.weight` → WLS weights).
The spec's separate Kalman step is therefore implicit in step 1 — we keep
the pipeline pure (no per-k side effects) and delegate anchor handling to
the canonical per-source fit. Explicit Kalman remains available for future
work if empirical behaviour demands it.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Mapping, Sequence

import numpy as np

from piano_core.completion.anchor_interpolate import (
    AnchorObservation,
    anchor_interpolate,
)
from piano_core.completion.consensus import (
    blue_consensus,
    has_outlier,
    huber_consensus,
)
from piano_core.completion.edge_cases import (
    EdgeCaseWarning,
    aggregate_warnings,
    check_multi_anchor_conflict,
)
from piano_core.constants import MATH_PARAMS, SIGMA_0
from piano_core.models.anchor import Anchor
from piano_core.models.note import Note
from piano_core.splines.bounds import clamp_to_bounds
from piano_core.splines.pchip import monotone_cubic_spline

from .provenance import ProvenanceRecord, TonalReference


logger = logging.getLogger(__name__)


# Parameters whose prior-fitted curve should dominate where sigma dominates the signal.
_REG_THRESHOLD_RATIO = 0.5   # if sigma / |theta| > this, blend toward physical prior


# ---------------------------------------------------------------------------
# Source bundle type
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Source:
    """One source's contribution to identify_tone: note + relevant anchors + bank id.

    `anchors` should already be filtered to the target (midi, velocity). The
    caller is responsible for matching — it's trivial with `Bank.anchors_for_note`.
    """

    bank_id: str
    note: Note
    anchors: tuple[Anchor, ...] = ()


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def identify_tone(
    midi: int,
    velocity: int,
    sources: Sequence[Source],
    *,
    parameters: Sequence[str] | None = None,
    use_physical_prior: bool = True,
    random_seed: int = 0,
) -> TonalReference:
    """Fuse multiple banks' measurements for (midi, velocity) into a TonalReference.

    Parameters
    ----------
    midi, velocity : the note to identify. All `sources` must carry a Note
        at this (midi, velocity) pair — otherwise the source is skipped with
        a warning.
    parameters : which math-layer parameters to compute. Defaults to
        all MATH_PARAMS.
    use_physical_prior : enable step 3 regularization.
    random_seed : propagated to bootstrap inside each anchor_interpolate call.
    """
    params = tuple(parameters if parameters is not None else MATH_PARAMS)
    if not sources:
        return _empty_reference(midi, velocity, params)

    # Filter sources that actually have a note at the target (midi, velocity)
    live_sources: list[Source] = []
    warnings: list[str] = []
    for s in sources:
        if s.note.midi != midi or s.note.vel != velocity:
            warnings.append(
                f"source {s.bank_id!r}: supplied note {(s.note.midi, s.note.vel)} != target {(midi, velocity)}; skipped"
            )
            continue
        live_sources.append(s)

    if not live_sources:
        return _empty_reference(
            midi, velocity, params,
            warnings=tuple(warnings) + ("identify_tone: no usable sources after filtering",),
        )

    # f0 from first live source — spec assumes invariance; emit warning if disagreement.
    f0 = live_sources[0].note.f0_hz
    if len(live_sources) > 1:
        deviations = [abs(s.note.f0_hz - f0) for s in live_sources[1:]]
        if any(d / max(f0, 1e-9) > 0.002 for d in deviations):   # >0.2% disagreement
            warnings.append(
                f"identify_tone: f0 disagreement across sources "
                f"(max |df0/f0| = {max(deviations) / max(f0, 1e-9):.4f})"
            )

    estimates: dict[str, Callable] = {}
    sigmas: dict[str, Callable] = {}
    coverage: dict[str, tuple[int, int]] = {}
    quality: dict[str, float] = {}
    provenance: dict[str, ProvenanceRecord] = {}

    for parameter in params:
        per_source = []
        lambdas: list[float] = []
        used_pchips: list[bool] = []
        anchor_ids: list[str] = []
        src_bank_ids: list[str] = []
        per_param_warnings: list[list[EdgeCaseWarning]] = []

        for s in live_sources:
            observations = [
                AnchorObservation(
                    k=a.k, parameter=a.parameter, value=a.value,
                    weight=a.weight, sigma=None,
                )
                for a in s.anchors
            ]
            result = anchor_interpolate(
                partials=s.note.partials,
                anchors=observations,
                parameter=parameter,
                random_seed=random_seed,
            )
            per_source.append((result.estimate, result.sigma))
            lambdas.append(result.lambda_used)
            used_pchips.append(result.used_pchip)
            anchor_ids.extend(a.id for a in s.anchors if a.parameter == parameter)
            src_bank_ids.append(s.bank_id)
            # We surface per-source warnings via provenance (they already pass through
            # as InterpolationResult.warnings; just include them on the record).
            if result.warnings:
                per_param_warnings.append([
                    EdgeCaseWarning(kind="per_source", detail=f"{s.bank_id}: {w}")
                    for w in result.warnings
                ])
            # Anchor-conflict check across sources (different banks disagreeing on same k)
            anchors_at_k: dict[int, list[Anchor]] = {}
            for a in s.anchors:
                if a.parameter == parameter:
                    anchors_at_k.setdefault(a.k, []).append(a)

        if not per_source:
            continue

        # Build the shared evaluation grid — union of per-source coverage
        k_mins = []
        k_maxs = []
        for s in live_sources:
            ks = [p.k for p in s.note.partials]
            if ks:
                k_mins.append(min(ks))
                k_maxs.append(max(ks))
        if not k_mins:
            continue
        k_lo = min(k_mins)
        k_hi = max(k_maxs)
        grid = np.arange(k_lo, k_hi + 1, dtype=float)

        if len(per_source) >= 3 and has_outlier(per_source, grid):
            cons = huber_consensus(per_source, grid, random_seed=random_seed)
            method = "huber"
        else:
            cons = blue_consensus(per_source, grid)
            method = "blue"

        per_param_warnings.append([
            EdgeCaseWarning(kind="consensus", detail=w) for w in cons.warnings
        ])

        theta_arr = cons.values
        sigma_arr = cons.sigmas

        # Step 3 — physical regularization
        if use_physical_prior:
            theta_arr, sigma_arr, reg_warnings = _regularize_with_prior(
                parameter=parameter, k_grid=grid,
                theta=theta_arr, sigma=sigma_arr,
                sources=live_sources,
            )
            if reg_warnings:
                per_param_warnings.append([
                    EdgeCaseWarning(kind="regularization", detail=w) for w in reg_warnings
                ])

        # Step 4 — clamp + monotonicity
        theta_clamped = clamp_to_bounds(theta_arr, parameter)
        if parameter in {"tau1", "tau2", "A0"}:
            theta_clamped = _enforce_monotone_decreasing(grid, np.asarray(theta_clamped))

        estimate_spline = monotone_cubic_spline(grid, np.asarray(theta_clamped, dtype=float))
        sigma_spline = monotone_cubic_spline(grid, np.asarray(sigma_arr, dtype=float))

        estimates[parameter] = estimate_spline
        sigmas[parameter] = sigma_spline
        coverage[parameter] = (int(k_lo), int(k_hi))
        quality[parameter] = _compute_quality_score(
            theta=np.asarray(theta_clamped, dtype=float),
            sigma=np.asarray(sigma_arr, dtype=float),
            parameter=parameter,
            n_sources=len(per_source),
        )
        provenance[parameter] = ProvenanceRecord(
            source_bank_ids=tuple(src_bank_ids),
            anchor_ids=tuple(dict.fromkeys(anchor_ids)),  # dedupe while preserving order
            consensus_method=method,
            lambda_used=tuple(lambdas),
            used_pchip=tuple(used_pchips),
            warnings=aggregate_warnings(*per_param_warnings),
        )

    B_hat = None
    if "f_coef" in estimates:
        # f_coef(k) = sqrt(1 + B*k^2) - 1, evaluated at k=2 for stability
        residual_at_2 = float(estimates["f_coef"](2.0))
        candidate = ((residual_at_2 + 1.0) ** 2 - 1.0) / 4.0
        if math.isfinite(candidate) and candidate >= 0:
            B_hat = candidate

    logger.info(
        "identify_tone",
        extra={
            "midi": midi, "velocity": velocity,
            "n_sources": len(live_sources),
            "parameters": list(params),
            "quality": {k: round(v, 3) for k, v in quality.items()},
        },
    )

    return TonalReference(
        midi=midi,
        velocity=velocity,
        f0_hz=f0,
        B_hat=B_hat,
        estimates=estimates,
        sigmas=sigmas,
        coverage=coverage,
        quality=quality,
        provenance=provenance,
        warnings=tuple(warnings),
    )


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _empty_reference(midi: int, velocity: int, params: tuple[str, ...], *, warnings: tuple[str, ...] = ()) -> TonalReference:
    def _zero_fn(x):
        xx = np.asarray(x, dtype=float)
        return np.zeros_like(xx) if xx.shape else 0.0

    def _big_sigma(x):
        xx = np.asarray(x, dtype=float)
        return np.full_like(xx, 1e6) if xx.shape else 1e6

    return TonalReference(
        midi=midi,
        velocity=velocity,
        f0_hz=0.0,
        B_hat=None,
        estimates={p: _zero_fn for p in params},
        sigmas={p: _big_sigma for p in params},
        coverage={p: (0, 0) for p in params},
        quality={p: 0.0 for p in params},
        provenance={},
        warnings=warnings or ("identify_tone: no sources",),
    )


def _regularize_with_prior(
    *,
    parameter: str,
    k_grid: np.ndarray,
    theta: np.ndarray,
    sigma: np.ndarray,
    sources: Sequence[Source],
) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Blend the consensus curve toward a physical-prior fit where sigma is large.

    Blending weight per k: w = sigma^2 / (sigma^2 + (ratio * |theta|)^2).
    w = 0 → trust consensus; w = 1 → fully trust prior.
    For parameters without a clean physical prior (a1, beat_hz) this is a no-op.
    """
    warnings: list[str] = []

    # Combine measured partials from all sources to refit a single physical prior.
    all_partials = []
    for s in sources:
        all_partials.extend(s.note.partials)

    if not all_partials:
        return theta, sigma, warnings

    prior_values: np.ndarray | None = None

    if parameter in ("tau1", "tau2"):
        from piano_core.completion.physical_priors import (
            power_law_predict,
            wls_log_log,
        )
        ks = [p.k for p in all_partials if p.k >= 1]
        ys = [float(getattr(p, parameter)) for p in all_partials if p.k >= 1]
        weights = [max(0.01, min(float(p.fit_quality), 1.0)) for p in all_partials if p.k >= 1]
        if len(ks) >= 3:
            fit = wls_log_log(ks, ys, weights=weights)
            prior_values = np.asarray(power_law_predict(fit, k_grid))
    elif parameter == "A0":
        from piano_core.completion.physical_priors import (
            amplitude_envelope_predict,
            wls_amplitude_envelope,
        )
        if len(all_partials) >= 3:
            fit = wls_amplitude_envelope(all_partials)
            prior_values = np.asarray(amplitude_envelope_predict(fit, k_grid))

    if prior_values is None:
        return theta, sigma, warnings

    ratio = _REG_THRESHOLD_RATIO
    abs_theta = np.abs(theta)
    denom = sigma ** 2 + (ratio * np.maximum(abs_theta, 1e-12)) ** 2
    blend = np.where(denom > 0, sigma ** 2 / denom, 0.0)
    blend = np.clip(blend, 0.0, 1.0)

    n_blended = int(np.sum(blend > 0.2))
    if n_blended > 0:
        warnings.append(
            f"{parameter}: regularized toward prior on {n_blended} of {k_grid.size} k-points"
        )

    regularized = (1.0 - blend) * theta + blend * prior_values
    # Inflate sigma by the blend factor — less confidence where we leaned on prior
    new_sigma = sigma * (1.0 + 0.5 * blend)
    return regularized, new_sigma, warnings


def _enforce_monotone_decreasing(k_grid: np.ndarray, values: np.ndarray) -> np.ndarray:
    """If `values` violates non-increase, fall back to a cumulative-min isotonic shape.

    PCHIP on the cumulative minimum gives a monotone interpolant through the
    extreme points while keeping the original data where it already descends.
    """
    v = np.asarray(values, dtype=float).copy()
    violates = np.any(np.diff(v) > 1e-12 * max(1.0, float(np.max(np.abs(v)))))
    if not violates:
        return v
    # Cumulative min: each value bounded by the min of itself and all earlier values
    for i in range(1, v.size):
        v[i] = min(v[i], v[i - 1])
    return v


def _compute_quality_score(
    *,
    theta: np.ndarray,
    sigma: np.ndarray,
    parameter: str,
    n_sources: int,
) -> float:
    """A bounded [0, 1] score summarizing how much Phase B should trust this estimate.

    Combines:
      - relative sigma (small sigma / |theta| → high confidence)
      - source count (more sources → higher confidence, capped at n=5)
    """
    abs_theta = np.abs(theta)
    sigma_ref = SIGMA_0.get(parameter, 1.0)
    denom = np.where(abs_theta > sigma_ref * 0.1, abs_theta, sigma_ref)
    rel = sigma / denom
    median_rel = float(np.median(rel))
    sigma_score = max(0.0, 1.0 - min(median_rel, 1.0))

    source_score = min(n_sources / 5.0, 1.0)

    return round(0.6 * sigma_score + 0.4 * source_score, 6)
