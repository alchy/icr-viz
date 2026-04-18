"""Smoke tests for piano_core models + constants — covers i1 F-1/F-2/F-3."""

from __future__ import annotations

import pytest

from piano_core.constants import (
    DEFAULT_QUALITY_THRESHOLD,
    MATH_PARAMS,
    ORIGINS,
    SIGMA_0,
    STORAGE_PARAMS,
)
from piano_core.models.note import Note
from piano_core.models.partial import Partial


# ---- F-1 SIGMA_0 ----------------------------------------------------------

def test_sigma_0_has_all_expected_keys():
    expected = {"tau1", "tau2", "A0", "a1", "f_coef", "beat_hz"}
    assert set(SIGMA_0) == expected


def test_sigma_0_values_are_positive_finite():
    import math
    for name, val in SIGMA_0.items():
        assert val > 0, f"SIGMA_0[{name}] must be positive, got {val}"
        assert math.isfinite(val), f"SIGMA_0[{name}] must be finite"


def test_sigma_0_matches_calibrated_values():
    # Pinned to the values in idea/i1.md §2.1 — keeps scripts/calibrate_sigma0.py
    # output and piano_core constants in lock-step.
    assert SIGMA_0["tau1"] == pytest.approx(0.174, rel=1e-6)
    assert SIGMA_0["tau2"] == pytest.approx(0.534, rel=1e-6)
    assert SIGMA_0["A0"] == pytest.approx(0.075, rel=1e-6)
    assert SIGMA_0["a1"] == pytest.approx(0.452, rel=1e-6)
    assert SIGMA_0["f_coef"] == pytest.approx(0.003, rel=1e-6)
    assert SIGMA_0["beat_hz"] == pytest.approx(0.23, rel=1e-6)


def test_storage_and_math_params_disjoint_except_storage_subset():
    # MATH_PARAMS is a superset of STORAGE_PARAMS (adds derived views like f_coef)
    for p in STORAGE_PARAMS:
        assert p in MATH_PARAMS
    # f_coef is derived-only — not stored on Partial
    assert "f_coef" in MATH_PARAMS
    assert "f_coef" not in STORAGE_PARAMS


def test_default_quality_threshold_unified():
    # F-6: one canonical threshold, not per-operator duplicates
    assert 0.0 < DEFAULT_QUALITY_THRESHOLD < 1.0


# ---- F-2/F-3 Partial ------------------------------------------------------

def test_partial_origin_literal_values():
    assert set(ORIGINS) == {"measured", "derived", "extrapolated", "anchored"}


def test_partial_default_origin_is_measured():
    p = Partial(k=1, f_hz=440.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=0.99)
    assert p.origin == "measured"
    assert p.sigma is None


def test_partial_from_icr_dict_tolerates_missing_sigma_origin():
    # Legacy v1 bank rows don't have sigma/origin
    d = {
        "k": 3,
        "f_hz": 82.3,
        "A0": 28.28,
        "tau1": 0.496,
        "tau2": 8.60,
        "a1": 0.246,
        "beat_hz": 0.14,
        "phi": 5.06,
        "fit_quality": 0.9946,
    }
    p = Partial.from_icr_dict(d)
    assert p.sigma is None
    assert p.origin == "measured"
    assert p.k == 3
    assert p.f_hz == pytest.approx(82.3)


def test_partial_roundtrip_preserves_sigma_and_origin():
    # F-2, F-3: new fields must survive ICR roundtrip
    original = Partial(
        k=5, f_hz=137.5, A0=22.3, tau1=0.666, tau2=10.2, a1=0.596,
        beat_hz=0.107, phi=0.864, fit_quality=0.96,
        sigma=0.04, origin="anchored",
    )
    d = original.to_icr_dict()
    assert d["sigma"] == pytest.approx(0.04)
    assert d["origin"] == "anchored"

    restored = Partial.from_icr_dict(d)
    assert restored == original


def test_partial_rejects_invalid_origin():
    with pytest.raises(ValueError):
        Partial.from_icr_dict({
            "k": 1, "f_hz": 440.0, "A0": 1.0, "tau1": 1.0, "tau2": 1.0,
            "a1": 1.0, "origin": "bogus",
        })


def test_partial_is_immutable():
    p = Partial(k=1, f_hz=440.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=0.99)
    with pytest.raises(Exception):
        p.f_hz = 880.0  # type: ignore[misc]


def test_partial_with_helper_returns_new_instance():
    p = Partial(k=1, f_hz=440.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
                beat_hz=0.0, phi=0.0, fit_quality=0.99)
    p2 = p.with_(sigma=0.01, origin="anchored")
    assert p is not p2
    assert p.sigma is None and p.origin == "measured"
    assert p2.sigma == pytest.approx(0.01)
    assert p2.origin == "anchored"


# ---- Note -----------------------------------------------------------------

def test_note_parses_m021_vel0_key_pattern():
    n = Note.from_icr_dict({"partials": []}, note_key="m021_vel0")
    assert n.midi == 21
    assert n.vel == 0
    assert n.note_key == "m021_vel0"


def test_note_parses_m060_vel5_and_preserves_id():
    n = Note.from_icr_dict({"partials": []}, note_key="m060_vel5")
    assert n.id == (60, 5)


def test_note_from_real_icr_dict_like_sample_banks():
    # Shape taken from idea/icr-bank-sample1.json m021_vel0
    raw = {
        "midi": 21,
        "vel": 0,
        "f0_hz": 27.42366453170568,
        "B": 0.00020695311242455093,
        "phi_diff": 5.350606470825994,
        "attack_tau": 0.09806885305709295,
        "A_noise": 0.41465113638970424,
        "noise_centroid_hz": 1200.0,
        "rms_gain": 0.0005551969097644708,
        "partials": [
            {"k": 1, "f_hz": 27.4265, "A0": 1.4063, "tau1": 14.36, "tau2": 14.36,
             "a1": 1.0, "beat_hz": 0.0, "phi": 4.11, "fit_quality": 0.874},
            {"k": 2, "f_hz": 54.87, "A0": 4.23, "tau1": 0.502, "tau2": 11.78,
             "a1": 0.066, "beat_hz": 0.0, "phi": 5.02, "fit_quality": 0.997},
        ],
    }
    n = Note.from_icr_dict(raw)
    assert n.midi == 21 and n.vel == 0
    assert len(n.partials) == 2
    assert n.partials[0].k == 1
    assert n.f0_hz == pytest.approx(27.42366, rel=1e-4)
    assert n.partial_by_k(2).a1 == pytest.approx(0.066)


def test_note_unknown_extras_preserved_in_roundtrip():
    raw = {
        "midi": 60, "vel": 5, "f0_hz": 261.6, "B": 0.0,
        "partials": [],
        "eq_biquads": [[1.0, 0, 0, 1.0, 0, 0]],
        "spectral_eq": {"freqs_hz": [100], "gains_db": [0.0]},
    }
    n = Note.from_icr_dict(raw)
    out = n.to_icr_dict()
    assert out["eq_biquads"] == raw["eq_biquads"]
    assert out["spectral_eq"] == raw["spectral_eq"]


def test_note_key_missing_both_midi_and_key_raises():
    with pytest.raises(ValueError):
        Note.from_icr_dict({"partials": []})
