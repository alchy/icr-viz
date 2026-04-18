"""Tests for the composite ToneIdentifyAndCorrectOperator."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.models.anchor import Anchor
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial
from piano_core.operators.base import ApplyResult, OperatorRegistry
from piano_core.operators.tone_identify_and_correct import (
    Source,
    ToneCorrectionParams,
    ToneIdentifyAndCorrectOperator,
)


@pytest.fixture(autouse=True)
def _ensure_operator_registered():
    """Other test modules (test_operators_base) call OperatorRegistry.clear() to
    isolate their own assertions. That also wipes the at-import-time registration
    done by the operator module, so we restore it before each test here."""
    if ToneIdentifyAndCorrectOperator.name not in OperatorRegistry.names():
        OperatorRegistry.register(ToneIdentifyAndCorrectOperator)
    yield


# ---- helpers -------------------------------------------------------------

def _note(
    *, midi: int = 60, vel: int = 5,
    noise_scale: float = 0.0, rng_seed: int = 0,
    f0: float = 261.6, k_max: int = 12,
) -> Note:
    rng = np.random.default_rng(rng_seed)
    partials = []
    for k in range(1, k_max + 1):
        tau = 0.5 * k ** (-0.7)
        a = 10.0 * k ** (-0.5)
        if noise_scale > 0:
            tau *= 1.0 + rng.normal(0.0, noise_scale)
            a *= 1.0 + rng.normal(0.0, noise_scale)
        partials.append(Partial(
            k=k, f_hz=k * f0, A0=max(a, 1e-6),
            tau1=max(tau, 1e-6), tau2=5.0 * max(tau, 1e-6),
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=f0, B=0.0, partials=tuple(partials))


def _bank(bank_id: str, note: Note, anchors: tuple[Anchor, ...] = ()) -> Bank:
    return Bank(id=bank_id, notes=(note,), anchors=anchors)


# ---- basic contract ------------------------------------------------------

def test_operator_is_registered():
    assert "ToneIdentifyAndCorrect" in OperatorRegistry.names()


def test_apply_raises_when_target_note_missing():
    op = ToneIdentifyAndCorrectOperator()
    empty = Bank(id="empty")
    params = ToneCorrectionParams(target_note_id=(60, 5))
    with pytest.raises(ValueError, match="target note"):
        op.apply(empty, params)


def test_apply_returns_apply_result_with_new_bank():
    op = ToneIdentifyAndCorrectOperator()
    bank = _bank("parent", _note())
    ref_bank_note = _note(noise_scale=0.1, rng_seed=9)
    sources = [
        Source(bank_id="parent", note=bank.get_note(60, 5)),
        Source(bank_id="ref", note=ref_bank_note),
    ]
    params = ToneCorrectionParams(
        target_note_id=(60, 5),
        parameters=("tau1", "A0"),
        preserve_fundamental=False,    # simplify assertions
    )
    result = op.apply_with_sources(bank, params, sources=sources)
    assert isinstance(result, ApplyResult)
    assert result.bank.id.startswith("parent.tic-")
    assert result.bank.parent_id == "parent"
    assert result.edit.operator == "ToneIdentifyAndCorrect"
    assert result.edit.source_note_id == (60, 5)


def test_diagnostics_carry_per_partial_log():
    op = ToneIdentifyAndCorrectOperator()
    bank = _bank("x", _note())
    sources = [Source(bank_id="x", note=bank.get_note(60, 5))]
    params = ToneCorrectionParams(
        target_note_id=(60, 5),
        parameters=("tau1",),
        min_sources_for_consensus=1,
    )
    result = op.apply_with_sources(bank, params, sources=sources)
    diag_dict = result.diagnostics.as_dict()
    assert "per_partial_log" in diag_dict
    assert len(diag_dict["per_partial_log"]) > 0
    assert diag_dict["target_note_id"] == [60, 5]


def test_insufficient_sources_skip_returns_unchanged_bank():
    op = ToneIdentifyAndCorrectOperator()
    bank = _bank("x", _note())
    sources = [Source(bank_id="x", note=bank.get_note(60, 5))]
    params = ToneCorrectionParams(
        target_note_id=(60, 5),
        min_sources_for_consensus=3,
        fallback_on_insufficient="skip",
    )
    result = op.apply_with_sources(bank, params, sources=sources)
    # Same bank reference — operator bailed out
    assert result.bank is bank
    assert any("skipped" in w for w in result.diagnostics.warnings)


def test_insufficient_sources_error_raises():
    op = ToneIdentifyAndCorrectOperator()
    bank = _bank("x", _note())
    sources = [Source(bank_id="x", note=bank.get_note(60, 5))]
    params = ToneCorrectionParams(
        target_note_id=(60, 5),
        min_sources_for_consensus=3,
        fallback_on_insufficient="error",
    )
    with pytest.raises(ValueError, match="min_sources_for_consensus"):
        op.apply_with_sources(bank, params, sources=sources)


def test_insufficient_sources_prior_only_proceeds_with_warning():
    op = ToneIdentifyAndCorrectOperator()
    bank = _bank("x", _note())
    sources = [Source(bank_id="x", note=bank.get_note(60, 5))]
    params = ToneCorrectionParams(
        target_note_id=(60, 5),
        min_sources_for_consensus=3,
        fallback_on_insufficient="prior_only",
    )
    result = op.apply_with_sources(bank, params, sources=sources)
    assert result.bank is not bank
    assert any("insufficient_sources" in w for w in result.diagnostics.warnings)


# ---- correcting an anomaly -----------------------------------------------

def test_strong_outlier_gets_hard_replaced():
    """Target has a huge spike at k=5; two reference banks agree on a smooth profile."""
    target_partials = list(_note().partials)
    # Blow up tau1 at k=5 in target
    target_partials[4] = Partial(
        k=5, f_hz=target_partials[4].f_hz, A0=target_partials[4].A0,
        tau1=10.0, tau2=10.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.95,
    )
    target_note = Note(midi=60, vel=5, f0_hz=261.6, B=0.0, partials=tuple(target_partials))
    bank = Bank(id="target", notes=(target_note,))
    ref_a = _note(rng_seed=1)
    ref_b = _note(rng_seed=2)

    op = ToneIdentifyAndCorrectOperator()
    sources = [
        Source(bank_id="target", note=target_note),
        Source(bank_id="a", note=ref_a),
        Source(bank_id="b", note=ref_b),
    ]
    params = ToneCorrectionParams(
        target_note_id=(60, 5),
        parameters=("tau1",),
        preserve_fundamental=False,
        min_sources_for_consensus=2,
    )
    result = op.apply_with_sources(bank, params, sources=sources)
    p5 = next(p for p in result.bank.get_note(60, 5).partials if p.k == 5)
    # The spike of tau1=10.0 should be corrected toward baseline
    assert p5.tau1 < 5.0
    assert p5.origin in ("derived", "anchored")
    assert result.diagnostics.n_changed >= 1
