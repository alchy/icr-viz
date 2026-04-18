"""Tests for SplineTransferOperator + params."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial
from piano_core.operators.base import ApplyResult, OperatorRegistry
from piano_core.operators.spline_transfer import (
    ParameterConfig,
    SplineTransferOperator,
    SplineTransferParams,
)


@pytest.fixture(autouse=True)
def _ensure_registered():
    if SplineTransferOperator.name not in OperatorRegistry.names():
        OperatorRegistry.register(SplineTransferOperator)
    yield


# ---- helpers ------------------------------------------------------------

def _note(
    *, midi: int = 60, vel: int = 5,
    tau1_scale: float = 1.0, A0_scale: float = 1.0,
    rng_seed: int = 0,
) -> Note:
    rng = np.random.default_rng(rng_seed)
    partials = []
    for k in range(1, 11):
        partials.append(Partial(
            k=k, f_hz=100.0 * k,
            A0=A0_scale * 10.0 * k ** (-0.5),
            tau1=tau1_scale * 0.5 * k ** (-0.7),
            tau2=tau1_scale * 5.0 * k ** (-0.7),
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=100.0, B=0.0, partials=tuple(partials))


# ---- params -------------------------------------------------------------

def test_parameter_config_validates_parameter():
    with pytest.raises(ValueError):
        ParameterConfig(parameter="bogus", mode="absolute")


def test_parameter_config_validates_mode():
    with pytest.raises(ValueError):
        ParameterConfig(parameter="tau1", mode="invalid")   # type: ignore[arg-type]


def test_params_single_builds_one_config():
    p = SplineTransferParams.single(
        "tau1", "absolute",
        source_bank_id="src", source_note_id=(60, 5),
        target_note_ids=((60, 5),),
    )
    configs = p.resolved_configs()
    assert len(configs) == 1
    assert configs[0].parameter == "tau1"
    assert configs[0].mode == "absolute"


def test_params_multi_configs():
    p = SplineTransferParams(
        source_bank_id="src",
        source_note_id=(60, 5),
        target_note_ids=((60, 5),),
        parameter_configs=(
            ParameterConfig(parameter="tau1", mode="absolute"),
            ParameterConfig(parameter="A0", mode="relative"),
        ),
    )
    configs = p.resolved_configs()
    assert [c.parameter for c in configs] == ["tau1", "A0"]
    assert [c.mode for c in configs] == ["absolute", "relative"]


def test_params_legacy_fields_fallback():
    p = SplineTransferParams(
        source_note_id=(60, 5),
        target_note_ids=((60, 5),),
        legacy_parameter="tau1",
        legacy_mode="delta",
    )
    configs = p.resolved_configs()
    assert len(configs) == 1
    assert configs[0].parameter == "tau1"
    assert configs[0].mode == "delta"


def test_params_no_configs_or_legacy_raises():
    p = SplineTransferParams(target_note_ids=((60, 5),))
    with pytest.raises(ValueError):
        p.resolved_configs()


# ---- operator registration ----------------------------------------------

def test_operator_is_registered():
    assert SplineTransferOperator.name in OperatorRegistry.names()


# ---- transfer modes -----------------------------------------------------

def test_absolute_mode_replaces_with_source_values():
    """With absolute mode, target partials take the source spline's values verbatim."""
    source = _note(tau1_scale=2.0)     # source has 2x the decay time
    target = _note(tau1_scale=1.0)     # target has baseline
    bank = Bank(id="bank", notes=(source, Note(midi=61, vel=5, f0_hz=100.0, B=0.0, partials=target.partials)))

    op = SplineTransferOperator()
    params = SplineTransferParams.single(
        "tau1", "absolute",
        source_bank_id="bank",
        source_note_id=(60, 5),
        target_note_ids=((61, 5),),
    )
    result = op.apply_with_source(bank, params, source_note=source)

    new_target = result.bank.get_note(61, 5)
    # tau1 at k=2 should match source's scale (≈ 0.5 * 2 * 2^(-0.7) ≈ 0.615)
    p2 = next(p for p in new_target.partials if p.k == 2)
    expected = 2.0 * 0.5 * 2 ** (-0.7)
    assert p2.tau1 == pytest.approx(expected, rel=0.1)
    # k=1 protected by preserve_fundamental default
    p1 = next(p for p in new_target.partials if p.k == 1)
    orig_p1 = next(p for p in target.partials if p.k == 1)
    assert p1.tau1 == pytest.approx(orig_p1.tau1)


