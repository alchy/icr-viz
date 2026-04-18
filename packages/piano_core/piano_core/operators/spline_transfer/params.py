"""SplineTransfer parameter / mode configuration.

Spec (i3 §4.1-4.2) — three transfer modes:

  - ``absolute``  target gets the source spline value directly:
                  ``new = source_spline(k)``
  - ``relative``  proportional transfer preserving target's k=1 magnitude:
                  ``new = target_at_k1 * source_spline(k) / source_spline(1)``
                  Handy for moving a *shape* while respecting a different
                  loudness / tempo.
  - ``delta``     additive correction — source minus its own raw observation
                  applied on top of the target's measurement:
                  ``new = current + (source_spline(k) - source_raw_at_k(k))``

A SplineTransferParams bundle carries one or more ParameterConfig rows plus
a handful of common knobs (target notes, random seed, commit flag). The legacy
single-parameter API (``SplineTransferParams.single(...)``) is preserved for
backward compatibility with pre-i3 callers.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar, Literal

from piano_core.constants import MATH_PARAMS
from piano_core.operators.base import OperatorParams


TransferMode = Literal["absolute", "relative", "delta"]
VALID_MODES: tuple[TransferMode, ...] = ("absolute", "relative", "delta")


@dataclass(frozen=True, slots=True)
class ParameterConfig:
    """One row in the multi-parameter transfer plan."""

    parameter: str
    mode: TransferMode
    preserve_fundamental: bool = True
    clamp_to_bounds: bool = True
    source_smoothing: float | None = None    # None → GCV default in anchor_interpolate

    def __post_init__(self) -> None:
        if self.parameter not in MATH_PARAMS:
            raise ValueError(
                f"invalid parameter {self.parameter!r}; expected one of {MATH_PARAMS}"
            )
        if self.mode not in VALID_MODES:
            raise ValueError(
                f"invalid mode {self.mode!r}; expected one of {VALID_MODES}"
            )


@dataclass(frozen=True)
class SplineTransferParams(OperatorParams):
    """Full-operator parameters — source note + targets + per-parameter plan."""

    # Source: one (bank_id, midi, velocity) — the operator loads this externally
    # (piano_web router does the bank fetch and hands the Note to the operator).
    source_bank_id: str = ""
    source_note_id: tuple[int, int] = (60, 5)

    # Targets: list of (midi, velocity) tuples on the target bank.
    target_note_ids: tuple[tuple[int, int], ...] = ()

    # Multi-parameter plan
    parameter_configs: tuple[ParameterConfig, ...] = ()

    # Operator toggles
    commit: bool = False

    # Back-compat single-parameter API — if parameter_configs is empty and
    # `legacy_parameter` + `legacy_mode` are set, the operator normalises
    # to a one-entry parameter_configs at apply time.
    legacy_parameter: str | None = None
    legacy_mode: TransferMode | None = None

    SINGLE_ATTR_ALLOWED: ClassVar[set[str]] = {
        "source_bank_id", "source_note_id", "target_note_ids", "commit",
        "random_seed", "preserve_fundamental", "clamp_to_bounds",
        "source_smoothing",
    }

    @classmethod
    def single(
        cls,
        parameter: str,
        mode: TransferMode,
        *,
        preserve_fundamental: bool = True,
        clamp_to_bounds: bool = True,
        source_smoothing: float | None = None,
        **kwargs: Any,
    ) -> "SplineTransferParams":
        """Backward-compat constructor for one-parameter transfers.

        Equivalent to building a SplineTransferParams with a single
        ParameterConfig in parameter_configs.
        """
        unknown = set(kwargs) - cls.SINGLE_ATTR_ALLOWED
        if unknown:
            raise TypeError(f"single() got unexpected kwargs: {sorted(unknown)}")
        cfg = ParameterConfig(
            parameter=parameter, mode=mode,
            preserve_fundamental=preserve_fundamental,
            clamp_to_bounds=clamp_to_bounds,
            source_smoothing=source_smoothing,
        )
        # Drop kwargs not in our dataclass before constructing.
        data = {k: v for k, v in kwargs.items() if k != "source_smoothing"}
        return cls(parameter_configs=(cfg,), **data)

    def resolved_configs(self) -> tuple[ParameterConfig, ...]:
        """Return parameter_configs, synthesising one from legacy fields when empty."""
        if self.parameter_configs:
            return self.parameter_configs
        if self.legacy_parameter is not None and self.legacy_mode is not None:
            return (ParameterConfig(
                parameter=self.legacy_parameter,
                mode=self.legacy_mode,
            ),)
        raise ValueError(
            "SplineTransferParams requires either parameter_configs or "
            "(legacy_parameter + legacy_mode)"
        )
