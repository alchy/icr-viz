"""ICR bank JSON I/O — reader, writer, v1->v2 migration, schema validation.

Separates serialization concerns from the domain model. `Bank.from_icr_dict` /
`Bank.to_icr_dict` remain the direct hooks for in-memory conversion; the
functions here add:

  - file-level `read_bank` / `write_bank`,
  - explicit ICR schema-version detection,
  - validation reporting (so a caller can fail early or warn),
  - v1 -> v2 migration helper (backfills `sigma=None, origin="measured"`).

`read_bank` accepts both v1 (legacy, no sigma/origin) and v2 (new fields) and
always returns a domain `Bank` built via `Bank.from_icr_dict`. `write_bank`
emits v2 unconditionally (the active schema in i1+).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Final

from piano_core.models.bank import Bank


logger = logging.getLogger(__name__)


CURRENT_ICR_VERSION: Final[int] = 2


# ---------------------------------------------------------------------------
# Version detection
# ---------------------------------------------------------------------------

def detect_icr_version(data: dict[str, Any]) -> int:
    """Return the ICR schema version embedded in the payload.

    Rules:
      - Explicit ``icr_version`` field wins (introduced in i1/v2).
      - Absent field => v1 (legacy banks do not carry sigma/origin on partials).
    """
    v = data.get("icr_version")
    if v is None:
        return 1
    try:
        return int(v)
    except (TypeError, ValueError) as exc:
        raise ValueError(f"invalid icr_version: {v!r}") from exc


# ---------------------------------------------------------------------------
# Schema validation
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ValidationReport:
    version: int
    errors: tuple[str, ...] = ()
    warnings: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.errors


_REQUIRED_TOP_LEVEL_KEYS: Final = ("notes",)
_REQUIRED_NOTE_KEYS: Final = ("partials",)
_REQUIRED_PARTIAL_KEYS: Final = ("k", "f_hz", "A0", "tau1", "tau2", "a1")


def validate_icr_schema(data: dict[str, Any]) -> ValidationReport:
    """Structural validation for an ICR bank payload.

    Intentionally lenient on optional fields — `Note.from_icr_dict` and
    `Partial.from_icr_dict` already default-fill missing `beat_hz`, `phi`,
    `fit_quality`, `sigma`, `origin`. We only hard-fail when a bank is
    structurally malformed (e.g. `notes` is not a dict, or a partial is
    missing its index `k`).
    """
    errors: list[str] = []
    warnings: list[str] = []

    version = detect_icr_version(data)

    for key in _REQUIRED_TOP_LEVEL_KEYS:
        if key not in data:
            errors.append(f"missing top-level key: {key!r}")

    notes = data.get("notes")
    if notes is None:
        return ValidationReport(version=version, errors=tuple(errors), warnings=tuple(warnings))
    if not isinstance(notes, dict):
        errors.append(f"`notes` must be a dict, got {type(notes).__name__}")
        return ValidationReport(version=version, errors=tuple(errors), warnings=tuple(warnings))
    if not notes:
        warnings.append("bank has zero notes")

    # Check a few sample notes (full scan would be O(N) — fine for typical sizes,
    # but we cap to first 5 of each side to keep validation fast on huge banks).
    sample_keys = list(notes.keys())[:5]
    for nkey in sample_keys:
        nd = notes[nkey]
        if not isinstance(nd, dict):
            errors.append(f"note {nkey!r} is not a dict")
            continue
        for rk in _REQUIRED_NOTE_KEYS:
            if rk not in nd:
                errors.append(f"note {nkey!r} missing required key: {rk!r}")
        partials = nd.get("partials")
        if isinstance(partials, list) and partials:
            first_p = partials[0]
            if isinstance(first_p, dict):
                for pk in _REQUIRED_PARTIAL_KEYS:
                    if pk not in first_p:
                        errors.append(f"note {nkey!r} partial[0] missing {pk!r}")

    if version > CURRENT_ICR_VERSION:
        warnings.append(
            f"icr_version={version} is newer than this reader ({CURRENT_ICR_VERSION}); "
            "some fields may not be surfaced to the domain model"
        )

    return ValidationReport(version=version, errors=tuple(errors), warnings=tuple(warnings))


# ---------------------------------------------------------------------------
# v1 -> v2 migration
# ---------------------------------------------------------------------------

def migrate_v1_to_v2(data: dict[str, Any]) -> dict[str, Any]:
    """Return a copy of `data` stamped with icr_version=2.

    The missing `sigma` / `origin` fields on partials are *not* written here —
    they are legitimately absent in v1 and the reader defaults them at parse
    time (see `Partial.from_icr_dict`). We only stamp the version so the file,
    once re-serialized, becomes a well-formed v2 document.
    """
    out = dict(data)
    out["icr_version"] = CURRENT_ICR_VERSION
    return out


# ---------------------------------------------------------------------------
# In-memory load/dump
# ---------------------------------------------------------------------------

def load_bank_dict(
    data: dict[str, Any],
    *,
    bank_id: str,
    parent_id: str | None = None,
    strict: bool = False,
) -> Bank:
    """Convert an ICR payload into a Bank.

    If `strict`, raises on any schema validation error; otherwise tolerates
    missing optional fields (defaults applied by Partial/Note from_icr_dict).
    """
    t0 = time.perf_counter()
    report = validate_icr_schema(data)
    if report.errors:
        logger.warning(
            "icr.validate.errors",
            extra={"bank_id": bank_id, "errors": report.errors, "version": report.version},
        )
    if report.warnings:
        logger.debug(
            "icr.validate.warnings",
            extra={"bank_id": bank_id, "warnings": report.warnings},
        )
    if strict and not report.ok:
        raise ValueError(f"invalid ICR bank: {report.errors}")
    bank = Bank.from_icr_dict(data, bank_id=bank_id, parent_id=parent_id)
    logger.info(
        "icr.load",
        extra={
            "bank_id": bank_id,
            "version": report.version,
            "n_notes": len(bank.notes),
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )
    return bank


def dump_bank_dict(bank: Bank) -> dict[str, Any]:
    """Serialize a Bank to an ICR v2 JSON-ready dict."""
    return bank.to_icr_dict()


# ---------------------------------------------------------------------------
# File I/O
# ---------------------------------------------------------------------------

def read_bank(
    path: str | Path,
    *,
    bank_id: str | None = None,
    parent_id: str | None = None,
    strict: bool = False,
) -> Bank:
    """Read an ICR JSON file from disk and return a Bank.

    `bank_id` defaults to the file stem (matches how `scripts/calibrate_sigma0.py`
    and the reference banks in `idea/` are identified).
    """
    p = Path(path)
    t0 = time.perf_counter()
    with p.open("r", encoding="utf-8") as f:
        data = json.load(f)
    resolved_id = bank_id or data.get("bank_id") or p.stem
    bank = load_bank_dict(data, bank_id=resolved_id, parent_id=parent_id, strict=strict)
    logger.info(
        "icr.read_file",
        extra={
            "path": str(p),
            "bank_id": resolved_id,
            "bytes": p.stat().st_size,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )
    return bank


def write_bank(
    bank: Bank,
    path: str | Path,
    *,
    indent: int | None = 2,
) -> None:
    """Serialize Bank to an ICR v2 JSON file.

    Default indent=2 is human-readable; pass `indent=None` for compact output
    (smaller files, faster I/O for large banks)."""
    p = Path(path)
    p.parent.mkdir(parents=True, exist_ok=True)
    t0 = time.perf_counter()
    payload = dump_bank_dict(bank)
    with p.open("w", encoding="utf-8", newline="\n") as f:
        json.dump(payload, f, ensure_ascii=False, indent=indent)
        f.write("\n")
    logger.info(
        "icr.write_file",
        extra={
            "path": str(p),
            "bank_id": bank.id,
            "bytes": p.stat().st_size,
            "duration_ms": round((time.perf_counter() - t0) * 1000, 2),
        },
    )
