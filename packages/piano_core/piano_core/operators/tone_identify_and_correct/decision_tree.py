"""Phase B — per-partial decision tree (math §3 / tone_identification_and_correction.md §3).

decide_action() inspects one (k, parameter) pair and returns a structured
Action: one of {none, fill, soft_blend, hard_replace}. apply_correction()
wraps that into a full per-note, per-parameter application that produces a
new Partial sequence and a per_partial_log for auditing.

Spec corrections (virtual_test) embedded here:
  - §7.1 #3: a partial whose reference came from a hard anchor must be
    hard_replace'd regardless of d-threshold — anchors are user-stated truth.
  - §7.1 #4: `preserve_fundamental=True` protects k=1 from all changes.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field, replace
from typing import Iterable, Mapping, Sequence

import numpy as np

from piano_core.constants import DEFAULT_QUALITY_THRESHOLD, SIGMA_0, STORAGE_PARAMS
from piano_core.models.partial import Partial

from .provenance import TonalReference


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Params
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class DecisionParams:
    """Thresholds shared across decide_action calls.

    - noise_threshold_d : if |d| < this, action is 'none' (the deviation is
      indistinguishable from noise at combined measurement uncertainty).
    - correction_threshold_d : below this (but above noise) action is
      'soft_blend'; above, 'hard_replace'.
    - fill_quality_threshold : minimum reference quality before we allow a
      'fill' action on a missing partial. Below this, we'd rather leave the
      partial missing than fabricate a low-confidence value.
    - preserve_fundamental : protect k=1 partials across the board.
    """

    noise_threshold_d: float = 1.0
    correction_threshold_d: float = 2.5
    fill_quality_threshold: float = DEFAULT_QUALITY_THRESHOLD
    preserve_fundamental: bool = True


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Action:
    kind: str                                     # "none" | "fill" | "soft_blend" | "hard_replace"
    new_value: float | None = None
    blend_weight: float | None = None             # for soft_blend
    reason: str = ""


@dataclass(frozen=True)
class PerPartialLogEntry:
    k: int
    parameter: str
    action: str
    current: float | None
    reference: float
    reference_sigma: float
    reference_quality: float
    normalized_deviation: float | None           # d = (current - ref) / sqrt(σ²_c + σ²_ref)
    new_value: float | None
    reason: str

    def as_dict(self) -> dict:
        return {
            "k": self.k,
            "parameter": self.parameter,
            "action": self.action,
            "current": self.current,
            "reference": self.reference,
            "reference_sigma": self.reference_sigma,
            "reference_quality": self.reference_quality,
            "normalized_deviation": self.normalized_deviation,
            "new_value": self.new_value,
            "reason": self.reason,
        }


@dataclass(frozen=True)
class CorrectionOutcome:
    """Result of apply_correction — the rewritten partials plus per-partial audit log."""

    partials: tuple[Partial, ...]
    log: tuple[PerPartialLogEntry, ...] = field(default_factory=tuple)


# ---------------------------------------------------------------------------
# decide_action
# ---------------------------------------------------------------------------

def decide_action(
    *,
    k: int,
    parameter: str,
    current: float | None,
    reference: float,
    reference_sigma: float,
    reference_quality: float,
    current_sigma: float | None,
    is_anchored_reference: bool,
    params: DecisionParams,
) -> Action:
    """Decide what to do with a single (k, parameter) pair."""
    # Virtual_test §7.1 #4: protect the fundamental when requested.
    if params.preserve_fundamental and k == 1:
        return Action(kind="none", reason="preserve_fundamental")

    # Virtual_test §7.1 #3: hard-anchored reference → force hard_replace regardless of d.
    # Tolerance on reference_sigma: a hard anchor collapses sigma to roughly 0 (or
    # well below SIGMA_0) after the consensus+regularization path.
    fallback_sigma = SIGMA_0.get(parameter, 1e-6)
    if is_anchored_reference and reference_sigma < fallback_sigma * 0.2:
        return Action(
            kind="hard_replace", new_value=reference,
            reason="hard_anchor_reference",
        )

    # Missing partial → fill (or refuse if reference quality too low)
    if current is None:
        if reference_quality < params.fill_quality_threshold:
            return Action(kind="none", reason="fill_quality_below_threshold")
        return Action(kind="fill", new_value=reference, reason="missing_partial")

    # Normalized deviation d = (current - reference) / sqrt(σ²_current + σ²_reference)
    s_c = current_sigma if (current_sigma is not None and current_sigma > 0) else fallback_sigma
    s_r = reference_sigma if reference_sigma > 0 else fallback_sigma
    denom = math.sqrt(s_c * s_c + s_r * s_r)
    if denom <= 0:
        # Shouldn't happen given the guards above — but belt-and-braces.
        return Action(kind="none", reason="zero_denominator_guard")

    d = (current - reference) / denom
    abs_d = abs(d)

    if abs_d < params.noise_threshold_d:
        return Action(kind="none", reason="within_noise")
    if abs_d < params.correction_threshold_d:
        blend = _soft_blend_weight(d, reference_quality, params)
        new_val = (1.0 - blend) * current + blend * reference
        return Action(
            kind="soft_blend",
            new_value=new_val,
            blend_weight=blend,
            reason="moderate_deviation",
        )
    return Action(
        kind="hard_replace",
        new_value=reference,
        reason="strong_anomaly",
    )


def _soft_blend_weight(d: float, reference_quality: float, params: DecisionParams) -> float:
    """Blend weight scales with |d| inside the soft band and with reference quality.

    At |d| = noise_threshold → blend = 0 (stay with current);
    at |d| = correction_threshold → blend = reference_quality (clamped to [0, 1]).
    """
    frac = max(0.0, min(1.0,
        (abs(d) - params.noise_threshold_d) /
        max(1e-9, params.correction_threshold_d - params.noise_threshold_d)
    ))
    return max(0.0, min(1.0, frac * reference_quality))


# ---------------------------------------------------------------------------
# apply_correction
# ---------------------------------------------------------------------------

def apply_correction(
    *,
    partials: Sequence[Partial],
    reference: TonalReference,
    parameters: Iterable[str] | None = None,
    params: DecisionParams | None = None,
    anchored_k_per_param: Mapping[str, set[int]] | None = None,
) -> CorrectionOutcome:
    """Apply Phase B to every partial in the note, for the requested parameters.

    anchored_k_per_param : the caller supplies which (parameter, k) pairs were
        hard-anchored in the TonalReference so we don't need to re-infer it
        from tiny sigmas. Pass None for no explicit anchor hints (the
        sigma-based heuristic will still catch them).
    """
    params = params or DecisionParams()
    target_params = tuple(parameters) if parameters else tuple(
        p for p in STORAGE_PARAMS if p in reference.estimates
    )

    # Build index of the supplied partials by k for O(1) lookups and to
    # detect missing partials (for fill actions).
    by_k: dict[int, Partial] = {p.k: p for p in partials}
    k_lo, k_hi = 0, 0
    covered = []
    for parameter in target_params:
        if parameter in reference.coverage:
            covered.append(reference.coverage[parameter])
    if covered:
        k_lo = min(c[0] for c in covered)
        k_hi = max(c[1] for c in covered)
    else:
        # No coverage → nothing to do. Return input unchanged.
        return CorrectionOutcome(partials=tuple(partials), log=())

    log: list[PerPartialLogEntry] = []

    # Snapshot existing partials into a mutable map so multiple parameters can
    # be applied in sequence without losing earlier updates.
    mutable_partials: dict[int, Partial] = dict(by_k)

    for parameter in target_params:
        estimate_fn = reference.estimates.get(parameter)
        sigma_fn = reference.sigmas.get(parameter)
        if estimate_fn is None or sigma_fn is None:
            continue
        ref_quality = float(reference.quality.get(parameter, 0.0))
        anchored_ks = (
            anchored_k_per_param.get(parameter, set())
            if anchored_k_per_param is not None
            else set()
        )

        for k in range(k_lo, k_hi + 1):
            ref_val = float(np.asarray(estimate_fn(float(k))).item()) if hasattr(estimate_fn(float(k)), "item") else float(estimate_fn(float(k)))
            ref_sigma = float(np.asarray(sigma_fn(float(k))).item()) if hasattr(sigma_fn(float(k)), "item") else float(sigma_fn(float(k)))

            if not math.isfinite(ref_val) or not math.isfinite(ref_sigma):
                continue

            current_p = mutable_partials.get(k)
            current_val: float | None
            current_sigma_val: float | None
            if current_p is None:
                current_val = None
                current_sigma_val = None
            else:
                val = getattr(current_p, parameter, None)
                current_val = float(val) if val is not None and math.isfinite(float(val)) else None
                current_sigma_val = current_p.sigma if current_p.sigma is not None else None

            action = decide_action(
                k=k,
                parameter=parameter,
                current=current_val,
                reference=ref_val,
                reference_sigma=ref_sigma,
                reference_quality=ref_quality,
                current_sigma=current_sigma_val,
                is_anchored_reference=(k in anchored_ks),
                params=params,
            )

            d_val: float | None
            if current_val is None:
                d_val = None
            else:
                fallback = SIGMA_0.get(parameter, 1e-6)
                s_c = current_sigma_val if current_sigma_val and current_sigma_val > 0 else fallback
                s_r = ref_sigma if ref_sigma > 0 else fallback
                denom = math.sqrt(s_c * s_c + s_r * s_r)
                d_val = (current_val - ref_val) / denom if denom > 0 else None

            log.append(PerPartialLogEntry(
                k=k,
                parameter=parameter,
                action=action.kind,
                current=current_val,
                reference=ref_val,
                reference_sigma=ref_sigma,
                reference_quality=ref_quality,
                normalized_deviation=d_val,
                new_value=action.new_value,
                reason=action.reason,
            ))

            # Apply the action
            if action.kind == "none":
                continue
            if action.kind == "fill":
                new_p = _build_filled_partial(
                    k=k, parameter=parameter, reference=reference,
                    value=action.new_value,
                )
                mutable_partials[k] = new_p
                continue
            if action.kind in ("soft_blend", "hard_replace") and current_p is not None:
                new_origin = "derived" if action.kind == "soft_blend" else "anchored" if (k in anchored_ks) else "derived"
                mutable_partials[k] = replace(
                    current_p,
                    **{parameter: float(action.new_value)},  # type: ignore[arg-type]
                    origin=new_origin,  # type: ignore[arg-type]
                )

    updated = tuple(mutable_partials[k] for k in sorted(mutable_partials.keys()))

    logger.info(
        "apply_correction",
        extra={
            "n_partials_in": len(partials),
            "n_partials_out": len(updated),
            "n_log_entries": len(log),
            "parameters": list(target_params),
        },
    )

    return CorrectionOutcome(partials=updated, log=tuple(log))


def _build_filled_partial(
    *, k: int, parameter: str, reference: TonalReference, value: float | None,
) -> Partial:
    """Synthesize a new Partial for a k that had no measurement.

    For missing k we still need a concrete frequency — use the inharmonicity
    model from reference (f_hz = k * f0 * sqrt(1 + B*k²)) if B is known;
    fall back to k * f0 otherwise.
    """
    B = reference.B_hat if reference.B_hat is not None else 0.0
    f_hz = k * reference.f0_hz * math.sqrt(1.0 + B * k * k) if reference.f0_hz > 0 else 0.0

    # Default per-parameter baseline — reference curves supply most, but we
    # need to fill any that weren't in `parameters`.
    def _eval(param: str, default: float) -> float:
        fn = reference.estimates.get(param)
        if fn is None:
            return default
        try:
            return float(np.asarray(fn(float(k))).item()) if hasattr(fn(float(k)), "item") else float(fn(float(k)))
        except Exception:
            return default

    fields = {
        "k": k,
        "f_hz": f_hz,
        "A0": value if parameter == "A0" else _eval("A0", 0.0),
        "tau1": value if parameter == "tau1" else _eval("tau1", 1.0),
        "tau2": value if parameter == "tau2" else _eval("tau2", 5.0),
        "a1": value if parameter == "a1" else _eval("a1", 1.0),
        "beat_hz": value if parameter == "beat_hz" else 0.0,
        "phi": 0.0,
        "fit_quality": 0.5,    # sentinel for derived/filled partials
        "sigma": None,
        "origin": "extrapolated",
    }
    return Partial(**fields)
