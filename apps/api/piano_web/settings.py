"""Runtime settings persisted as YAML.

A single `data/icr-viz-settings.yaml` file stores paths and knobs that the GUI
exposes for editing. YAML is the canonical form (users may hand-edit between
runs); JSON is accepted as a fallback for older installations that shipped
`icr-viz-settings.json` from i6.1.

Schema — all keys optional; unknown keys preserved on round-trip:

    icr_path:         str        # path to icr.exe / icr binary
    bank_dirs:        list[str]  # directories scanned by the ingest script
    midi:
      default_input:  int | null # preferred input port index
      default_output: int | null # preferred output port index
      default_core:   str        # 'active' | 'additive' | ...
    log_level:        str        # DEBUG / INFO / WARNING

Atomic writes — we dump to a temp file and rename to avoid truncated writes
on crash. Settings access is thread-safe via a module-level lock.
"""

from __future__ import annotations

import json
import logging
import os
import threading
from pathlib import Path
from typing import Any

import yaml


logger = logging.getLogger(__name__)

_lock = threading.Lock()


# ---------------------------------------------------------------------------
# Path resolution
# ---------------------------------------------------------------------------

def settings_path() -> Path:
    override = os.environ.get("ICR_VIZ_SETTINGS")
    if override:
        return Path(override)
    return Path(__file__).resolve().parents[3] / "data" / "icr-viz-settings.yaml"


def _legacy_json_path() -> Path:
    """i6.1 shipped a .json settings file — migrate it on first read."""
    return settings_path().with_suffix(".json")


# ---------------------------------------------------------------------------
# Defaults
# ---------------------------------------------------------------------------

DEFAULT_SETTINGS: dict[str, Any] = {
    "icr_path": None,
    "bank_dirs": [],
    "midi": {
        "default_input": None,
        "default_output": None,
        "default_core": "active",
    },
    "log_level": "INFO",
}


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load() -> dict[str, Any]:
    """Return the current settings, merged over defaults. Never raises."""
    with _lock:
        result = _deep_merge(DEFAULT_SETTINGS, {})

        path = settings_path()
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    result = _deep_merge(result, data)
            except Exception as exc:
                logger.warning("settings.load_failed", extra={"path": str(path), "detail": str(exc)})
        else:
            # Migrate legacy .json if present — rewrite as YAML once.
            legacy = _legacy_json_path()
            if legacy.exists():
                try:
                    data = json.loads(legacy.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        result = _deep_merge(result, data)
                        # Persist migrated content as YAML for future reads.
                        _save_locked(result)
                        logger.info("settings.migrated_from_json", extra={"from": str(legacy)})
                except Exception as exc:
                    logger.warning(
                        "settings.legacy_migration_failed",
                        extra={"path": str(legacy), "detail": str(exc)},
                    )

        return result


def save(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into current settings and persist. Returns the new full dict.

    Deep merge — nested dicts (e.g., `midi`) have their keys updated individually.
    Use `null` / None to clear a leaf key.
    """
    with _lock:
        current = _deep_merge(DEFAULT_SETTINGS, {})
        path = settings_path()
        if path.exists():
            try:
                existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(existing, dict):
                    current = _deep_merge(current, existing)
            except Exception:
                pass   # fall through — current == defaults
        merged = _deep_merge(current, updates)
        _save_locked(merged)
        return merged


def _save_locked(data: dict[str, Any]) -> None:
    """Atomic YAML write. Caller must hold `_lock`."""
    path = settings_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(
        yaml.safe_dump(data, sort_keys=True, allow_unicode=True, default_flow_style=False),
        encoding="utf-8",
    )
    os.replace(tmp, path)
    logger.info("settings.save", extra={"path": str(path)})


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------

def _deep_merge(base: dict[str, Any], overlay: dict[str, Any]) -> dict[str, Any]:
    """Recursive merge — overlay wins on leaves, dicts merge."""
    out: dict[str, Any] = {}
    for k, v in base.items():
        if isinstance(v, dict):
            out[k] = _deep_merge(v, {})    # deep-copy dicts
        elif isinstance(v, list):
            out[k] = list(v)
        else:
            out[k] = v
    for k, v in overlay.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out
