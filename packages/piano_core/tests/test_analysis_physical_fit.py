"""Tests for per-note physical-parameter fits."""

from __future__ import annotations

import math

import numpy as np
import pytest

from piano_core.analysis.physical_fit import NoteMathDiag, fit_note
from piano_core.models.note import Note
from piano_core.models.partial import Partial


def _synth_note(
    *, midi: int = 60, vel: int = 5,
    f0: float = 261.6, B: float = 5e-4,
    tau_alpha: float = 0.7, A_alpha: float = 0.5, A_mu: float = 0.05,
    k_max: int = 25,
) -> Note:
    partials = []
    for k in range(1, k_max + 1):
        f = k * f0 * math.sqrt(1.0 + B * k * k)
        tau = 0.5 * k ** (-tau_alpha)
        A = math.exp(1.0 - A_alpha * math.log(k) - A_mu * k)
        partials.append(Partial(
            k=k, f_hz=f, A0=max(A, 1e-6),
            tau1=max(tau, 1e-6), tau2=5.0 * max(tau, 1e-6),
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=f0, B=B, partials=tuple(partials))


def test_fit_note_recovers_B_from_clean_synthetic():
    note = _synth_note(B=4e-4)
    diag = fit_note(note)
    assert isinstance(diag, NoteMathDiag)
    assert diag.midi == 60 and diag.velocity == 5
    assert diag.B_hat is not None
    assert diag.B_hat == pytest.approx(4e-4, rel=5e-3)


def test_fit_note_recovers_tau_alpha():
    note = _synth_note(tau_alpha=0.8)
    diag = fit_note(note)
    assert diag.tau1_alpha == pytest.approx(0.8, abs=5e-3)
    assert diag.tau2_alpha == pytest.approx(0.8, abs=5e-3)


def test_fit_note_recovers_A0_envelope():
    note = _synth_note(A_alpha=0.6, A_mu=0.08)
    diag = fit_note(note)
    assert diag.A0_beta == pytest.approx(0.6, abs=5e-3)
    assert diag.A0_mu == pytest.approx(0.08, abs=5e-3)


def test_fit_note_reports_gamma_median():
    note = _synth_note()
    diag = fit_note(note)
    # tau2/tau1 = 5 by construction
    assert diag.gamma == pytest.approx(5.0, rel=1e-6)


def test_fit_note_empty_partials_returns_mostly_none():
    note = Note(midi=60, vel=0, f0_hz=261.6, B=0.0, partials=())
    diag = fit_note(note)
    assert diag.B_hat in (None, 0.0)
    assert diag.tau1_alpha is None
    assert diag.gamma is None
    assert diag.physical_prior_fit_quality == 0.0


def test_fit_note_handles_negative_B_gracefully():
    """If measurement noise drives the NLS toward negative B, fit clamps to 0 and warns."""
    # Use data that genuinely looks harmonic (B=0) — NLS should return B=0, warn "negligible signal".
    partials = []
    for k in range(1, 20):
        partials.append(Partial(
            k=k, f_hz=100.0 * k, A0=1.0, tau1=0.5, tau2=2.5, a1=1.0,
            beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    note = Note(midi=60, vel=5, f0_hz=100.0, B=0.0, partials=tuple(partials))
    diag = fit_note(note)
    assert diag.B_hat == pytest.approx(0.0, abs=1e-10)


def test_fit_note_quality_score_bounded():
    diag = fit_note(_synth_note())
    assert 0.0 <= diag.physical_prior_fit_quality <= 1.0


def test_fit_note_as_dict_is_json_serialisable():
    import json
    note = _synth_note()
    diag = fit_note(note)
    payload = diag.as_dict()
    json.dumps(payload)
    assert payload["midi"] == 60
    assert "fit_rmse" in payload
