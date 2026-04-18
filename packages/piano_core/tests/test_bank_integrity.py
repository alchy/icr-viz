"""Tests for BankIntegrityOperator."""

from __future__ import annotations

import math

import pytest

from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial
from piano_core.operators.base import ApplyResult, OperatorRegistry
from piano_core.operators.bank_integrity import (
    BankIntegrityDiagnostics,
    BankIntegrityOperator,
    BankIntegrityParams,
    IntegrityIssue,
)


@pytest.fixture(autouse=True)
def _ensure_registered():
    if BankIntegrityOperator.name not in OperatorRegistry.names():
        OperatorRegistry.register(BankIntegrityOperator)
    yield


def _note(
    *, midi: int = 60, vel: int = 5, B: float = 5e-4,
    tau1_scale: float = 1.0,
    override: dict[int, dict] | None = None,
) -> Note:
    override = override or {}
    partials = []
    for k in range(1, 11):
        f = k * 100.0 * math.sqrt(1.0 + B * k * k)
        tau1 = 0.5 * k ** (-0.7) * tau1_scale
        tau2 = 5.0 * tau1
        A0 = 10.0 * k ** (-0.5)
        quality = 0.95
        patch = override.get(k, {})
        kw = {
            "k": k, "f_hz": f, "A0": A0, "tau1": tau1, "tau2": tau2,
            "a1": 1.0, "beat_hz": 0.0, "phi": 0.0, "fit_quality": quality,
        }
        kw.update(patch)
        partials.append(Partial(**kw))
    return Note(midi=midi, vel=vel, f0_hz=100.0, B=B, partials=tuple(partials))


def _bank(bank_id: str, notes: tuple[Note, ...]) -> Bank:
    return Bank(id=bank_id, notes=notes)


# ---- contract ----------------------------------------------------------

def test_returns_apply_result_with_original_bank():
    bank = _bank("clean", (_note(),))
    op = BankIntegrityOperator()
    result = op.apply(bank, BankIntegrityParams())
    assert isinstance(result, ApplyResult)
    assert result.bank is bank   # validator never mutates
    assert isinstance(result.diagnostics, BankIntegrityDiagnostics)


def test_clean_bank_has_ok_flag():
    bank = _bank("clean", (_note(midi=60), _note(midi=61), _note(midi=62)))
    op = BankIntegrityOperator()
    result = op.apply(bank, BankIntegrityParams(
        expected_midi_range=(60, 62),
        expected_velocities=(5,),
    ))
    diag = result.diagnostics
    # Clean synthetic data — no errors. Warnings may exist (e.g., the n<3 paths
    # for cross-note smoothness), but ok flag should hold unless there are errors.
    assert diag.n_errors == 0
    assert diag.ok is True


# ---- physical_consistency: tau2 < tau1 -------------------------------

def test_detects_tau2_less_than_tau1():
    note = _note(override={3: {"tau1": 2.0, "tau2": 0.5}})
    bank = _bank("bad", (note,))
    op = BankIntegrityOperator()
    result = op.apply(bank, BankIntegrityParams())
    kinds = {i.kind for i in result.diagnostics.issues}
    assert "physical_consistency" in kinds
    bad = [i for i in result.diagnostics.issues if i.kind == "physical_consistency"]
    assert bad[0].severity == "error"
    assert bad[0].location.k == 3


# ---- tau_ordering --------------------------------------------------------

def test_detects_tau_ordering_spike():
    # k=5 tau1 spikes way up — more than 1.5x the previous value
    note = _note(override={5: {"tau1": 10.0, "tau2": 50.0}})
    bank = _bank("spike", (note,))
    op = BankIntegrityOperator()
    result = op.apply(bank, BankIntegrityParams())
    ordering = [i for i in result.diagnostics.issues if i.kind == "tau_ordering"]
    assert len(ordering) >= 1
    assert ordering[0].location.k == 5


# ---- inharmonicity_range ------------------------------------------------

def test_flags_negative_B_as_error():
    note = Note(midi=60, vel=5, f0_hz=100.0, B=-1e-4, partials=_note().partials)
    bank = _bank("badB", (note,))
    result = BankIntegrityOperator().apply(bank, BankIntegrityParams())
    inharm = [i for i in result.diagnostics.issues if i.kind == "inharmonicity_range"]
    assert len(inharm) >= 1
    assert inharm[0].severity == "error"


