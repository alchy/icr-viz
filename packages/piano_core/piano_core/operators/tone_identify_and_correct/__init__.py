"""Composite ToneIdentifyAndCorrect operator (i3)."""

from .decision_tree import (
    Action,
    CorrectionOutcome,
    DecisionParams,
    PerPartialLogEntry,
    apply_correction,
    decide_action,
)
from .operator import (
    ToneCorrectionDiagnostics,
    ToneCorrectionParams,
    ToneIdentifyAndCorrectOperator,
)
from .phase_a_identify import Source, identify_tone
from .provenance import ProvenanceRecord, TonalReference

__all__ = [
    "Action",
    "CorrectionOutcome",
    "DecisionParams",
    "PerPartialLogEntry",
    "ProvenanceRecord",
    "Source",
    "TonalReference",
    "ToneCorrectionDiagnostics",
    "ToneCorrectionParams",
    "ToneIdentifyAndCorrectOperator",
    "apply_correction",
    "decide_action",
    "identify_tone",
]
