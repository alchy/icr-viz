"""Partial — a single harmonic component of a piano note (F-2, F-3)."""

from __future__ import annotations

from dataclasses import dataclass, replace
from typing import Any

from piano_core.constants import Origin


@dataclass(frozen=True, slots=True)
class Partial:
    """Single partial measured from a recorded piano note.

    Fields match real ICR bank JSON structure (absolute values — no coefficient
    normalization in storage). See `packages/piano_core/piano_core/constants.py`
    for the full parameter naming convention.

    The two fields added by i1 F-2/F-3 are `sigma` (per-measurement uncertainty
    in the same units as the respective parameter) and `origin` (provenance).
    """

    k: int                                    # partial index (1 = fundamental)
    f_hz: float                               # absolute frequency
    A0: float                                 # amplitude coefficient
    tau1: float                               # fast-component decay time (s)
    tau2: float                               # slow-component decay time (s)
    a1: float                                 # fast/slow coupling, in [0, 1]
    beat_hz: float                            # beat frequency (0 if no beating)
    phi: float                                # phase
    fit_quality: float                        # 0..1; quality of the fit
    sigma: float | None = None                # F-2 per-partial uncertainty; None if unknown
    origin: Origin = "measured"               # F-3 provenance

    @classmethod
    def from_icr_dict(cls, d: dict[str, Any]) -> "Partial":
        """Parse a single partial from ICR JSON (tolerates missing sigma/origin for legacy banks)."""
        return cls(
            k=int(d["k"]),
            f_hz=float(d["f_hz"]),
            A0=float(d["A0"]),
            tau1=float(d["tau1"]),
            tau2=float(d["tau2"]),
            a1=float(d["a1"]),
            beat_hz=float(d.get("beat_hz", 0.0)),
            phi=float(d.get("phi", 0.0)),
            fit_quality=float(d.get("fit_quality", 0.0)),
            sigma=_opt_float(d.get("sigma")),
            origin=_coerce_origin(d.get("origin", "measured")),
        )

    def to_icr_dict(self) -> dict[str, Any]:
        """Serialize to ICR JSON. Only non-default sigma/origin are written."""
        out: dict[str, Any] = {
            "k": self.k,
            "f_hz": self.f_hz,
            "A0": self.A0,
            "tau1": self.tau1,
            "tau2": self.tau2,
            "a1": self.a1,
            "beat_hz": self.beat_hz,
            "phi": self.phi,
            "fit_quality": self.fit_quality,
        }
        if self.sigma is not None:
            out["sigma"] = self.sigma
        if self.origin != "measured":
            out["origin"] = self.origin
        return out

    def with_(self, **overrides: Any) -> "Partial":
        """Return a copy with given fields replaced (immutable update helper)."""
        return replace(self, **overrides)


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)


def _coerce_origin(v: Any) -> Origin:
    s = str(v)
    if s not in ("measured", "derived", "extrapolated", "anchored"):
        raise ValueError(f"invalid origin: {v!r}")
    return s  # type: ignore[return-value]
