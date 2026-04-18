"""ToneIdentifyAndCorrect — composite operator (Phase A + Phase B + Phase C).

Takes a target note and a set of reference banks, identifies the consensus
spectral profile, and applies per-partial corrections back onto the target.
Returns an ApplyResult per the F-5 contract.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import ClassVar, Literal, Sequence

from piano_core.models.bank import Bank
from piano_core.operators.base import (
    ApplyDiagnostics,
    ApplyResult,
    EditRecord,
    Operator,
    OperatorParams,
    OperatorRegistry,
)

from .decision_tree import DecisionParams, apply_correction
from .phase_a_identify import Source, identify_tone
from .provenance import TonalReference


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Params + diagnostics
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class ToneCorrectionParams(OperatorParams):
    """User-facing configuration for ToneIdentifyAndCorrect."""

    target_note_id: tuple[int, int] = (60, 5)
    reference_bank_ids: tuple[str, ...] = ()          # IDs the operator loads externally
    parameters: tuple[str, ...] = ("tau1", "tau2", "A0", "a1", "beat_hz")
    use_anchors: bool = True
    use_physical_prior: bool = True
    preserve_fundamental: bool = True
    noise_threshold_d: float = 1.0
    correction_threshold_d: float = 2.5
    fill_quality_threshold: float = 0.3
    fallback_on_insufficient: Literal["error", "skip", "prior_only"] = "prior_only"
    min_sources_for_consensus: int = 2


@dataclass(frozen=True)
class ToneCorrectionDiagnostics(ApplyDiagnostics):
    """Per-operator diagnostics — surfaced to clients via ApplyResult."""

    per_partial_log: tuple[dict, ...] = ()
    reference_summary: dict | None = None
    target_note_id: tuple[int, int] = (0, 0)
    n_changed: int = 0
    n_filled: int = 0
    n_unchanged: int = 0

    def as_dict(self) -> dict:
        base = super().as_dict()
        base.update({
            "per_partial_log": list(self.per_partial_log),
            "reference_summary": self.reference_summary,
            "target_note_id": list(self.target_note_id),
            "n_changed": self.n_changed,
            "n_filled": self.n_filled,
            "n_unchanged": self.n_unchanged,
        })
        return base


# ---------------------------------------------------------------------------
# Operator
# ---------------------------------------------------------------------------

class ToneIdentifyAndCorrectOperator(Operator[ToneCorrectionParams]):
    """Phase A → Phase B → Phase C pipeline.

    The target bank is the ``bank`` argument to `apply`. Reference banks must
    be preloaded by the caller and supplied via `apply_with_sources` — this
    keeps the operator pure (no IO) and testable. The FastAPI router layer
    wires reference loading before calling in.
    """

    name: ClassVar[str] = "ToneIdentifyAndCorrect"
    params_class: ClassVar[type[OperatorParams]] = ToneCorrectionParams

    def apply(self, bank: Bank, params: ToneCorrectionParams) -> ApplyResult:
        """Apply correction using the target bank as its own only source.

        This is the minimal "no reference bank" path — useful primarily when
        the user wants to validate the pipeline on self-data (anchors-only
        driven correction). Production usage goes through `apply_with_sources`
        to pass external references explicitly.
        """
        target_note = bank.get_note(*params.target_note_id)
        if target_note is None:
            raise ValueError(f"target note {params.target_note_id} not in bank {bank.id!r}")
        sources = [Source(bank_id=bank.id, note=target_note,
                          anchors=bank.anchors_for_note(*params.target_note_id) if params.use_anchors else ())]
        return self.apply_with_sources(bank, params, sources=sources)

    def apply_with_sources(
        self,
        bank: Bank,
        params: ToneCorrectionParams,
        *,
        sources: Sequence[Source],
    ) -> ApplyResult:
        """Full Phase A + B + C with explicit reference sources."""
        midi, velocity = params.target_note_id
        target_note = bank.get_note(midi, velocity)
        if target_note is None:
            raise ValueError(f"target note {params.target_note_id} not in bank {bank.id!r}")

        warnings: list[str] = []

        # Enforce min_sources_for_consensus gate per params.fallback_on_insufficient.
        if len(sources) < params.min_sources_for_consensus:
            if params.fallback_on_insufficient == "error":
                raise ValueError(
                    f"only {len(sources)} source(s) available; "
                    f"min_sources_for_consensus={params.min_sources_for_consensus}"
                )
            if params.fallback_on_insufficient == "skip":
                # No-op — return bank unchanged
                diag = ToneCorrectionDiagnostics(
                    warnings=(f"skipped: {len(sources)} < {params.min_sources_for_consensus} sources",),
                    target_note_id=params.target_note_id,
                )
                edit = EditRecord.now(operator=self.name, params=params)
                return ApplyResult(bank=bank, edit=edit, diagnostics=diag)
            # "prior_only" — proceed with whatever sources we have, log a warning
            warnings.append(
                f"insufficient_sources: {len(sources)} < {params.min_sources_for_consensus}, "
                "proceeding with available sources + priors"
            )

        # Phase A: identify_tone
        tonal_ref: TonalReference = identify_tone(
            midi=midi,
            velocity=velocity,
            sources=sources,
            parameters=params.parameters,
            use_physical_prior=params.use_physical_prior,
            random_seed=params.random_seed,
        )
        warnings.extend(tonal_ref.warnings)

        # Build anchored-k hint set so Phase B knows which references came from anchors.
        # A "hard" anchor is recognised by weight ≥ 0.999; we treat its k as anchored per parameter.
        anchored_k_per_param: dict[str, set[int]] = {}
        if params.use_anchors:
            for a in bank.anchors_for_note(midi, velocity):
                if a.parameter in params.parameters and a.weight >= 0.999:
                    anchored_k_per_param.setdefault(a.parameter, set()).add(a.k)

        # Phase B: apply_correction
        decision_params = DecisionParams(
            noise_threshold_d=params.noise_threshold_d,
            correction_threshold_d=params.correction_threshold_d,
            fill_quality_threshold=params.fill_quality_threshold,
            preserve_fundamental=params.preserve_fundamental,
        )
        outcome = apply_correction(
            partials=target_note.partials,
            reference=tonal_ref,
            parameters=params.parameters,
            params=decision_params,
            anchored_k_per_param=anchored_k_per_param,
        )

        # Phase C: build new Bank with corrected note
        new_note = replace(target_note, partials=outcome.partials)
        new_bank_id = f"{bank.id}.tic-{_short_hash(midi, velocity, len(outcome.log))}"
        new_bank = bank.with_updated_note(new_note, new_id=new_bank_id)

        # Tally log actions
        n_changed = sum(1 for e in outcome.log if e.action in ("soft_blend", "hard_replace"))
        n_filled = sum(1 for e in outcome.log if e.action == "fill")
        n_unchanged = sum(1 for e in outcome.log if e.action == "none")

        diag = ToneCorrectionDiagnostics(
            warnings=tuple(warnings),
            per_partial_log=tuple(e.as_dict() for e in outcome.log),
            reference_summary=tonal_ref.as_summary_dict(),
            target_note_id=params.target_note_id,
            n_changed=n_changed,
            n_filled=n_filled,
            n_unchanged=n_unchanged,
        )
        edit = EditRecord.now(
            operator=self.name,
            params=params,
            source_note_id=params.target_note_id,
        )

        logger.info(
            "tone_identify_and_correct.apply",
            extra={
                "bank_id": bank.id, "new_bank_id": new_bank.id,
                "target": params.target_note_id,
                "n_sources": len(sources),
                "n_changed": n_changed, "n_filled": n_filled, "n_unchanged": n_unchanged,
            },
        )

        return ApplyResult(bank=new_bank, edit=edit, diagnostics=diag)


def _short_hash(*vals) -> str:
    """Short deterministic suffix for the new bank id. Uses built-in hash of the tuple
    normalised to 8 hex chars."""
    h = hash(vals) & 0xFFFFFFFF
    return f"{h:08x}"


# Register for deterministic replay (i5)
try:
    OperatorRegistry.register(ToneIdentifyAndCorrectOperator)
except ValueError:
    # Already registered (happens on test reimports) — safe to ignore.
    pass
