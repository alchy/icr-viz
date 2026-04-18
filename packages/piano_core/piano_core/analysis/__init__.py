"""Math-analysis module — per-note physical fits plus keyboard-wide aggregates (i4)."""

from .cross_note import (
    EXPECTED_DIRECTION,
    MonotonicityViolation,
    MonotoneDirection,
    check_monotonicity,
)
from .outlier_detect import OutlierNote, detect_outliers
from .physical_fit import NoteMathDiag, fit_note
from .relationships import (
    RatioStats,
    TrendAnalysis,
    gamma_ratio_stats,
    inharmonicity_trend,
    power_law_distribution,
)
from .report import MathAnalysisReport, analyze_bank

__all__ = [
    "EXPECTED_DIRECTION",
    "MathAnalysisReport",
    "MonotonicityViolation",
    "MonotoneDirection",
    "NoteMathDiag",
    "OutlierNote",
    "RatioStats",
    "TrendAnalysis",
    "analyze_bank",
    "check_monotonicity",
    "detect_outliers",
    "fit_note",
    "gamma_ratio_stats",
    "inharmonicity_trend",
    "power_law_distribution",
]