def test_flags_huge_B_as_warning():
    note = Note(midi=60, vel=5, f0_hz=100.0, B=0.1, partials=_note().partials)
    bank = _bank("bigB", (note,))
    result = BankIntegrityOperator().apply(bank, BankIntegrityParams())
    inharm = [i for i in result.diagnostics.issues if i.kind == "inharmonicity_range"]
    assert len(inharm) >= 1
    assert inharm[0].severity == "warning"


# ---- quality_floor ------------------------------------------------------

def test_detects_low_quality_ratio():
    # 5 of 10 partials below threshold → ratio 50% > default 30%
    overrides = {k: {"fit_quality": 0.1} for k in range(1, 6)}
    note = _note(override=overrides)
    bank = _bank("badQ", (note,))
    result = BankIntegrityOperator().apply(bank, BankIntegrityParams())
    qf = [i for i in result.diagnostics.issues if i.kind == "quality_floor"]
    assert len(qf) == 1
    assert "ratio" in qf[0].detail


def test_quality_floor_clean_note_no_issue():
    note = _note()
    bank = _bank("ok", (note,))
    result = BankIntegrityOperator().apply(bank, BankIntegrityParams())
    qf = [i for i in result.diagnostics.issues if i.kind == "quality_floor"]
    assert qf == []


# ---- missing_note -------------------------------------------------------

def test_flags_missing_midi_in_expected_range():
    bank = _bank("sparse", (_note(midi=60), _note(midi=62)))
    result = BankIntegrityOperator().apply(
        bank,
        BankIntegrityParams(
            expected_midi_range=(60, 62),
            expected_velocities=(5,),
        ),
    )
    missing = [i for i in result.diagnostics.issues if i.kind == "missing_note"]
    assert len(missing) == 1
    assert missing[0].location.midi == 61


def test_missing_expected_velocities():
    bank = _bank("noLowVel", (_note(vel=5), _note(vel=6)))
    result = BankIntegrityOperator().apply(
        bank,
        BankIntegrityParams(
            expected_midi_range=(60, 60),
            expected_velocities=(0, 5, 6),
        ),
    )
    missing = [i for i in result.diagnostics.issues if i.kind == "missing_note"]
    # (60, 0) missing
    assert any(i.location.velocity == 0 for i in missing)


# ---- cross-note monotonicity -------------------------------------------

def test_detects_inverted_tau1_across_midi():
    # tau1 grows with midi → monotonicity violations flagged
    notes = tuple(
        _note(midi=m, tau1_scale=0.5 + 0.5 * (m - 60))
        for m in range(60, 65)
    )
    bank = _bank("climbing", notes)
    result = BankIntegrityOperator().apply(bank, BankIntegrityParams())
    mono = [i for i in result.diagnostics.issues if i.kind == "monotonicity"]
    assert len(mono) >= 1


# ---- ok flag + counts ---------------------------------------------------

def test_ok_flag_false_when_errors_present():
    bank = _bank("bad", (_note(override={2: {"tau1": 3.0, "tau2": 0.5}}),))
    result = BankIntegrityOperator().apply(bank, BankIntegrityParams())
    assert result.diagnostics.ok is False
    assert result.diagnostics.n_errors >= 1


def test_ok_flag_true_when_only_warnings():
    # Make only warning-level issues (quality_floor, tau_ordering) — no errors
    overrides = {k: {"fit_quality": 0.05} for k in range(1, 7)}
    bank = _bank("warn", (_note(override=overrides),))
    result = BankIntegrityOperator().apply(bank, BankIntegrityParams())
    # quality_floor is warning-level; should not flip ok to False alone
    assert result.diagnostics.n_errors == 0
    assert result.diagnostics.ok is True
    assert result.diagnostics.n_warnings >= 1


# ---- serialization ------------------------------------------------------

def test_issue_as_dict_json_safe():
    import json
    bank = _bank("mix", (_note(override={4: {"tau1": 2.0, "tau2": 0.5}}),))
    result = BankIntegrityOperator().apply(bank, BankIntegrityParams())
    payload = result.diagnostics.as_dict()
    json.dumps(payload)
    assert "issues" in payload
    assert payload["n_issues"] == len(payload["issues"])


def test_operator_registered():
    assert BankIntegrityOperator.name in OperatorRegistry.names()