def test_relative_mode_preserves_target_k1():
    """Relative transfer scales the source shape by target_k1 / source_k1."""
    source = _note(tau1_scale=2.0)     # source k=1 tau1 = 1.0
    target = _note(tau1_scale=1.0)     # target k=1 tau1 = 0.5
    bank = Bank(id="bank", notes=(source, Note(midi=61, vel=5, f0_hz=100.0, B=0.0, partials=target.partials)))

    op = SplineTransferOperator()
    params = SplineTransferParams.single(
        "tau1", "relative",
        source_bank_id="bank",
        source_note_id=(60, 5),
        target_note_ids=((61, 5),),
    )
    result = op.apply_with_source(bank, params, source_note=source)

    new_target = result.bank.get_note(61, 5)
    # At k=2: new = target_k1 * source_spline(2) / source_spline(1)
    #       = 0.5 * (2 * 0.5 * 2^(-0.7)) / (2 * 0.5 * 1) = 0.5 * 2^(-0.7)
    p2 = next(p for p in new_target.partials if p.k == 2)
    expected = 0.5 * 2 ** (-0.7)
    assert p2.tau1 == pytest.approx(expected, rel=0.1)


def test_delta_mode_adds_source_minus_source_raw():
    """Delta adds (source_spline - source_raw) on top of the target value.

    If the source spline = source raw (trivial fit), delta is zero → target unchanged.
    """
    source = _note(tau1_scale=1.5)
    target = _note(tau1_scale=1.0)
    bank = Bank(
        id="bank",
        notes=(source, Note(midi=61, vel=5, f0_hz=100.0, B=0.0, partials=target.partials)),
    )
    op = SplineTransferOperator()
    params = SplineTransferParams.single(
        "tau1", "delta",
        source_bank_id="bank",
        source_note_id=(60, 5),
        target_note_ids=((61, 5),),
    )
    result = op.apply_with_source(bank, params, source_note=source)
    new_target = result.bank.get_note(61, 5)
    orig_p2 = next(p for p in target.partials if p.k == 2)
    p2 = next(p for p in new_target.partials if p.k == 2)
    # Delta should be small (source spline ≈ source raw, so new ≈ old).
    # Tolerate up to a few percent from smoothing.
    assert abs(p2.tau1 - orig_p2.tau1) < 0.05 * orig_p2.tau1


def test_preserve_fundamental_protects_k1_across_modes():
    source = _note(tau1_scale=3.0)
    target = _note()
    bank = Bank(
        id="bank",
        notes=(source, Note(midi=61, vel=5, f0_hz=100.0, B=0.0, partials=target.partials)),
    )
    op = SplineTransferOperator()
    params = SplineTransferParams.single(
        "tau1", "absolute",
        source_bank_id="bank", source_note_id=(60, 5),
        target_note_ids=((61, 5),),
        preserve_fundamental=True,
    )
    result = op.apply_with_source(bank, params, source_note=source)
    p1 = next(p for p in result.bank.get_note(61, 5).partials if p.k == 1)
    orig_p1 = next(p for p in target.partials if p.k == 1)
    assert p1.tau1 == pytest.approx(orig_p1.tau1)


