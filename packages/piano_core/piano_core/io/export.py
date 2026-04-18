"""Export formats for Bank data (i5 §4).

Three flavours beyond the ICR v2 JSON already covered by Bank.to_icr_dict:

  - ``synth_csv`` : flat per-partial rows with absolute values for a DSP
                    pipeline (Pianoteq, custom synth) to consume directly.
  - ``analysis_csv`` : same rows, plus per-note fit diagnostics (B_hat, alpha,
                    etc.) — intended for R/Python/Matlab analysis off-tool.
  - ``ndjson`` : one ICR note per line; used for streaming large banks where
                 a single JSON blob would be huge.

All three accept an ``exclude_extrapolated`` flag so a caller can filter out
partials whose `origin == "extrapolated"` (spec i5 §4.1 Pianoteq use case).
"""

from __future__ import annotations

import csv
import io
import json
import logging
from typing import Iterable, Iterator

from piano_core.analysis.physical_fit import NoteMathDiag, fit_note
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# CSV rows
# ---------------------------------------------------------------------------

SYNTH_CSV_COLUMNS: tuple[str, ...] = (
    "midi", "velocity", "k",
    "f_hz", "A0", "tau1", "tau2", "a1", "beat_hz", "phi",
    "fit_quality", "origin",
)

ANALYSIS_CSV_EXTRA: tuple[str, ...] = (
    "sigma", "B_note", "f0_hz",
    "note_B_hat", "note_tau1_alpha", "note_A0_beta", "note_A0_mu", "note_gamma",
    "note_fit_quality",
)


# ---------------------------------------------------------------------------
# Dispatch helpers
# ---------------------------------------------------------------------------

def to_synth_csv(
    bank: Bank,
    *,
    exclude_extrapolated: bool = False,
) -> str:
    """Flat per-partial CSV — one row per (midi, velocity, k)."""
    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(SYNTH_CSV_COLUMNS)
    for note in bank.notes:
        for p in note.partials:
            if exclude_extrapolated and p.origin == "extrapolated":
                continue
            writer.writerow([
                note.midi, note.vel, p.k,
                p.f_hz, p.A0, p.tau1, p.tau2, p.a1, p.beat_hz, p.phi,
                p.fit_quality, p.origin,
            ])
    logger.info(
        "export.synth_csv",
        extra={"bank_id": bank.id, "n_notes": len(bank.notes), "exclude_extrapolated": exclude_extrapolated},
    )
    return buf.getvalue()


def to_analysis_csv(
    bank: Bank,
    *,
    exclude_extrapolated: bool = False,
) -> str:
    """Analysis CSV — synth columns + per-note fit diagnostics.

    One physical_fit pass per note up-front, then fanned out per partial so
    each row carries the same note-level stats. Cheap: O(n_notes) extra work
    on top of the O(n_partials) row emission.
    """
    fits: dict[tuple[int, int], NoteMathDiag] = {}
    for note in bank.notes:
        fits[(note.midi, note.vel)] = fit_note(note)

    buf = io.StringIO()
    writer = csv.writer(buf)
    writer.writerow(SYNTH_CSV_COLUMNS + ANALYSIS_CSV_EXTRA)
    for note in bank.notes:
        diag = fits[(note.midi, note.vel)]
        for p in note.partials:
            if exclude_extrapolated and p.origin == "extrapolated":
                continue
            writer.writerow([
                note.midi, note.vel, p.k,
                p.f_hz, p.A0, p.tau1, p.tau2, p.a1, p.beat_hz, p.phi,
                p.fit_quality, p.origin,
                # extras
                p.sigma if p.sigma is not None else "",
                note.B, note.f0_hz,
                diag.B_hat if diag.B_hat is not None else "",
                diag.tau1_alpha if diag.tau1_alpha is not None else "",
                diag.A0_beta if diag.A0_beta is not None else "",
                diag.A0_mu if diag.A0_mu is not None else "",
                diag.gamma if diag.gamma is not None else "",
                diag.physical_prior_fit_quality,
            ])
    logger.info(
        "export.analysis_csv",
        extra={"bank_id": bank.id, "n_notes": len(bank.notes)},
    )
    return buf.getvalue()


def to_ndjson(
    bank: Bank,
    *,
    include_anchors: bool = True,
) -> Iterator[str]:
    """Stream the bank as newline-delimited JSON.

    First line — metadata header.
    Subsequent lines — one per note, each a full ICR-style dict.
    Optional anchors line after notes.
    """
    header = {
        "icr_version": 2,
        "bank_id": bank.id,
        "parent_id": bank.parent_id,
        "metadata": dict(bank.metadata),
        "n_notes": len(bank.notes),
    }
    yield json.dumps(header, ensure_ascii=False) + "\n"
    for note in bank.notes:
        row = {"__note__": True, "key": note.note_key, **note.to_icr_dict()}
        yield json.dumps(row, ensure_ascii=False) + "\n"
    if include_anchors and bank.anchors:
        for a in bank.anchors:
            yield json.dumps({"__anchor__": True, **a.as_dict()}, ensure_ascii=False) + "\n"
    logger.info(
        "export.ndjson",
        extra={"bank_id": bank.id, "n_notes": len(bank.notes), "n_anchors": len(bank.anchors)},
    )


# ---------------------------------------------------------------------------
# Bank hashing for deterministic-replay round-trip
# ---------------------------------------------------------------------------

def bank_hash(bank: Bank) -> str:
    """Stable content hash of a Bank — used by round-trip tests.

    Ignores timestamps (they drift trivially on serialization) but covers every
    note, every partial, and every anchor. Not cryptographically secure —
    SHA-256 over a canonicalised JSON view is good enough for "did the replay
    reproduce the bank?" questions.
    """
    import hashlib
    payload = bank.to_icr_dict()
    # Strip metadata fields that would legitimately change between runs
    if "metadata" in payload:
        payload = {**payload, "metadata": _strip_timestamp(payload["metadata"])}
    # Strip anchor.created_at since it is a wall-clock timestamp
    if "anchors" in payload:
        payload = {
            **payload,
            "anchors": [
                {k: v for k, v in a.items() if k != "created_at"}
                for a in payload["anchors"]
            ],
        }
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _strip_timestamp(meta: dict) -> dict:
    return {k: v for k, v in meta.items() if k not in ("created", "created_at")}
