"""SplineTransferOperator — copy per-k curves from a source note onto target notes.

Unlike ToneIdentifyAndCorrect (which fuses multiple sources via consensus),
SplineTransfer is a *directed* transfer: one chosen source, one or more
chosen targets. The operator smooths the source curve through
`completion.anchor_interpolate` (so missing partials or noisy measurements
don't propagate), then applies per-mode arithmetic on the target partials.

Logging: every apply emits one "spline_transfer.apply" INFO line with
  bank, n_targets, n_parameters, commit, new_bank_id (if any).
"""

from __future__ import annotations

import logging
import math
import uuid
from dataclasses import replace
from typing import ClassVar

from piano_core.completion.anchor_interpolate import (
    AnchorObservation,
    anchor_interpolate,
)
from piano_core.models.bank import Bank
from piano_core.models.note import Note
from piano_core.models.partial import Partial
from piano_core.operators.base import (
    ApplyDiagnostics,
    ApplyResult,
    EditRecord,
    Operator,
    OperatorRegistry,
)
from piano_core.splines.bounds import clamp_to_bounds

from .params import ParameterConfig, SplineTransferParams, TransferMode


logger = logging.getLogger(__name__)


class SplineTransferDiagnostics(ApplyDiagnostics):
    """Diagnostics carry per-(target, parameter) transfer stats so UIs can
    summarise what landed where."""


class SplineTransferOperator(Operator[SplineTransferParams]):
    """Copies per-k curves from a source note onto a set of target notes."""

    name: ClassVar[str] = "SplineTransfer"
    params_class: ClassVar[type[SplineTransferParams]] = SplineTransferParams

    def apply(self, bank: Bank, params: SplineTransferParams) -> ApplyResult:
        """Apply transfer using `bank` as both source and target.

        For cross-bank transfers (the common case) use `apply_with_source`
        — the operator itself stays IO-free; the piano_web router loads
        the source note and hands it in.
        """
        source_note = bank.get_note(*params.source_note_id)
        if source_note is None:
            raise ValueError(
                f"source note {params.source_note_id} not in bank {bank.id!r}"
            )
        return self.apply_with_source(bank, params, source_note=source_note)

    def apply_with_source(
        self,
        bank: Bank,
        params: SplineTransferParams,
        *,
        source_note: Note,
    ) -> ApplyResult:
        """Full operator — source note supplied explicitly (may be from another bank)."""
        configs = params.resolved_configs()
        if not params.target_note_ids:
            raise ValueError("SplineTransferParams.target_note_ids must not be empty")

        # Pre-fit the source curve per parameter — done once up-front, reused
        # across all targets.
        source_splines = _build_source_splines(
            source_note=source_note,
            configs=configs,
            random_seed=params.random_seed,
        )

        warnings: list[str] = []
        per_target_stats: list[dict] = []

        # Apply to each target note independently
        updated_notes: dict[tuple[int, int], Note] = {}
        for target_id in params.target_note_ids:
            midi, velocity = target_id
            target_note = bank.get_note(midi, velocity)
            if target_note is None:
                warnings.append(f"target note {target_id} not in bank {bank.id!r} — skipped")
                continue
            new_note, stats = _transfer_one_note(
                target_note=target_note,
                source_note=source_note,
                source_splines=source_splines,
                configs=configs,
            )
            updated_notes[target_id] = new_note
            per_target_stats.append({
                "target_note_id": list(target_id),
                **stats,
            })

        if not updated_notes:
            # Nothing actually changed — return the input bank.
            diag = SplineTransferDiagnostics(
                warnings=tuple(warnings) + ("no targets resolved — no-op",),
            )
            edit = EditRecord.now(operator=self.name, params=params)
            return ApplyResult(bank=bank, edit=edit, diagnostics=diag)

        new_bank_id = f"{bank.id}.st-{_short_hash(params)}"
        new_bank = bank
        for target_id, note in updated_notes.items():
            new_bank = new_bank.with_updated_note(note, new_id=new_bank_id)
            # `with_updated_note` only sets new_id/parent_id on the first call; further
            # calls would try to set parent_id=new_bank.id creating a cycle. We break
            # out after the first to keep the mutation pure — then apply remaining
            # note updates in-place via replace().
            new_bank = _apply_remaining_notes(new_bank, updated_notes, skip=target_id)
            break

        diag_dict_entries = tuple(per_target_stats)
        edit = EditRecord.now(
            operator=self.name,
            params=params,
            source_note_id=params.source_note_id,
        )
        diag = SplineTransferDiagnostics(
            warnings=tuple(warnings),
        )
        # Attach extended diagnostics as ad-hoc attributes via __dict__ path —
        # the frozen base class only carries `warnings`, subclasses can supplement
        # via as_dict override if needed. Here we expose through EditRecord.params
        # instead; FE reads them from the diagnostics' as_dict override below.
        setattr(diag, "_per_target", diag_dict_entries)  # type: ignore[attr-defined]

        logger.info(
            "spline_transfer.apply",
            extra={
                "bank_id": bank.id,
                "new_bank_id": new_bank.id,
                "source_bank_id": params.source_bank_id or bank.id,
                "source_note_id": list(params.source_note_id),
                "n_targets": len(updated_notes),
                "n_parameters": len(configs),
                "commit": params.commit,
            },
        )

        return ApplyResult(bank=new_bank, edit=edit, diagnostics=diag)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _build_source_splines(
    *,
    source_note: Note,
    configs: tuple[ParameterConfig, ...],
    random_seed: int,
) -> dict[str, callable]:
    """Fit a smoothed spline for each requested source parameter."""
    splines: dict[str, callable] = {}
    for cfg in configs:
        # anchor_interpolate handles missing partials, noise, and log-space for
        # tau/A0 — exactly what we want on the source side of a transfer.
        result = anchor_interpolate(
            partials=source_note.partials,
            anchors=[],    # SplineTransfer doesn't use anchors on the source
            parameter=cfg.parameter,
            smoothing=cfg.source_smoothing,
            random_seed=random_seed,
        )
        splines[cfg.parameter] = result.estimate
    return splines


