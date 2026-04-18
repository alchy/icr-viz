"""Tests for the operator base contract (F-4, F-5)."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone

import numpy as np
import pytest

from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.operators.base import (
    ApplyDiagnostics,
    ApplyResult,
    EditRecord,
    Operator,
    OperatorParams,
    OperatorRegistry,
)


# ---- F-4 random_seed propagation -----------------------------------------

def test_operator_params_has_default_seed_zero():
    p = OperatorParams()
    assert p.random_seed == 0


def test_operator_params_as_dict_is_json_serializable():
    import json
    p = OperatorParams(random_seed=42)
    d = p.as_dict()
    assert d == {"random_seed": 42}
    json.dumps(d)  # does not raise


def test_random_seed_propagation_to_numpy_rng_is_deterministic():
    """F-4: same seed -> identical draws (operator must consume seed via rng factory)."""
    p = OperatorParams(random_seed=7)
    rng_a = np.random.default_rng(p.random_seed)
    rng_b = np.random.default_rng(p.random_seed)
    assert np.array_equal(rng_a.integers(0, 10**9, size=50), rng_b.integers(0, 10**9, size=50))


def test_subclass_params_inherit_seed():
    @dataclass(frozen=True)
    class MyParams(OperatorParams):
        threshold: float = 0.1

    p = MyParams(random_seed=3, threshold=0.25)
    assert p.random_seed == 3
    assert p.as_dict() == {"random_seed": 3, "threshold": 0.25}


# ---- EditRecord -----------------------------------------------------------

def test_edit_record_now_stamps_utc_timestamp_and_uuid():
    r = EditRecord.now(operator="Test", params={"random_seed": 1})
    assert r.operator == "Test"
    assert r.timestamp.tzinfo == timezone.utc
    # uuid4 -> 36 chars with dashes
    assert len(r.edit_id) == 36


def test_edit_record_accepts_operator_params_object():
    p = OperatorParams(random_seed=99)
    r = EditRecord.now(operator="Test", params=p)
    assert r.params == {"random_seed": 99}


def test_edit_record_roundtrip_through_dict():
    r1 = EditRecord.now(
        operator="Test",
        params={"random_seed": 11, "x": 3.14},
        source_note_id=(60, 5),
    )
    d = r1.as_dict()
    assert d["source_note_id"] == [60, 5]
    r2 = EditRecord.from_dict(d)
    assert r2.edit_id == r1.edit_id
    assert r2.operator == r1.operator
    assert r2.params == r1.params
    assert r2.source_note_id == (60, 5)
    assert r2.timestamp == r1.timestamp


def test_edit_record_from_dict_handles_naive_timestamp():
    d = {
        "operator": "Test",
        "params": {"random_seed": 0},
        "timestamp": "2026-04-18T12:00:00",   # no tz
        "source_note_id": None,
    }
    r = EditRecord.from_dict(d)
    assert r.timestamp.tzinfo == timezone.utc


# ---- ApplyResult (F-5) ----------------------------------------------------

def test_apply_result_is_immutable():
    bank = Bank(id="x")
    edit = EditRecord.now(operator="T", params={})
    res = ApplyResult(bank=bank, edit=edit, diagnostics=ApplyDiagnostics())
    with pytest.raises(Exception):
        res.bank = Bank(id="y")  # type: ignore[misc]


def test_apply_result_summary_shape():
    bank = Bank(id="new1", parent_id="orig", notes=())
    edit = EditRecord.now(operator="T", params={"random_seed": 2})
    res = ApplyResult(
        bank=bank, edit=edit,
        diagnostics=ApplyDiagnostics(warnings=("w1",)),
    )
    s = res.to_summary()
    assert s["new_bank_id"] == "new1"
    assert s["parent_id"] == "orig"
    assert s["edit"]["operator"] == "T"
    assert s["diagnostics"]["warnings"] == ("w1",)


# ---- Operator ABC + registry ---------------------------------------------

@dataclass(frozen=True)
class _NoOpParams(OperatorParams):
    marker: str = "hello"


class _NoOp(Operator[_NoOpParams]):
    name = "NoOp_test"
    params_class = _NoOpParams

    def apply(self, bank: Bank, params: _NoOpParams) -> ApplyResult:
        edit = EditRecord.now(operator=self.name, params=params)
        return ApplyResult(bank=bank, edit=edit, diagnostics=ApplyDiagnostics())


def test_operator_abc_cannot_be_instantiated_without_apply():
    with pytest.raises(TypeError):
        Operator()  # type: ignore[abstract]


def test_noop_operator_returns_apply_result():
    op = _NoOp()
    bank = Bank(id="b1")
    result = op.apply(bank, _NoOpParams(random_seed=1, marker="x"))
    assert isinstance(result, ApplyResult)
    assert result.bank is bank
    assert result.edit.params == {"random_seed": 1, "marker": "x"}


def test_operator_registry_roundtrip_and_rejects_duplicates():
    # Clean registry between tests
    OperatorRegistry.clear()
    OperatorRegistry.register(_NoOp)
    assert _NoOp.name in OperatorRegistry.names()
    assert OperatorRegistry.get(_NoOp.name) is _NoOp
    with pytest.raises(ValueError):
        OperatorRegistry.register(_NoOp)
    OperatorRegistry.clear()


def test_operator_registry_rejects_unnamed_operator():
    class _Unnamed(Operator[_NoOpParams]):
        name = ""  # empty -> invalid
        params_class = _NoOpParams

        def apply(self, bank: Bank, params: _NoOpParams) -> ApplyResult:
            raise NotImplementedError

    OperatorRegistry.clear()
    with pytest.raises(ValueError):
        OperatorRegistry.register(_Unnamed)


# ---- Integration: EditRecord references bank that Operator returned ------

def test_end_to_end_no_op_preserves_bank_identity():
    op = _NoOp()
    bank = Bank(
        id="v1",
        notes=(Note(midi=60, vel=5, f0_hz=261.6, B=0.0, partials=()),),
        metadata={"instrument_name": "Demo"},
    )
    result = op.apply(bank, _NoOpParams(random_seed=42))
    # NoOp returns same bank; in real operators apply() produces a child with new id.
    assert result.bank is bank
    assert result.edit.operator == "NoOp_test"
    assert result.diagnostics.warnings == ()
