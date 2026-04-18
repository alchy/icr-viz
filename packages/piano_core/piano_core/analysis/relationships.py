"""Keyboard-wide aggregates — B trend, tau power exponent distribution, gamma stats.

Takes a sequence of NoteMathDiag objects (one per note) and produces summary
distributions: mean/median/IQR of α, β, μ, γ, plus an R² of a simple monotone
B(midi) fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Iterable, Mapping

import numpy as np

from .physical_fit import NoteMathDiag


@dataclass(frozen=True)
class TrendAnalysis:
    n: int
    median: float | None
    mean: float | None
    iqr: tuple[float, float] | None     # (q1, q3)
    r_squared: float | None = None      # when a linear fit makes sense

    def as_dict(self) -> dict:
        return {
            "n": self.n,
            "median": self.median,
            "mean": self.mean,
            "iqr": list(self.iqr) if self.iqr is not None else None,
            "r_squared": self.r_squared,
        }


@dataclass(frozen=True)
class RatioStats:
    n: int
    median: float | None
    mean: float | None
    min: float | None
    max: float | None
    p05: float | None
    p95: float | None

    def as_dict(self) -> dict:
        return {
            "n": self.n, "median": self.median, "mean": self.mean,
            "min": self.min, "max": self.max, "p05": self.p05, "p95": self.p95,
        }


# ---------------------------------------------------------------------------
# aggregate builders
# ---------------------------------------------------------------------------

def inharmonicity_trend(per_note: Iterable[NoteMathDiag]) -> TrendAnalysis:
    """Fit log(B) = a + b * (midi - 60). Returns R² as smoothness proxy."""
    midis = []
    B_vals = []
    for d in per_note:
        if d.B_hat is not None and d.B_hat > 0:
            midis.append(d.midi)
            B_vals.append(d.B_hat)

    if len(midis) < 3:
        return TrendAnalysis(n=len(midis), median=None, mean=None, iqr=None, r_squared=None)

    arr = np.array(B_vals)
    log_B = np.log(arr)
    X = np.column_stack([np.ones_like(midis), np.array(midis) - 60.0])
    coef, *_ = np.linalg.lstsq(X, log_B, rcond=None)
    predicted = X @ coef
    ss_res = float(np.sum((log_B - predicted) ** 2))
    ss_tot = float(np.sum((log_B - np.mean(log_B)) ** 2))
    r_squared = 1.0 - ss_res / ss_tot if ss_tot > 1e-12 else 0.0

    return TrendAnalysis(
        n=len(arr),
        median=float(np.median(arr)),
        mean=float(np.mean(arr)),
        iqr=(float(np.quantile(arr, 0.25)), float(np.quantile(arr, 0.75))),
        r_squared=r_squared,
    )


def power_law_distribution(
    per_note: Iterable[NoteMathDiag], *, attr: str = "tau1_alpha",
) -> TrendAnalysis:
    """Summary stats of a given power-law exponent across the keyboard."""
    vals = [getattr(d, attr) for d in per_note if getattr(d, attr) is not None]
    if not vals:
        return TrendAnalysis(n=0, median=None, mean=None, iqr=None, r_squared=None)
    arr = np.array(vals)
    return TrendAnalysis(
        n=int(arr.size),
        median=float(np.median(arr)),
        mean=float(np.mean(arr)),
        iqr=(float(np.quantile(arr, 0.25)), float(np.quantile(arr, 0.75))),
        r_squared=None,
    )


def gamma_ratio_stats(per_note: Iterable[NoteMathDiag]) -> RatioStats:
    """Distribution of gamma = median(tau2/tau1) across notes.

    Published piano measurements report gamma around 2-4. Values far outside
    that band suggest either model failure or unusually short/long decay
    structure (restoration-specific)."""
    vals = [d.gamma for d in per_note if d.gamma is not None and d.gamma > 0]
    if not vals:
        return RatioStats(n=0, median=None, mean=None, min=None, max=None, p05=None, p95=None)
    arr = np.array(vals)
    return RatioStats(
        n=int(arr.size),
        median=float(np.median(arr)),
        mean=float(np.mean(arr)),
        min=float(np.min(arr)),
        max=float(np.max(arr)),
        p05=float(np.quantile(arr, 0.05)),
        p95=float(np.quantile(arr, 0.95)),
    )
