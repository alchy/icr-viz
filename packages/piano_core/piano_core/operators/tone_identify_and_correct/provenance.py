"""TonalReference + provenance records — Phase A output.

A TonalReference encapsulates everything Phase B needs to decide per-partial
corrections: the smoothed per-parameter curve, its uncertainty, and enough
audit metadata to trace each estimate back to its sources.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Callable, Mapping

import numpy as np


@dataclass(frozen=True)
class ProvenanceRecord:
    """Audit trail for one parameter's consensus fit."""

    source_bank_ids: tuple[str, ...]
    anchor_ids: tuple[str, ...]
    consensus_method: str          # "blue" | "huber"
    lambda_used: tuple[float, ...]  # per source
    used_pchip: tuple[bool, ...]    # per source
    warnings: tuple[str, ...] = ()

    def as_dict(self) -> dict:
        return {
            "source_bank_ids": list(self.source_bank_ids),
            "anchor_ids": list(self.anchor_ids),
            "consensus_method": self.consensus_method,
            "lambda_used": list(self.lambda_used),
            "used_pchip": list(self.used_pchip),
            "warnings": list(self.warnings),
        }


@dataclass(frozen=True)
class TonalReference:
    """Phase A result — per-parameter smooth estimates over k for one (midi, velocity).

    The `estimates` and `sigmas` callables accept either scalar k or an array
    of k; they return the same shape. `coverage[param] = (k_min, k_max)`
    records the domain over which the fit is well-supported by data.

    `quality[param]` is a bounded [0, 1] score combining relative sigma and
    data support — caller uses it to decide whether a correction should
    actually land (Phase B). Below `DEFAULT_QUALITY_THRESHOLD` operators
    should refuse to change measured partials without explicit override.
    """

    midi: int
    velocity: int
    f0_hz: float
    B_hat: float | None

    estimates: Mapping[str, Callable[[np.ndarray | float], np.ndarray | float]]
    sigmas: Mapping[str, Callable[[np.ndarray | float], np.ndarray | float]]
    coverage: Mapping[str, tuple[int, int]]
    quality: Mapping[str, float]
    provenance: Mapping[str, ProvenanceRecord] = field(default_factory=dict)
    warnings: tuple[str, ...] = ()

    def as_summary_dict(self) -> dict:
        """JSON-safe summary — no callables, just numbers + provenance."""
        return {
            "midi": self.midi,
            "velocity": self.velocity,
            "f0_hz": self.f0_hz,
            "B_hat": self.B_hat,
            "coverage": {k: list(v) for k, v in self.coverage.items()},
            "quality": dict(self.quality),
            "provenance": {k: v.as_dict() for k, v in self.provenance.items()},
            "warnings": list(self.warnings),
        }
