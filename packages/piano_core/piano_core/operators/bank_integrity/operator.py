"""BankIntegrityOperator — read-only validator per spec i5 §3.

Scans a Bank for a small catalogue of structural / physical issues and
returns an ApplyResult whose `.bank` is the input unchanged. The issues
travel in diagnostics as structured `IntegrityIssue` records so the FE can
render them with "Fix it" buttons that pre-populate the appropriate operator.

Categories (spec i5 §3.2):
  - monotonicity         (cross-note trend broken on tau1/tau2/A0)
  - missing_note         (expected (midi, velocity) pair not present)
  - quality_floor        (too many partials in a note below quality threshold)
  - physical_consistency (tau2 < tau1 inside a partial — wrong ordering)
  - inharmonicity_range  (B < 0 or B unusually large)
  - tau_ordering         (tau1 not monotone-decreasing across k in one note)

All checks are pure functions — reused for in-process validation (and by
i5.5 FE integrity panel). The operator wiring just aggregates them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import Any, ClassVar, Literal

import numpy as np

from piano_core.analysis.cross_note import check_monotonicity
from piano_core.constants import DEFAULT_QUALITY_THRESHOLD
from piano_core.models.bank import Bank
from piano_core.operators.base import (
    ApplyDiagnostics,
    ApplyResult,
    EditRecord,
    Operator,
    OperatorParams,
    OperatorRegistry,
)


logger = logging.getLogger(__name__)


IssueKind = Literal[
    "monotonicity", "missing_note", "quality_floor",
    "physical_consistency", "inharmonicity_range", "tau_ordering",
]
IssueSeverity = Literal["info", "warning", "error"]


@dataclass(frozen=True)
class IssueLocation:
    midi: int
    velocity: int
    k: int | None = None
    parameter: str | None = None

    def as_dict(self) -> dict:
        return {
            "midi": self.midi,
            "velocity": self.velocity,
            "k": self.k,
            "parameter": self.parameter,
        }


@dataclass(frozen=True)
class IntegrityIssue:
    kind: IssueKind
    severity: IssueSeverity
    location: IssueLocation
    detail: str
    suggested_operator: str | None = None
    suggested_params: dict[str, Any] | None = None

    def as_dict(self) -> dict:
        return {
            "kind": self.kind,
            "severity": self.severity,
            "location": self.location.as_dict(),
            "detail": self.detail,
            "suggested_operator": self.suggested_operator,
            "suggested_params": self.suggested_params,
        }


@dataclass(frozen=True)
class BankIntegrityParams(OperatorParams):
    """Tunable thresholds for each check."""

    quality_floor: float = DEFAULT_QUALITY_THRESHOLD     # min partial fit_quality
    quality_floor_max_ratio: float = 0.3                 # fraction of below-quality partials to flag a note
    inharmonicity_min: float = 0.0                       # B < this → error
    inharmonicity_max: float = 1e-2                      # B > this → error (piano physics: ~1e-5..1e-2)
    expected_midi_range: tuple[int, int] | None = None   # optional: flag missing notes in this range
    expected_velocities: tuple[int, ...] | None = None   # e.g. (0,1,...,7); None = derived from bank


@dataclass(frozen=True)
class BankIntegrityDiagnostics(ApplyDiagnostics):
    """Diagnostics carrying the full issue list."""

    issues: tuple[IntegrityIssue, ...] = ()
    n_issues: int = 0
    n_errors: int = 0
    n_warnings: int = 0
    ok: bool = True

    def as_dict(self) -> dict:
        base = super().as_dict()
        base.update({
            "issues": [i.as_dict() for i in self.issues],
            "n_issues": self.n_issues,
            "n_errors": self.n_errors,
            "n_warnings": self.n_warnings,
            "ok": self.ok,
        })
        return base


class BankIntegrityOperator(Operator[BankIntegrityParams]):
    """Read-only validator. `apply` returns the input bank untouched and attaches
    an IntegrityIssue list to diagnostics."""

    name: ClassVar[str] = "BankIntegrity"
    params_class: ClassVar[type[BankIntegrityParams]] = BankIntegrityParams

    def apply(self, bank: Bank, params: BankIntegrityParams) -> ApplyResult:
        issues: list[IntegrityIssue] = []
        issues.extend(_check_physical_consistency(bank))
        issues.extend(_check_tau_ordering(bank))
        issues.extend(_check_inharmonicity_range(bank, params))
        issues.extend(_check_quality_floor(bank, params))
        issues.extend(_check_missing_notes(bank, params))
        issues.extend(_check_cross_note_monotonicity(bank))

        n_errors = sum(1 for i in issues if i.severity == "error")
        n_warnings = sum(1 for i in issues if i.severity == "warning")

        diag = BankIntegrityDiagnostics(
            warnings=(),   # human-readable strings reserved for operator warnings
            issues=tuple(issues),
            n_issues=len(issues),
            n_errors=n_errors,
            n_warnings=n_warnings,
            ok=(n_errors == 0),
        )
        edit = EditRecord.now(operator=self.name, params=params)

        logger.info(
            "bank_integrity.apply",
            extra={
                "bank_id": bank.id,
                "n_issues": len(issues),
                "n_errors": n_errors,
                "n_warnings": n_warnings,
            },
        )

        # Return original bank — validator never mutates.
        return ApplyResult(bank=bank, edit=edit, diagnostics=diag)


# ---------------------------------------------------------------------------
# Individual checks — each returns a list[IntegrityIssue]
# ---------------------------------------------------------------------------

def _check_physical_consistency(bank: Bank) -> list[IntegrityIssue]:
    """For piano decays, tau2 (slow component) must be ≥ tau1 (fast). Flipped
    ordering indicates a model failure."""
    out: list[IntegrityIssue] = []
    for note in bank.notes:
        for p in note.partials:
            if p.tau1 > 0 and p.tau2 > 0 and p.tau2 < p.tau1:
                out.append(IntegrityIssue(
                    kind="physical_consistency",
                    severity="error",
                    location=IssueLocation(midi=note.midi, velocity=note.vel, k=p.k),
                    detail=f"tau2 ({p.tau2:.3g}) < tau1 ({p.tau1:.3g}) — violates piano damping model",
                    suggested_operator="ToneIdentifyAndCorrect",
                    suggested_params={"parameters": ["tau1", "tau2"]},
                ))
    return out


def _check_tau_ordering(bank: Bank) -> list[IntegrityIssue]:
    """Within a single note, tau1 should descend with k. Strong upward bumps
    hint at a bad fit on that partial."""
    out: list[IntegrityIssue] = []
    for note in bank.notes:
        taus = [(p.k, p.tau1) for p in note.partials if p.tau1 > 0]
        taus.sort(key=lambda x: x[0])
        if len(taus) < 3:
            continue
        for (k_prev, t_prev), (k_curr, t_curr) in zip(taus, taus[1:]):
            # Flag only if tau1 grows by >50% between adjacent partials.
            if t_curr > 1.5 * t_prev:
                out.append(IntegrityIssue(
                    kind="tau_ordering",
                    severity="warning",
                    location=IssueLocation(midi=note.midi, velocity=note.vel, k=k_curr, parameter="tau1"),
                    detail=f"tau1(k={k_prev})={t_prev:.3g} → tau1(k={k_curr})={t_curr:.3g} (>50% increase)",
                    suggested_operator="AnchorInterpolate",
                    suggested_params={"parameters": ["tau1"]},
                ))
    return out


def _check_inharmonicity_range(
    bank: Bank, params: BankIntegrityParams,
) -> list[IntegrityIssue]:
    """Flag notes whose declared `Note.B` falls outside physical piano range."""
    out: list[IntegrityIssue] = []
    for note in bank.notes:
        if note.B < params.inharmonicity_min:
            out.append(IntegrityIssue(
                kind="inharmonicity_range",
                severity="error",
                location=IssueLocation(midi=note.midi, velocity=note.vel),
                detail=f"B={note.B:.3g} < {params.inharmonicity_min} (non-physical)",
                suggested_operator="ToneIdentifyAndCorrect",
                suggested_params={"parameters": ["f_coef"]},
            ))
        elif note.B > params.inharmonicity_max:
            out.append(IntegrityIssue(
                kind="inharmonicity_range",
                severity="warning",
                location=IssueLocation(midi=note.midi, velocity=note.vel),
                detail=f"B={note.B:.3g} > {params.inharmonicity_max} (suspect — typical piano range 1e-5..1e-2)",
                suggested_operator="ToneIdentifyAndCorrect",
                suggested_params={"parameters": ["f_coef"]},
            ))
    return out


def _check_quality_floor(
    bank: Bank, params: BankIntegrityParams,
) -> list[IntegrityIssue]:
    """Flag notes where more than `quality_floor_max_ratio` of partials are
    below the quality threshold."""
    out: list[IntegrityIssue] = []
    for note in bank.notes:
        if not note.partials:
            continue
        low = sum(1 for p in note.partials if p.fit_quality < params.quality_floor)
        ratio = low / len(note.partials)
        if ratio > params.quality_floor_max_ratio:
            out.append(IntegrityIssue(
                kind="quality_floor",
                severity="warning",
                location=IssueLocation(midi=note.midi, velocity=note.vel),
                detail=(
                    f"{low}/{len(note.partials)} partials below fit_quality={params.quality_floor:.2f} "
                    f"(ratio {ratio:.0%})"
                ),
                suggested_operator="ToneIdentifyAndCorrect",
                suggested_params=None,
            ))
    return out


def _check_missing_notes(
    bank: Bank, params: BankIntegrityParams,
) -> list[IntegrityIssue]:
    """Flag (midi, velocity) pairs that ought to be present in the bank."""
    out: list[IntegrityIssue] = []

    present = {n.id for n in bank.notes}
    if not present:
        return out

    velocities = (
        params.expected_velocities
        if params.expected_velocities is not None
        else tuple(sorted({n.vel for n in bank.notes}))
    )
    midi_range = params.expected_midi_range
    if midi_range is None:
        midi_lo = min(n.midi for n in bank.notes)
        midi_hi = max(n.midi for n in bank.notes)
    else:
        midi_lo, midi_hi = midi_range

    for m in range(midi_lo, midi_hi + 1):
        for v in velocities:
            if (m, v) not in present:
                out.append(IntegrityIssue(
                    kind="missing_note",
                    severity="warning",
                    location=IssueLocation(midi=m, velocity=v),
                    detail=f"expected note ({m}, {v}) is absent",
                    suggested_operator=None,
                    suggested_params=None,
                ))
    return out


def _check_cross_note_monotonicity(bank: Bank) -> list[IntegrityIssue]:
    """Reuse the analysis module's monotonicity check and wrap into IntegrityIssue."""
    if not bank.notes:
        return []
    raw = check_monotonicity(list(bank.notes))
    out: list[IntegrityIssue] = []
    for v in raw:
        severity: IssueSeverity = "error" if v.severity == "major" else "warning"
        out.append(IntegrityIssue(
            kind="monotonicity",
            severity=severity,
            location=IssueLocation(
                midi=v.midi_to, velocity=v.velocity, k=v.k, parameter=v.parameter,
            ),
            detail=(
                f"{v.parameter} violates {v.expected_direction} trend at k={v.k} "
                f"(m{v.midi_from}→m{v.midi_to}, Δ={v.delta:.3g})"
            ),
            suggested_operator="ToneIdentifyAndCorrect",
            suggested_params={"parameters": [v.parameter]},
        ))
    return out


# Register for deterministic replay (i5).
try:
    OperatorRegistry.register(BankIntegrityOperator)
except ValueError:
    pass