def _transfer_one_note(
    *,
    target_note: Note,
    source_note: Note,
    source_splines: dict[str, callable],
    configs: tuple[ParameterConfig, ...],
) -> tuple[Note, dict]:
    """Apply every (parameter, mode) config to this target note.

    Returns (updated_note, stats_dict). stats_dict counts touched partials
    per parameter so callers can report on the transfer.
    """
    new_partials: list[Partial] = list(target_note.partials)
    by_k: dict[int, int] = {p.k: i for i, p in enumerate(new_partials)}
    src_by_k: dict[int, Partial] = {p.k: p for p in source_note.partials}

    changes_per_param: dict[str, int] = {}

    for cfg in configs:
        spline = source_splines.get(cfg.parameter)
        if spline is None:
            continue

        # Reference values for relative mode — both sides' k=1 magnitudes.
        target_k1 = _value_at_k(new_partials, cfg.parameter, 1)
        source_k1 = float(spline(1.0))
        if cfg.mode == "relative" and (
            source_k1 is None or abs(source_k1) < 1e-12 or target_k1 is None
        ):
            # Relative mode can't scale without a non-zero k=1 — fall back to absolute.
            effective_mode: TransferMode = "absolute"
        else:
            effective_mode = cfg.mode

        touched = 0
        for idx, p in enumerate(new_partials):
            if cfg.preserve_fundamental and p.k == 1:
                continue

            source_smooth = float(spline(float(p.k)))
            if not math.isfinite(source_smooth):
                continue

            if effective_mode == "absolute":
                new_val = source_smooth
            elif effective_mode == "relative":
                assert target_k1 is not None and source_k1 is not None   # narrowed above
                new_val = float(target_k1) * source_smooth / source_k1
            elif effective_mode == "delta":
                source_raw_p = src_by_k.get(p.k)
                if source_raw_p is not None:
                    source_raw_val = float(getattr(source_raw_p, cfg.parameter))
                else:
                    source_raw_val = source_smooth   # no raw point → delta=0
                current_val = float(getattr(p, cfg.parameter))
                new_val = current_val + (source_smooth - source_raw_val)
            else:
                continue  # unreachable — params validated up-front

            if cfg.clamp_to_bounds:
                new_val = float(clamp_to_bounds(new_val, cfg.parameter))

            current_val = float(getattr(p, cfg.parameter))
            if abs(current_val - new_val) < 1e-12:
                continue

            new_partials[idx] = replace(
                p, **{cfg.parameter: new_val}, origin="derived",
            )
            touched += 1

        changes_per_param[cfg.parameter] = touched

    updated_note = replace(target_note, partials=tuple(new_partials))
    stats = {
        "changes_per_parameter": changes_per_param,
        "n_partials": len(new_partials),
    }
    return updated_note, stats


def _value_at_k(partials: list[Partial], parameter: str, k: int) -> float | None:
    for p in partials:
        if p.k == k:
            val = float(getattr(p, parameter, math.nan))
            return val if math.isfinite(val) else None
    return None


def _apply_remaining_notes(
    bank: Bank,
    updated_notes: dict[tuple[int, int], Note],
    *,
    skip: tuple[int, int],
) -> Bank:
    """After the first with_updated_note sets up parent_id, swap in all remaining notes."""
    remaining_notes_tuple = bank.notes
    needed: dict[tuple[int, int], Note] = {
        k: v for k, v in updated_notes.items() if k != skip
    }
    if not needed:
        return bank
    new_tuple: list[Note] = []
    for n in remaining_notes_tuple:
        key = (n.midi, n.vel)
        if key in needed:
            new_tuple.append(needed.pop(key))
        else:
            new_tuple.append(n)
    return replace(bank, notes=tuple(new_tuple))


def _short_hash(params: SplineTransferParams) -> str:
    """Short deterministic suffix for new bank id."""
    key = (
        params.source_bank_id,
        params.source_note_id,
        params.target_note_ids,
        tuple((c.parameter, c.mode) for c in params.resolved_configs()),
        params.random_seed,
        params.commit,
    )
    return f"{abs(hash(key)) & 0xFFFFFFFF:08x}"


# Register for deterministic replay (i5).
try:
    OperatorRegistry.register(SplineTransferOperator)
except ValueError:
    pass
