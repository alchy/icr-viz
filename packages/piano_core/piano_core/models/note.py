"""Note — one recorded (midi, velocity) sample with its partials.

Models the additive representation used by ICR banks (8 velocity layers per
MIDI note, each with up to `k_max` partials). Physical-mode notes (single
layer, 16 scalar params, no partials) are not modelled in i1 — all 5 reference
banks in `idea/` are additive.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from .partial import Partial


_NOTE_KEY_RE = re.compile(r"^m0*(?P<midi>\d+)(?:_vel(?P<vel>\d+))?$")


@dataclass(frozen=True, slots=True)
class Note:
    """One (midi, vel) sample with its additive parameters and partials."""

    midi: int
    vel: int                                 # 0..7 for additive banks; 0 for physical
    f0_hz: float                             # fundamental frequency
    B: float                                 # inharmonicity coefficient
    partials: tuple[Partial, ...] = ()

    # Per-note additive scalars (typical ICR bank fields)
    phi_diff: float = 0.0
    attack_tau: float = 0.0
    A_noise: float = 0.0
    noise_centroid_hz: float = 0.0
    rms_gain: float = 0.0

    # Optional per-note scalars — absent in older banks
    n_strings: int | None = None
    rise_tau: float | None = None
    stereo_width: float | None = None

    # Extensible bucket for bank-specific extras (eq_biquads, spectral_eq, etc.).
    # Stored as immutable mapping via tuple of (key, json_value) pairs to keep
    # the dataclass hashable. Helpers below cast to dict on demand.
    extras: tuple[tuple[str, Any], ...] = field(default_factory=tuple)

    # ---- construction --------------------------------------------------

    @classmethod
    def from_icr_dict(
        cls,
        d: dict[str, Any],
        *,
        note_key: str | None = None,
    ) -> "Note":
        """Parse a single note dict.

        If (midi, vel) are absent on the dict, they are inferred from the
        note key (e.g. ``m021_vel0`` -> midi=21, vel=0).
        """
        midi = _coerce_int(d.get("midi"), fallback=None)
        vel = _coerce_int(d.get("vel"), fallback=None)
        if (midi is None or vel is None) and note_key is not None:
            m = _NOTE_KEY_RE.match(note_key)
            if not m:
                raise ValueError(f"cannot parse note key: {note_key!r}")
            if midi is None:
                midi = int(m.group("midi"))
            if vel is None:
                vel = int(m.group("vel") or 0)
        if midi is None:
            raise ValueError("Note dict missing `midi` and no parseable key")
        if vel is None:
            vel = 0

        partials_list = d.get("partials") or []
        if not isinstance(partials_list, list):
            raise TypeError(f"partials must be a list, got {type(partials_list).__name__}")
        partials = tuple(Partial.from_icr_dict(p) for p in partials_list)

        known_keys = {
            "midi", "vel", "f0_hz", "B", "partials",
            "phi_diff", "attack_tau", "A_noise", "noise_centroid_hz", "rms_gain",
            "n_strings", "rise_tau", "stereo_width",
        }
        extras = tuple((k, v) for k, v in d.items() if k not in known_keys)

        return cls(
            midi=int(midi),
            vel=int(vel),
            f0_hz=float(d.get("f0_hz", 0.0)),
            B=float(d.get("B", 0.0)),
            partials=partials,
            phi_diff=float(d.get("phi_diff", 0.0)),
            attack_tau=float(d.get("attack_tau", 0.0)),
            A_noise=float(d.get("A_noise", 0.0)),
            noise_centroid_hz=float(d.get("noise_centroid_hz", 0.0)),
            rms_gain=float(d.get("rms_gain", 0.0)),
            n_strings=_opt_int(d.get("n_strings")),
            rise_tau=_opt_float(d.get("rise_tau")),
            stereo_width=_opt_float(d.get("stereo_width")),
            extras=extras,
        )

    # ---- accessors -----------------------------------------------------

    @property
    def note_key(self) -> str:
        """Return canonical ICR note key, e.g. ``m021_vel0``."""
        return f"m{self.midi:03d}_vel{self.vel}"

    @property
    def id(self) -> tuple[int, int]:
        return (self.midi, self.vel)

    def partial_by_k(self, k: int) -> Partial | None:
        for p in self.partials:
            if p.k == k:
                return p
        return None

    def extras_dict(self) -> dict[str, Any]:
        return dict(self.extras)

    # ---- serialization -------------------------------------------------

    def to_icr_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {
            "midi": self.midi,
            "vel": self.vel,
            "f0_hz": self.f0_hz,
            "B": self.B,
            "phi_diff": self.phi_diff,
            "attack_tau": self.attack_tau,
            "A_noise": self.A_noise,
            "noise_centroid_hz": self.noise_centroid_hz,
            "rms_gain": self.rms_gain,
        }
        if self.n_strings is not None:
            out["n_strings"] = self.n_strings
        if self.rise_tau is not None:
            out["rise_tau"] = self.rise_tau
        if self.stereo_width is not None:
            out["stereo_width"] = self.stereo_width
        for key, value in self.extras:
            out[key] = value
        out["partials"] = [p.to_icr_dict() for p in self.partials]
        return out


def _coerce_int(v: Any, *, fallback: int | None) -> int | None:
    if v is None:
        return fallback
    return int(v)


def _opt_int(v: Any) -> int | None:
    if v is None:
        return None
    return int(v)


def _opt_float(v: Any) -> float | None:
    if v is None:
        return None
    return float(v)
