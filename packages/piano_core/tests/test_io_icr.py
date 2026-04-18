"""Tests for io/icr.py — version detection, v1->v2 migration, validation, file I/O."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from piano_core.io.icr import (
    CURRENT_ICR_VERSION,
    ValidationReport,
    detect_icr_version,
    dump_bank_dict,
    load_bank_dict,
    migrate_v1_to_v2,
    read_bank,
    validate_icr_schema,
    write_bank,
)
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial


REPO_ROOT = Path(__file__).resolve().parents[3]
IDEA_DIR = REPO_ROOT / "idea"


# ---- fixtures -------------------------------------------------------------

def _v1_minimal_bank() -> dict:
    """Legacy bank — no icr_version, no sigma/origin on partials."""
    return {
        "metadata": {"instrument_name": "Legacy", "k_max": 60},
        "notes": {
            "m021_vel0": {
                "midi": 21, "vel": 0, "f0_hz": 27.42, "B": 0.0002,
                "partials": [
                    {"k": 1, "f_hz": 27.42, "A0": 1.4, "tau1": 14.3, "tau2": 14.3,
                     "a1": 1.0, "beat_hz": 0.0, "phi": 0.0, "fit_quality": 0.87},
                ],
            },
        },
    }


def _v2_bank_with_sigma_origin() -> dict:
    partial = Partial(
        k=1, f_hz=440.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.99,
        sigma=0.02, origin="anchored",
    )
    bank = Bank(
        id="v2demo",
        notes=(Note(midi=60, vel=5, f0_hz=261.6, B=0.0, partials=(partial,)),),
        metadata={"k_max": 60},
    )
    return dump_bank_dict(bank)


# ---- detect_icr_version ---------------------------------------------------

def test_detect_version_absent_is_v1():
    assert detect_icr_version({"notes": {}}) == 1


def test_detect_version_explicit_v2():
    assert detect_icr_version({"icr_version": 2, "notes": {}}) == 2


def test_detect_version_string_coercion():
    assert detect_icr_version({"icr_version": "3"}) == 3


def test_detect_version_invalid_raises():
    with pytest.raises(ValueError):
        detect_icr_version({"icr_version": "not-a-number"})


# ---- validate_icr_schema --------------------------------------------------

def test_validate_ok_on_minimal_v1_bank():
    r = validate_icr_schema(_v1_minimal_bank())
    assert r.ok
    assert r.version == 1
    assert r.errors == ()


def test_validate_reports_missing_notes_key():
    r = validate_icr_schema({"metadata": {}})
    assert not r.ok
    assert any("notes" in e for e in r.errors)


def test_validate_reports_notes_wrong_type():
    r = validate_icr_schema({"notes": []})
    assert not r.ok


def test_validate_warns_on_empty_notes():
    r = validate_icr_schema({"notes": {}})
    assert r.ok
    assert r.warnings  # has at least one warning


def test_validate_reports_partial_missing_required_key():
    bank = _v1_minimal_bank()
    del bank["notes"]["m021_vel0"]["partials"][0]["k"]
    r = validate_icr_schema(bank)
    assert not r.ok
    assert any("k" in e for e in r.errors)


def test_validate_warns_on_future_version():
    data = {"icr_version": 99, "notes": {}}
    r = validate_icr_schema(data)
    assert any("newer" in w for w in r.warnings)


# ---- migrate_v1_to_v2 -----------------------------------------------------

def test_migrate_stamps_current_version_without_touching_other_fields():
    v1 = _v1_minimal_bank()
    v2 = migrate_v1_to_v2(v1)
    assert v2["icr_version"] == CURRENT_ICR_VERSION
    # Other fields preserved
    assert v2["metadata"] == v1["metadata"]
    assert v2["notes"] == v1["notes"]
    # Original untouched (shallow copy semantics)
    assert "icr_version" not in v1


def test_migrated_v2_payload_is_readable():
    v1 = _v1_minimal_bank()
    v2 = migrate_v1_to_v2(v1)
    bank = load_bank_dict(v2, bank_id="migrated")
    # Defaults applied by Partial.from_icr_dict on missing sigma/origin
    p = bank.get_note(21, 0).partials[0]
    assert p.sigma is None
    assert p.origin == "measured"


# ---- load/dump ------------------------------------------------------------

def test_load_bank_from_v1_payload_backfills_defaults():
    bank = load_bank_dict(_v1_minimal_bank(), bank_id="legacy")
    partial = bank.get_note(21, 0).partials[0]
    # F-2 / F-3: defaults applied when sigma/origin absent
    assert partial.sigma is None
    assert partial.origin == "measured"


def test_load_bank_from_v2_payload_preserves_sigma_origin():
    bank = load_bank_dict(_v2_bank_with_sigma_origin(), bank_id="v2demo")
    p = bank.get_note(60, 5).partials[0]
    assert p.sigma == pytest.approx(0.02)
    assert p.origin == "anchored"


def test_load_bank_strict_raises_on_missing_notes():
    with pytest.raises(ValueError):
        load_bank_dict({"metadata": {}}, bank_id="bad", strict=True)


def test_load_bank_non_strict_still_builds_empty_bank():
    # Non-strict path: missing required keys logged but parser proceeds where possible.
    # With no notes, resulting bank has zero notes.
    bank = load_bank_dict({"notes": {}}, bank_id="empty")
    assert len(bank.notes) == 0


def test_dump_bank_stamps_v2_schema():
    bank = Bank(id="x", notes=())
    out = dump_bank_dict(bank)
    assert out["icr_version"] == CURRENT_ICR_VERSION


# ---- round-trip -----------------------------------------------------------

def test_dump_then_load_is_identity_on_sigma_origin():
    """F-5 round-trip: icr_v2(bank) -> parse -> icr_v2(parsed) hash-equivalent."""
    partial = Partial(
        k=3, f_hz=82.34, A0=28.28, tau1=0.496, tau2=8.60, a1=0.246,
        beat_hz=0.143, phi=5.06, fit_quality=0.9946,
        sigma=0.035, origin="derived",
    )
    note = Note(midi=21, vel=0, f0_hz=27.42, B=0.0002, partials=(partial,))
    bank = Bank(id="rtrip", notes=(note,), metadata={"k_max": 60})

    dumped = dump_bank_dict(bank)
    restored = load_bank_dict(dumped, bank_id="rtrip")

    assert restored == bank


def test_v1_to_v2_migration_backfills_and_reserializes_cleanly():
    """Spec i1 §3.1 integration test: icr_v1 -> read -> rewrite as v2 -> still parses."""
    v1 = _v1_minimal_bank()
    bank_from_v1 = load_bank_dict(v1, bank_id="m1")

    # Writer stamps v2; partial defaults preserved on re-read
    dumped = dump_bank_dict(bank_from_v1)
    assert dumped["icr_version"] == 2

    restored = load_bank_dict(dumped, bank_id="m1")
    p = restored.get_note(21, 0).partials[0]
    assert p.sigma is None
    assert p.origin == "measured"
    assert restored == bank_from_v1


# ---- file I/O round-trip --------------------------------------------------

def test_write_bank_then_read_bank_is_identity(tmp_path: Path):
    partial = Partial(
        k=1, f_hz=440.0, A0=1.0, tau1=0.5, tau2=5.0, a1=1.0,
        beat_hz=0.0, phi=0.0, fit_quality=0.99,
        sigma=0.01, origin="anchored",
    )
    bank = Bank(
        id="fio",
        notes=(Note(midi=60, vel=5, f0_hz=261.6, B=0.0, partials=(partial,)),),
        metadata={"instrument_name": "FIO"},
    )
    out_path = tmp_path / "subdir" / "bank.json"
    write_bank(bank, out_path)

    assert out_path.exists()
    restored = read_bank(out_path)
    assert restored == bank


def test_read_bank_uses_file_stem_as_default_id(tmp_path: Path):
    path = tmp_path / "myname.json"
    path.write_text(json.dumps({"icr_version": 2, "notes": {}}), encoding="utf-8")
    bank = read_bank(path)
    assert bank.id == "myname"


# ---- integration with real reference banks -------------------------------

@pytest.mark.parametrize("bank_file", [
    "icr-bank-sample1.json",
    "icr-bank-sample2.json",
    "ks-grand-04130018-raw.json",
    "ks-grand-2604161547-icr.json",
    "as-blackgrand-04162340-raw-icr.json",
])
def test_real_bank_v1_to_v2_roundtrip(bank_file: str, tmp_path: Path):
    """Every reference bank must survive a load -> dump v2 -> reload cycle."""
    src = IDEA_DIR / bank_file
    if not src.exists():
        pytest.skip(f"{bank_file} not present")

    bank = read_bank(src)
    assert len(bank.notes) > 0
    # round-trip via v2
    out_path = tmp_path / f"{src.stem}-v2.json"
    write_bank(bank, out_path, indent=None)
    restored = read_bank(out_path, bank_id=bank.id)
    # Frozen dataclass equality covers all fields including nested partials
    assert restored == bank
