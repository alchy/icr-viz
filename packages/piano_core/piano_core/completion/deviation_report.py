"""Leave-one-out deviation analysis for cross-bank anomaly detection.

For a target bank + set of reference banks, flag every (midi, velocity, k, parameter)
pair where the target value lies more than `min_z` standard deviations from
the consensus fit built from the **other** banks only. The leave-one-out rule
(virtual_test §7.1 #12) is load-bearing — without it a target that is also
a reference source can't be legitimately flagged as an anomaly against itself.

Pure math layer: the function operates on piano_core domain objects. The
FastAPI wrapper in piano_web.services loads banks and orchestrates the call.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass, field
from typing import Iterable, Sequence

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
from piano_core.constants import SIGMA_0, STORAGE_PARAMS
from piano_core.models.anchor import Anchor
from piano_core.models.note import Note


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Types
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ReferenceBankSample:
    """One reference bank's contribution to the deviation report.

    `bank_id` is the logical identifier; `note_by_key` maps (midi, velocity)
    tuples to Note objects. Anchors are also scoped per (midi, velocity).
    Callers can supply a thin view (only the notes they care about) so big
    banks don't need to be fully loaded on every report.
    """

    bank_id: str
    note_by_key: dict[tuple[int, int], Note]
    anchors_by_key: dict[tuple[int, int], tuple[Anchor, ...]] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviationEntry:
    """One flagged anomaly."""

    midi: int
    velocity: int
    k: int
    parameter: str
    target_value: float
    reference_value: float
    reference_sigma: float
    z_score: float
    recommend_action: str       # "review" | "soft_correction" | "hard_correction" | "fill"

    def as_dict(self) -> dict:
        return {
            "midi": self.midi,
            "velocity": self.velocity,
            "k": self.k,
            "parameter": self.parameter,
            "target_value": self.target_value,
            "reference_value": self.reference_value,
            "reference_sigma": self.reference_sigma,
            "z_score": self.z_score,
            "recommend_action": self.recommend_action,
        }


@dataclass(frozen=True)
class DeviationReport:
    """Ordered list of anomalies plus summary stats."""

    target_bank_id: str
    reference_bank_ids: tuple[str, ...]
    loo: bool
    min_z: float
    parameters: tuple[str, ...]
    entries: tuple[DeviationEntry, ...]

    def as_dict(self) -> dict:
        return {
            "target_bank_id": self.target_bank_id,
            "reference_bank_ids": list(self.reference_bank_ids),
            "loo": self.loo,
            "min_z": self.min_z,
            "parameters": list(self.parameters),
            "entries": [e.as_dict() for e in self.entries],
            "n_entries": len(self.entries),
        }


# ---------------------------------------------------------------------------
# Main routine
# ---------------------------------------------------------------------------

def deviation_report(
    *,
    target_bank_id: str,
    target_notes: dict[tuple[int, int], Note],
    references: Sequence[ReferenceBankSample],
    parameters: Iterable[str] | None = None,
    min_z: float = 2.0,
    note_filter: Iterable[tuple[int, int]] | None = None,
    random_seed: int = 0,
) -> DeviationReport:
    """Produce a sorted DeviationReport.

    Parameters
    ----------
    target_bank_id : id of the bank being analysed.
    target_notes : (midi, velocity) -> Note dict on the target bank.
    references : sequence of ReferenceBankSample. If one of these has
        `bank_id == target_bank_id` the LOO (leave-one-out) path drops it
        from each note's consensus, automatically.
    parameters : which math-layer STORAGE_PARAMS to scan. Default: all.
    min_z : threshold on |z| for inclusion in the report.
    note_filter : optional (midi, velocity) allow-list. None ⇒ every target note.
    random_seed : passed through to anchor_interpolate for determinism.
    """
    param_tuple = tuple(parameters if parameters is not None else STORAGE_PARAMS)
    ref_ids = tuple(r.bank_id for r in references)
    loo = target_bank_id in ref_ids
    keys_to_scan = list(note_filter) if note_filter is not None else list(target_notes.keys())

    entries: list[DeviationEntry] = []
    for (midi, velocity) in keys_to_scan:
        target_note = target_notes.get((midi, velocity))
        if target_note is None:
            continue

        # Per-parameter consensus — build per-source fits from references (excluding target on LOO).
        active_refs = [r for r in references if r.bank_id != target_bank_id] if loo else list(references)
        if len(active_refs) < 2:
            # Without at least 2 non-target sources the consensus degenerates.
            continue

        per_source_fits: dict[str, list[tuple]] = {p: [] for p in param_tuple}
        for ref in active_refs:
            ref_note = ref.note_by_key.get((midi, velocity))
            if ref_note is None:
                continue
            anchors = ref.anchors_by_key.get((midi, velocity), ())
            anchor_obs = [
                AnchorObservation(
                    k=a.k, parameter=a.parameter, value=a.value,
                    weight=a.weight, sigma=None,
                )
                for a in anchors
            ]
            for parameter in param_tuple:
                result = anchor_interpolate(
                    partials=ref_note.partials,
                    anchors=anchor_obs,
                    parameter=parameter,
                    random_seed=random_seed,
                )
                per_source_fits[parameter].append((result.estimate, result.sigma))

        # Evaluate consensus at each partial k present on the target
        target_ks = sorted({p.k for p in target_note.partials})
        target_val_by_k: dict[int, dict[str, float]] = {
            p.k: {param: float(getattr(p, param, math.nan)) for param in param_tuple}
            for p in target_note.partials
        }

        for parameter in param_tuple:
            sources = per_source_fits[parameter]
            if len(sources) < 2:
                continue
            k_arr = np.asarray(target_ks, dtype=float)

            # Choose consensus flavour
            if len(sources) >= 3 and has_outlier(sources, k_arr):
                cons = huber_consensus(sources, k_arr, random_seed=random_seed)
            else:
                cons = blue_consensus(sources, k_arr)

            for i, k in enumerate(target_ks):
                target_val = target_val_by_k[k].get(parameter)
                if target_val is None or not math.isfinite(target_val):
                    continue
                ref_val = float(cons.values[i])
                ref_sigma = float(cons.sigmas[i])
                if not math.isfinite(ref_val) or not math.isfinite(ref_sigma):
                    continue

                fallback = SIGMA_0.get(parameter, 1e-6)
                denom = max(ref_sigma, fallback * 0.1)
                z = (target_val - ref_val) / denom
                if abs(z) < min_z:
                    continue

                entries.append(DeviationEntry(
                    midi=midi,
                    velocity=velocity,
                    k=k,
                    parameter=parameter,
                    target_value=target_val,
                    reference_value=ref_val,
                    reference_sigma=ref_sigma,
                    z_score=float(z),
                    recommend_action=_recommend(z),
                ))

    entries.sort(key=lambda e: abs(e.z_score), reverse=True)

    logger.info(
        "deviation_report",
        extra={
            "target_bank_id": target_bank_id,
            "n_references": len(references),
            "loo": loo,
            "min_z": min_z,
            "n_entries": len(entries),
        },
    )

    return DeviationReport(
        target_bank_id=target_bank_id,
        reference_bank_ids=ref_ids,
        loo=loo,
        min_z=float(min_z),
        parameters=param_tuple,
        entries=tuple(entries),
    )


def _recommend(z: float) -> str:
    """Map an |z| band to a suggested corrective action.

    Thresholds align with the Phase B decision tree defaults so the anomaly
    list hints at the operator that will resolve each entry.
    """
    abs_z = abs(z)
    if abs_z < 2.5:
        return "review"
    if abs_z < 5.0:
        return "soft_correction"
    return "hard_correction"