def test_preserve_fundamental_disabled_allows_k1_change():
    source = _note(tau1_scale=3.0)
    target = _note()
    bank = Bank(
        id="bank",
        notes=(source, Note(midi=61, vel=5, f0_hz=100.0, B=0.0, partials=target.partials)),
    )
    op = SplineTransferOperator()
    params = SplineTransferParams(
        source_bank_id="bank", source_note_id=(60, 5),
        target_note_ids=((61, 5),),
        parameter_configs=(ParameterConfig(parameter="tau1", mode="absolute", preserve_fundamental=False),),
    )
    result = op.apply_with_source(bank, params, source_note=source)
    p1 = next(p for p in result.bank.get_note(61, 5).partials if p.k == 1)
    orig_p1 = next(p for p in target.partials if p.k == 1)
    assert p1.tau1 > orig_p1.tau1   # scaled up by source_scale=3.0


# ---- multi-parameter and multi-target -----------------------------------

def test_multi_parameter_applies_every_config():
    source = _note(tau1_scale=2.0, A0_scale=3.0)
    target = _note()
    bank = Bank(
        id="bank",
        notes=(source, Note(midi=61, vel=5, f0_hz=100.0, B=0.0, partials=target.partials)),
    )
    op = SplineTransferOperator()
    params = SplineTransferParams(
        source_bank_id="bank", source_note_id=(60, 5),
        target_note_ids=((61, 5),),
        parameter_configs=(
            ParameterConfig(parameter="tau1", mode="absolute"),
            ParameterConfig(parameter="A0", mode="absolute"),
        ),
    )
    result = op.apply_with_source(bank, params, source_note=source)
    new_note = result.bank.get_note(61, 5)
    orig_p2 = next(p for p in target.partials if p.k == 2)
    p2 = next(p for p in new_note.partials if p.k == 2)
    assert p2.tau1 > orig_p2.tau1     # scaled up via source
    assert p2.A0 > orig_p2.A0


def test_multiple_targets_updated():
    source = _note(tau1_scale=2.0)
    t1 = Note(midi=61, vel=5, f0_hz=100.0, B=0.0, partials=_note().partials)
    t2 = Note(midi=62, vel=5, f0_hz=100.0, B=0.0, partials=_note().partials)
    bank = Bank(id="bank", notes=(source, t1, t2))

    op = SplineTransferOperator()
    params = SplineTransferParams.single(
        "tau1", "absolute",
        source_bank_id="bank", source_note_id=(60, 5),
        target_note_ids=((61, 5), (62, 5)),
    )
    result = op.apply_with_source(bank, params, source_note=source)
    for midi in (61, 62):
        n = result.bank.get_note(midi, 5)
        p2 = next(p for p in n.partials if p.k == 2)
        orig_p2 = next(p for p in t1.partials if p.k == 2)
        assert p2.tau1 > orig_p2.tau1


def test_missing_target_emits_warning_and_skips():
    source = _note()
    bank = Bank(id="bank", notes=(source,))
    op = SplineTransferOperator()
    params = SplineTransferParams.single(
        "tau1", "absolute",
        source_bank_id="bank", source_note_id=(60, 5),
        target_note_ids=((99, 0),),
    )
    result = op.apply_with_source(bank, params, source_note=source)
    assert any("not in bank" in w for w in result.diagnostics.warnings)


# ---- contract -----------------------------------------------------------

def test_apply_returns_child_bank_with_parent_id():
    source = _note()
    target = Note(midi=61, vel=5, f0_hz=100.0, B=0.0, partials=_note().partials)
    bank = Bank(id="parent", notes=(source, target))
    op = SplineTransferOperator()
    params = SplineTransferParams.single(
        "tau1", "absolute",
        source_bank_id="parent", source_note_id=(60, 5),
        target_note_ids=((61, 5),),
    )
    result = op.apply_with_source(bank, params, source_note=source)
    assert isinstance(result, ApplyResult)
    assert result.bank.id.startswith("parent.st-")
    assert result.bank.parent_id == "parent"
    assert result.edit.operator == "SplineTransfer"


def test_target_note_ids_required():
    bank = Bank(id="x", notes=(_note(),))
    op = SplineTransferOperator()
    params = SplineTransferParams.single(
        "tau1", "absolute",
        source_bank_id="x", source_note_id=(60, 5),
    )
    with pytest.raises(ValueError, match="target_note_ids"):
        op.apply_with_source(bank, params, source_note=bank.get_note(60, 5))
