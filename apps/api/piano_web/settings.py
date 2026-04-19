"""Runtime settings persisted as YAML.

A single `data/icr-viz-settings.yaml` file stores paths and knobs that the GUI
exposes for editing. YAML is the canonical form (users may hand-edit between
runs); JSON is accepted as a fallback for older installations that shipped
`icr-viz-settings.json` from i6.1.

Schema — all keys optional; unknown keys preserved on round-trip:

    icr_path:    str | null         # path to icrgui / icr binary
    log_level:   str                # DEBUG / INFO / WARNING

    # Soundbank directory — one source of truth. Used by:
    #   - scripts/ingest.py (scan for banks to import)
    #   - engine launch as --soundbank-dir
    bank_dir:    str | null

    # Editor-side MIDI bridge. Engine-side ports are *derived* (swapped)
    # at launch time — the editor's output is the engine's input, and
    # vice versa.
    midi:
      input:        str | null      # editor receives PONG here
      output:       str | null      # editor sends SysEx here
      default_core: str             # 'active' | 'additive' | ...

    # Everything the engine's CLI accepts, grouped under one namespace
    # so the mapping to icrgui flags is obvious.
    # null ⇒ the corresponding --flag is not passed on launch.
    engine:
      ir_file:          str | null  # --ir-file
      ir_dir:           str | null  # --ir-dir
      config_file:      str | null  # --engine-config-file
      config_dir:       str | null  # --engine-config-dir
      core_config_file: str | null  # --core-config-file

Atomic writes — we dump to a temp file and rename to avoid truncated writes
on crash. Settings access is thread-safe via a module-level lock.

Migration — older layouts are auto-migrated on load and rewritten to disk
on the next save, so users don't see a stale dual-key file:

    bank_dirs: [x, ...]     → bank_dir: x (first entry)
    soundbank_dir: x        → bank_dir: x (wins over bank_dirs)
    ir_file, ir_dir         → engine.ir_file, engine.ir_dir
    engine_config_file/dir  → engine.config_file, engine.config_dir
    core_config_file        → engine.core_config_file
    midi.default_input      → midi.input
    midi.default_output     → midi.output
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
    "log_level": "INFO",
    "bank_dir": None,
    "midi": {
        "input": None,
        "output": None,
        "default_core": "active",
    },
    "engine": {
        "ir_file": None,
        "ir_dir": None,
        "config_file": None,
        "config_dir": None,
        "core_config_file": None,
    },
}


# ---------------------------------------------------------------------------
# Load / save
# ---------------------------------------------------------------------------

def load() -> dict[str, Any]:
    """Return the current settings, merged over defaults. Never raises."""
    with _lock:
        result = _deep_merge(DEFAULT_SETTINGS, {})

        path = settings_path()
        raw: dict[str, Any] | None = None
        # `force_rewrite` is true when the source needs to be normalised on
        # disk — either we're migrating from the old JSON file, or one of
        # the legacy key-renames actually moved data.
        force_rewrite = False
        if path.exists():
            try:
                data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(data, dict):
                    raw = data
            except Exception as exc:
                logger.warning("settings.load_failed", extra={"path": str(path), "detail": str(exc)})
        else:
            # Migrate legacy .json if present — rewrite as YAML once.
            legacy = _legacy_json_path()
            if legacy.exists():
                try:
                    data = json.loads(legacy.read_text(encoding="utf-8"))
                    if isinstance(data, dict):
                        raw = data
                        force_rewrite = True
                        logger.info("settings.migrated_from_json", extra={"from": str(legacy)})
                except Exception as exc:
                    logger.warning(
                        "settings.legacy_migration_failed",
                        extra={"path": str(legacy), "detail": str(exc)},
                    )

        if raw is not None:
            migrated, changed = _migrate_legacy_keys(raw)
            result = _deep_merge(result, migrated)
            # Rewrite when either the source format changed (JSON→YAML) or the
            # legacy key layout moved, so the next read is fast-path YAML
            # and users see the canonical shape in their text editor.
            if force_rewrite or changed:
                _save_locked(result)
                if changed:
                    logger.info("settings.migrated_schema", extra={"path": str(path)})

        return result


def save(updates: dict[str, Any]) -> dict[str, Any]:
    """Merge `updates` into current settings and persist. Returns the new full dict.

    Deep merge — nested dicts (e.g., `midi`, `engine`) have their keys updated
    individually. Use `null` / None to clear a leaf key.
    """
    with _lock:
        current = _deep_merge(DEFAULT_SETTINGS, {})
        path = settings_path()
        if path.exists():
            try:
                existing = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
                if isinstance(existing, dict):
                    migrated, _ = _migrate_legacy_keys(existing)
                    current = _deep_merge(current, migrated)
            except Exception:
                pass   # fall through — current == defaults
        # Same migration pass on the user-supplied patch so legacy keys from
        # the FE still land correctly during the transition.
        migrated_updates, _ = _migrate_legacy_keys(dict(updates))
        merged = _deep_merge(current, migrated_updates)
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
# Schema migration
# ---------------------------------------------------------------------------

def _migrate_legacy_keys(d: dict[str, Any]) -> tuple[dict[str, Any], bool]:
    """Rewrite older top-level keys into the grouped layout.

    Idempotent — running it on an already-migrated dict is a no-op.
    Returns (new_dict, changed_flag). The caller rewrites disk only when
    something actually moved, so clean files aren't touched on every load.
    """
    out: dict[str, Any] = dict(d)
    changed = False

    # bank_dirs[0] / soundbank_dir → bank_dir
    if out.get("bank_dir") is None:
        if out.get("soundbank_dir"):
            out["bank_dir"] = out["soundbank_dir"]
            changed = True
        elif isinstance(out.get("bank_dirs"), list) and out["bank_dirs"]:
            out["bank_dir"] = str(out["bank_dirs"][0])
            changed = True
    for legacy in ("bank_dirs", "soundbank_dir"):
        if legacy in out:
            out.pop(legacy, None)
            changed = True

    # engine.* from top-level
    engine = dict(out.get("engine") or {})
    _LEGACY_TO_ENGINE = {
        "ir_file": "ir_file",
        "ir_dir": "ir_dir",
        "engine_config_file": "config_file",
        "engine_config_dir": "config_dir",
        "core_config_file": "core_config_file",
    }
    for old, new in _LEGACY_TO_ENGINE.items():
        if old in out:
            value = out.pop(old)
            if value is not None and engine.get(new) is None:
                engine[new] = value
            changed = True
    if engine or "engine" in out:
        out["engine"] = engine

    # midi.default_input/output → midi.input/output
    midi = dict(out.get("midi") or {})
    for old, new in (("default_input", "input"), ("default_output", "output")):
        if old in midi:
            value = midi.pop(old)
            if value is not None and midi.get(new) is None:
                midi[new] = value
            changed = True
    if midi or "midi" in out:
        out["midi"] = midi

    return out, changed


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
