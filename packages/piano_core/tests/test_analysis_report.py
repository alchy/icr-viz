"""Tests for analyze_bank + MathAnalysisReport."""

from __future__ import annotations

import json
import math

import pytest

from piano_core.analysis import MathAnalysisReport, analyze_bank
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial


def _note(midi: int, *, vel: int = 5, B: float = 5e-4) -> Note:
    partials = []
    for k in range(1, 16):
        f = k * 100.0 * math.sqrt(1.0 + B * k * k)
        partials.append(Partial(
            k=k, f_hz=f, A0=10.0 * k ** (-0.5),
            tau1=0.5 * k ** (-0.7), tau2=5.0 * k ** (-0.7),
            a1=1.0, beat_hz=0.0, phi=0.0, fit_quality=0.95,
        ))
    return Note(midi=midi, vel=vel, f0_hz=100.0, B=B, partials=tuple(partials))


def test_analyze_bank_returns_report_for_every_note():
    notes = tuple(_note(m) for m in range(60, 75))
    bank = Bank(id="demo", notes=notes)
    report = analyze_bank(bank)
    assert isinstance(report, MathAnalysisReport)
    assert report.bank_id == "demo"
    assert report.n_notes == 15
    assert len(report.per_note) == 15
    # key format {midi}_{velocity}
    assert "60_5" in report.per_note


def test_analyze_bank_produces_keyboard_wide_trends():
    notes = tuple(_note(m, B=1e-4 * math.exp(0.05 * (m - 60))) for m in range(60, 85))
    bank = Bank(id="demo", notes=notes)
    report = analyze_bank(bank)
    # Inharmonicity trend should have a clear monotonic shape — R² > 0.8
    assert report.inharmonicity_trend.r_squared is not None
    assert report.inharmonicity_trend.r_squared > 0.8

    # Power-law distributions populated
    assert report.tau1_power_trend.n == 25
    assert report.tau1_power_trend.median is not None

    # Gamma stats populated
    assert report.gamma_ratio_stats_.n == 25
    assert report.gamma_ratio_stats_.median is not None


def test_analyze_bank_progress_callback_called():
    notes = tuple(_note(m) for m in range(60, 65))
    bank = Bank(id="x", notes=notes)
    calls: list[tuple[int, int]] = []
    analyze_bank(bank, progress_callback=lambda done, total: calls.append((done, total)))
    assert len(calls) == 5
    assert calls[-1] == (5, 5)


def test_analyze_bank_empty_bank_returns_valid_report():
    bank = Bank(id="empty")
    report = analyze_bank(bank)
    assert report.n_notes == 0
    assert report.per_note == {}
    # Trends still populated but with n=0
    assert report.inharmonicity_trend.n == 0


def test_report_as_dict_is_json_serialisable():
    notes = tuple(_note(m) for m in range(60, 65))
    bank = Bank(id="demo", notes=notes)
    report = analyze_bank(bank)
    payload = report.as_dict()
    json.dumps(payload)
    assert payload["bank_id"] == "demo"
    assert "per_note" in payload
    assert "inharmonicity_trend" in payload
    assert "monotonicity_violations" in payload


def test_monotonicity_violations_reported_when_present():
    # Construct notes where tau1 INCREASES with midi (against physics)
    notes = []
    for i, midi in enumerate(range(60, 65)):
        partials = []
        for k in range(1, 11):
            tau = 0.5 * k ** (-0.7) * (1.0 + 0.5 * i)   # grows with midi
            partials.append(Partial(
                k=k, f_hz=100 * k, A0=1.0,
                tau1=tau, tau2=5 * tau, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=0.95,
            ))
        notes.append(Note(midi=midi, vel=5, f0_hz=100.0, B=0.0, partials=tuple(partials)))
    bank = Bank(id="bumpy", notes=tuple(notes))
    report = analyze_bank(bank)
    assert len(report.monotonicity_violations) > 0
    assert any(v.parameter == "tau1" for v in report.monotonicity_violations)
