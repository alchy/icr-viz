"""MathAnalysisReport — the big aggregate produced by `analyze_bank()`.

Orchestrates per-note fits (physical_fit.fit_note), cross-note monotonicity
(cross_note.check_monotonicity), keyboard-wide aggregates (relationships.*),
and outlier detection (outlier_detect.detect_outliers) into a single
JSON-serialisable object suitable for the /api/banks/:id/math-analysis
endpoint.

Pure function — no IO. piano_web wraps with bank loading + caching.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Callable

from piano_core.models.bank import Bank

from .cross_note import MonotonicityViolation, check_monotonicity
from .outlier_detect import OutlierNote, detect_outliers
from .physical_fit import NoteMathDiag, fit_note
from .relationships import (
    RatioStats,
    TrendAnalysis,
    gamma_ratio_stats,
    inharmonicity_trend,
    power_law_distribution,
)


logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class MathAnalysisReport:
    """Full keyboard-wide math report (i4 §3)."""

    bank_id: str
    generated_at: str                      # ISO 8601 UTC
    n_notes: int
    per_note: dict[str, NoteMathDiag]      # key = "{midi}_{velocity}"

    inharmonicity_trend: TrendAnalysis
    tau1_power_trend: TrendAnalysis
    tau2_power_trend: TrendAnalysis
    A0_beta_distribution: TrendAnalysis
    A0_mu_distribution: TrendAnalysis
    gamma_ratio_stats_: RatioStats         # trailing underscore to avoid name clash

    monotonicity_violations: tuple[MonotonicityViolation, ...]
    outlier_notes: tuple[OutlierNote, ...]

    def as_dict(self) -> dict:
        return {
            "bank_id": self.bank_id,
            "generated_at": self.generated_at,
            "n_notes": self.n_notes,
            "per_note": {k: v.as_dict() for k, v in self.per_note.items()},
            "inharmonicity_trend": self.inharmonicity_trend.as_dict(),
            "tau1_power_trend": self.tau1_power_trend.as_dict(),
            "tau2_power_trend": self.tau2_power_trend.as_dict(),
            "A0_beta_distribution": self.A0_beta_distribution.as_dict(),
            "A0_mu_distribution": self.A0_mu_distribution.as_dict(),
            "gamma_ratio_stats": self.gamma_ratio_stats_.as_dict(),
            "monotonicity_violations": [v.as_dict() for v in self.monotonicity_violations],
            "outlier_notes": [o.as_dict() for o in self.outlier_notes],
        }


def analyze_bank(
    bank: Bank,
    *,
    progress_callback: Callable[[int, int], None] | None = None,
) -> MathAnalysisReport:
    """Fit every note in the bank and combine into a MathAnalysisReport.

    Parameters
    ----------
    bank : the Bank to analyse.
    progress_callback : optional. Receives (notes_done, notes_total) after
        each note. i4.8 uses this to emit WebSocket progress events.
    """
    n_total = len(bank.notes)
    per_note: dict[str, NoteMathDiag] = {}

    for i, note in enumerate(bank.notes):
        diag = fit_note(note)
        per_note[f"{note.midi}_{note.vel}"] = diag
        if progress_callback is not None:
            progress_callback(i + 1, n_total)

    diags_list = list(per_note.values())

    # Cross-note monotonicity on raw note data — uses the same notes, not the fits
    violations = check_monotonicity(bank.notes)

    # Keyboard-wide aggregates + outliers
    in_trend = inharmonicity_trend(diags_list)
    tau1_trend = power_law_distribution(diags_list, attr="tau1_alpha")
    tau2_trend = power_law_distribution(diags_list, attr="tau2_alpha")
    beta_trend = power_law_distribution(diags_list, attr="A0_beta")
    mu_trend = power_law_distribution(diags_list, attr="A0_mu")
    gamma_stats = gamma_ratio_stats(diags_list)
    outliers = detect_outliers(diags_list)

    logger.info(
        "analyze_bank",
        extra={
            "bank_id": bank.id,
            "n_notes": n_total,
            "n_violations": len(violations),
            "n_outliers": len(outliers),
        },
    )

    return MathAnalysisReport(
        bank_id=bank.id,
        generated_at=datetime.now(timezone.utc).isoformat(),
        n_notes=n_total,
        per_note=per_note,
        inharmonicity_trend=in_trend,
        tau1_power_trend=tau1_trend,
        tau2_power_trend=tau2_trend,
        A0_beta_distribution=beta_trend,
        A0_mu_distribution=mu_trend,
        gamma_ratio_stats_=gamma_stats,
        monotonicity_violations=tuple(violations),
        outlier_notes=tuple(outliers),
    )
