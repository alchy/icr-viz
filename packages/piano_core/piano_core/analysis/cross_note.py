"""Cross-note monotonicity checks — does a parameter vary smoothly with MIDI?

For each triple (velocity, k, parameter), sort observed partials by midi and
check whether the values follow the physically-expected direction (e.g. tau1
should decrease with midi — higher pitches decay faster).

Reports violations per (midi_from → midi_to) pair that break the trend by
more than a tolerance threshold.
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Iterable, Literal, Mapping, Sequence

import numpy as np

from piano_core.models.note import Note


logger = logging.getLogger(__name__)


MonotoneDirection = Literal["decreasing", "increasing"]


# Physical expectations per spec i4 §3.3 table.
EXPECTED_DIRECTION: Mapping[str, MonotoneDirection] = {
    "tau1": "decreasing",
    "tau2": "decreasing",
    "A0":   "decreasing",
    # a1 (coupling) and beat_hz don't have reliable cross-note monotonicity — caller may skip.
    # B (inharmonicity) is handled by fit-level analysis, not per-k cross-note.
}


@dataclass(frozen=True)
class MonotonicityViolation:
    parameter: str
    velocity: int
    k: int
    midi_from: int
    midi_to: int
    expected_direction: MonotoneDirection
    delta: float
    severity: Literal["minor", "major"]

    def as_dict(self) -> dict:
        return {
            "parameter": self.parameter,
            "velocity": self.velocity,
            "k": self.k,
            "midi_from": self.midi_from,
            "midi_to": self.midi_to,
            "expected_direction": self.expected_direction,
            "delta": self.delta,
            "severity": self.severity,
        }


def check_monotonicity(
    notes: Sequence[Note],
    *,
    parameters: Iterable[str] | None = None,
    run_length_minor: int = 1,
    run_length_major: int = 3,
) -> list[MonotonicityViolation]:
    """Scan all (velocity, k, parameter) triples for cross-note trend violations.

    Parameters
    ----------
    notes : all notes in the bank.
    parameters : which params to check. Defaults to entries in EXPECTED_DIRECTION.
    run_length_minor / run_length_major : controls severity tagging:
        a single-pair violation is "minor"; runs of `run_length_major` or more
        adjacent pairs in the same direction are "major".
    """
    params = tuple(parameters) if parameters is not None else tuple(EXPECTED_DIRECTION.keys())

    # Group notes by velocity
    by_vel: dict[int, list[Note]] = {}
    for note in notes:
        by_vel.setdefault(note.vel, []).append(note)

    violations: list[MonotonicityViolation] = []
    for vel, group in by_vel.items():
        group = sorted(group, key=lambda n: n.midi)
        if len(group) < 2:
            continue

        # Collect values per (k, parameter) across the sorted midi axis
        for parameter in params:
            direction = EXPECTED_DIRECTION.get(parameter)
            if direction is None:
                continue
            # Find max k present across this velocity group
            max_k = 0
            for n in group:
                for p in n.partials:
                    if p.k > max_k:
                        max_k = p.k

            for k in range(1, max_k + 1):
                series: list[tuple[int, float]] = []
                for n in group:
                    partial = next((p for p in n.partials if p.k == k), None)
                    if partial is None:
                        continue
                    val = getattr(partial, parameter, None)
                    if val is None or not math.isfinite(val):
                        continue
                    series.append((n.midi, float(val)))

                if len(series) < 2:
                    continue

                violations.extend(_check_series(
                    series, velocity=vel, k=k,
                    parameter=parameter, direction=direction,
                    run_length_major=run_length_major,
                ))

    logger.info(
        "cross_note.check",
        extra={
            "n_notes": len(notes),
            "n_violations": len(violations),
            "parameters": list(params),
        },
    )
    return violations


def _check_series(
    series: list[tuple[int, float]],
    *,
    velocity: int,
    k: int,
    parameter: str,
    direction: MonotoneDirection,
    run_length_major: int,
) -> list[MonotonicityViolation]:
    """Check adjacent-pair violations on one sorted-by-midi series."""
    # Violation sign convention:
    #   - "decreasing": we expect delta = v1 - v0 < 0. Violation when delta > +threshold.
    #     So sign=+1 and `delta * sign > threshold` triggers on upward jumps.
    #   - "increasing": we expect delta > 0. Violation when delta < -threshold.
    #     So sign=-1 and `delta * sign > threshold` triggers on downward jumps.
    sign = 1.0 if direction == "decreasing" else -1.0
    # Robust threshold: 10% of the series' absolute median. This avoids flagging
    # noise near zero.
    values = np.array([v for _, v in series])
    positive_values = values[values > 0]
    reference = float(np.median(np.abs(positive_values))) if positive_values.size else 1.0
    threshold = 0.1 * reference

    violations: list[MonotonicityViolation] = []
    # Collect raw pair deltas plus a "violates" flag so we can group into runs.
    flags: list[bool] = []
    deltas: list[float] = []
    for (m0, v0), (m1, v1) in zip(series, series[1:]):
        delta = v1 - v0
        # Violation when delta has wrong sign AND |delta| > threshold.
        violates = (delta * sign > threshold)
        flags.append(violates)
        deltas.append(delta)

    # Find runs of consecutive violations. A single pair = minor, runs of length
    # >= run_length_major = major.
    i = 0
    while i < len(flags):
        if not flags[i]:
            i += 1
            continue
        run_start = i
        while i < len(flags) and flags[i]:
            i += 1
        run_len = i - run_start
        severity: Literal["minor", "major"] = "major" if run_len >= run_length_major else "minor"
        for j in range(run_start, i):
            m0 = series[j][0]
            m1 = series[j + 1][0]
            violations.append(MonotonicityViolation(
                parameter=parameter,
                velocity=velocity,
                k=k,
                midi_from=m0,
                midi_to=m1,
                expected_direction=direction,
                delta=deltas[j],
                severity=severity,
            ))
    return violations
