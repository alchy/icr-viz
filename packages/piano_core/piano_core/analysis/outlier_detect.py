"""Outlier detection on per-note fit parameters.

Finds notes whose physical-fit parameters (B, alpha, beta, mu, gamma) sit
far outside the keyboard-wide distribution. Uses robust MAD-based z-scores.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

import numpy as np

from .physical_fit import NoteMathDiag


@dataclass(frozen=True)
class OutlierNote:
    midi: int
    velocity: int
    attribute: str                   # one of "B_hat", "tau1_alpha", ...
    value: float
    median: float
    mad_z: float                     # robust z-score (|x - median| / (1.4826 * MAD))
    severity: str                    # "warn" | "strong"

    def as_dict(self) -> dict:
        return {
            "midi": self.midi,
            "velocity": self.velocity,
            "attribute": self.attribute,
            "value": self.value,
            "median": self.median,
            "mad_z": self.mad_z,
            "severity": self.severity,
        }


def detect_outliers(
    per_note: Sequence[NoteMathDiag],
    *,
    attributes: Iterable[str] = ("B_hat", "tau1_alpha", "A0_beta", "gamma"),
    warn_threshold: float = 3.0,
    strong_threshold: float = 5.0,
) -> list[OutlierNote]:
    """Flag notes whose per-attribute value is > warn_threshold robust-sigmas
    from the keyboard-wide median."""
    if not per_note:
        return []

    outliers: list[OutlierNote] = []
    for attr in attributes:
        values: list[tuple[NoteMathDiag, float]] = []
        for d in per_note:
            v = getattr(d, attr, None)
            if v is not None and np.isfinite(v):
                values.append((d, float(v)))
        if len(values) < 5:
            continue

        arr = np.array([v for _, v in values])
        median = float(np.median(arr))
        mad = float(np.median(np.abs(arr - median))) * 1.4826   # robust sigma

        # MAD collapses to 0 when more than half of samples share the median
        # (bimodal "90% flat + a few outliers" is common for per-note fits).
        # Fall back to sample std so an extreme outlier still shows up.
        if mad <= 1e-12:
            std_fallback = float(np.std(arr, ddof=0))
            if std_fallback <= 1e-12:
                continue    # everything truly identical — no outliers possible
            mad = std_fallback

        for d, v in values:
            z = abs(v - median) / mad
            if z >= warn_threshold:
                outliers.append(OutlierNote(
                    midi=d.midi,
                    velocity=d.velocity,
                    attribute=attr,
                    value=v,
                    median=median,
                    mad_z=float(z),
                    severity="strong" if z >= strong_threshold else "warn",
                ))

    # Sort most extreme first
    outliers.sort(key=lambda o: o.mad_z, reverse=True)
    return outliers
