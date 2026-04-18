"""Calibrate SIGMA_0 priors from pairwise dispersion across all reference banks.

Auto-discovers all *.json files in idea/, filters ICR-format banks, and computes
per-parameter dispersion from every (bank_i, bank_j) pair at matching (midi, vel, k).
Prints suggested SIGMA_0 values for piano_core/constants.py.

Usage:
    python scripts/calibrate_sigma0.py
"""

from __future__ import annotations

import json
import math
from itertools import combinations
from pathlib import Path
from typing import Sequence

import numpy as np

REPO_ROOT = Path(__file__).resolve().parent.parent
IDEA_DIR = REPO_ROOT / "idea"

# Parameters whose absolute value we compare directly.
ABS_PARAMS = ("tau1", "tau2", "A0", "a1", "beat_hz")
# Frequency: we compare the dimensionless inharmonicity residual instead of f_hz.
# f_coef = f_hz / (k * f0 * sqrt(1 + B*k²)) - 1
# This is scale-invariant; a flat prior on f_hz makes no sense since f_hz spans 3+ orders of magnitude.


def load_bank(path: Path) -> dict:
    with path.open("r", encoding="utf-8") as f:
        return json.load(f)


def is_icr_format(bank: dict) -> bool:
    """Return True iff bank has the expected ICR structure (metadata + notes dict with partials)."""
    if not isinstance(bank, dict):
        return False
    notes = bank.get("notes")
    if not isinstance(notes, dict) or not notes:
        return False
    sample_note = next(iter(notes.values()))
    if not isinstance(sample_note, dict):
        return False
    partials = sample_note.get("partials")
    return isinstance(partials, list) and len(partials) > 0 and isinstance(partials[0], dict)


def discover_banks(idea_dir: Path) -> list[tuple[str, dict]]:
    """Load every *.json in idea/ and return [(name, bank)] for ICR-format banks only."""
    found: list[tuple[str, dict]] = []
    for path in sorted(idea_dir.glob("*.json")):
        try:
            bank = load_bank(path)
        except json.JSONDecodeError as exc:
            print(f"  [skip] {path.name}: invalid JSON ({exc})")
            continue
        if is_icr_format(bank):
            found.append((path.stem, bank))
        else:
            print(f"  [skip] {path.name}: not ICR format (no metadata/notes/partials)")
    return found


def iter_note_keys(bank: dict) -> list[str]:
    return [k for k in bank.get("notes", {}).keys()]


def collect_pairs(bank1: dict, bank2: dict) -> dict[str, list[tuple[float, float]]]:
    """For each parameter, collect (bank1_value, bank2_value) pairs at matching (midi, vel, k)."""
    pairs: dict[str, list[tuple[float, float]]] = {p: [] for p in ABS_PARAMS}
    pairs["f_coef"] = []

    notes1 = bank1.get("notes", {})
    notes2 = bank2.get("notes", {})
    common_keys = set(notes1.keys()) & set(notes2.keys())

    for nkey in sorted(common_keys):
        n1 = notes1[nkey]
        n2 = notes2[nkey]

        f0_1 = n1.get("f0_hz") or 0.0
        f0_2 = n2.get("f0_hz") or 0.0
        B_1 = n1.get("B") or 0.0
        B_2 = n2.get("B") or 0.0

        # Index partials by k for O(1) matching
        p1_by_k = {p["k"]: p for p in n1.get("partials", []) if "k" in p}
        p2_by_k = {p["k"]: p for p in n2.get("partials", []) if "k" in p}
        common_ks = set(p1_by_k) & set(p2_by_k)

        for k in sorted(common_ks):
            p1 = p1_by_k[k]
            p2 = p2_by_k[k]

            # Absolute-value parameters
            for name in ABS_PARAMS:
                v1 = p1.get(name)
                v2 = p2.get(name)
                if v1 is None or v2 is None:
                    continue
                if not (math.isfinite(v1) and math.isfinite(v2)):
                    continue
                pairs[name].append((float(v1), float(v2)))

            # f_coef: inharmonicity residual
            f1 = p1.get("f_hz")
            f2 = p2.get("f_hz")
            if f1 is None or f2 is None or f0_1 <= 0 or f0_2 <= 0:
                continue
            # Expected frequency from inharmonicity model
            expected_1 = k * f0_1 * math.sqrt(1.0 + B_1 * k * k)
            expected_2 = k * f0_2 * math.sqrt(1.0 + B_2 * k * k)
            if expected_1 <= 0 or expected_2 <= 0:
                continue
            c1 = f1 / expected_1 - 1.0
            c2 = f2 / expected_2 - 1.0
            pairs["f_coef"].append((c1, c2))

    return pairs


def robust_sigma_0(pair_values: Sequence[tuple[float, float]], *, ignore_zero: bool = False) -> dict:
    """Estimate per-measurement sigma_0 from a list of paired observations.

    Given pairs (x, y) that are two independent measurements of the same underlying value,
    var(x - y) = 2 * sigma_0^2, so sigma_0 = std(x - y) / sqrt(2).

    Uses MAD-based robust estimator to reduce sensitivity to real anomalies.
    """
    if not pair_values:
        return {"n": 0}

    arr = np.array(pair_values, dtype=float)
    diff = arr[:, 0] - arr[:, 1]

    if ignore_zero:
        nz_mask = ~((arr[:, 0] == 0.0) & (arr[:, 1] == 0.0))
        diff = diff[nz_mask]
        arr = arr[nz_mask]
        if diff.size == 0:
            return {"n": 0}

    n_identical = int(np.sum(diff == 0.0))
    frac_identical = n_identical / diff.size if diff.size else 0.0

    abs_diff = np.abs(diff)
    # MAD-based robust std of (x - y), divided by sqrt(2) for per-measurement sigma
    mad = float(np.median(np.abs(diff - np.median(diff))))
    sigma_0_mad = 1.4826 * mad / math.sqrt(2.0)

    # Classical std (dispersion including tails)
    sigma_0_std = float(np.std(diff, ddof=1)) / math.sqrt(2.0)

    # Percentile-based estimator: use only differing pairs, then p84 / sqrt(2) is approx sigma
    differing = abs_diff[abs_diff > 0]
    sigma_0_p84 = float(np.percentile(differing, 84)) / math.sqrt(2.0) if differing.size else 0.0

    magnitudes = np.abs(arr).mean(axis=1)
    rel = abs_diff / np.maximum(magnitudes, 1e-12)

    return {
        "n": int(arr.shape[0]),
        "n_identical": n_identical,
        "frac_identical": frac_identical,
        "n_differing": int(differing.size),
        "sigma_0_mad": sigma_0_mad,
        "sigma_0_std": sigma_0_std,
        "sigma_0_p84_differing": sigma_0_p84,
        "p50_abs_diff": float(np.median(abs_diff)),
        "p95_abs_diff": float(np.percentile(abs_diff, 95)),
        "max_abs_diff": float(np.max(abs_diff)),
        "median_magnitude": float(np.median(magnitudes)),
        "rel_median": float(np.median(rel)),
        "rel_p95": float(np.percentile(rel, 95)),
    }


def main() -> int:
    if not IDEA_DIR.exists():
        print(f"idea/ directory not found at {IDEA_DIR}")
        return 1

    print(f"Discovering banks in {IDEA_DIR} ...")
    banks = discover_banks(IDEA_DIR)
    if len(banks) < 2:
        print(f"Need at least 2 ICR-format banks; found {len(banks)}.")
        return 1

    print(f"\nFound {len(banks)} ICR-format bank(s):")
    for name, b in banks:
        meta = b.get("metadata", {})
        n_notes = len(b.get("notes", {}))
        k_max = meta.get("k_max", "?")
        instr = meta.get("instrument_name") or "(unnamed)"
        src = meta.get("source", "?")
        print(f"  - {name}: notes={n_notes}, k_max={k_max}, instrument={instr!r}, source={src!r}")

    # Compute pairwise diffs across all (i, j) bank combinations; aggregate per parameter.
    all_params = list(ABS_PARAMS) + ["f_coef"]
    pairs: dict[str, list[tuple[float, float]]] = {p: [] for p in all_params}
    per_pair_counts: list[tuple[str, str, int]] = []
    for (name_i, bank_i), (name_j, bank_j) in combinations(banks, 2):
        pair_data = collect_pairs(bank_i, bank_j)
        n_common = sum(len(v) for v in pair_data.values()) // len(all_params) if all_params else 0
        per_pair_counts.append((name_i, name_j, n_common))
        for key, vals in pair_data.items():
            pairs.setdefault(key, []).extend(vals)

    print(f"\nPairwise coverage ({len(per_pair_counts)} bank pairs):")
    for a, b, n in per_pair_counts:
        print(f"  {a} x {b}: ~{n} matched partials per param")

    print("\n=== SIGMA_0 calibration from pairwise dispersion ===")
    print("(robust estimator: sigma_0 = 1.4826 * MAD(x - y) / sqrt(2))")
    print()

    all_stats = {}
    for name in ("tau1", "tau2", "A0", "a1", "f_coef", "beat_hz"):
        ignore_zero = (name == "beat_hz")
        all_stats[name] = robust_sigma_0(pairs[name], ignore_zero=ignore_zero)

    print(f"{'param':>8} | {'n':>6} | {'%ident':>7} | {'sigma_0 (MAD)':>14} | {'sigma_0 (std)':>14} | {'sigma_0 (p84 differ)':>20} | {'rel% median':>11} | {'median |val|':>12}")
    print("-" * 125)
    for name, s in all_stats.items():
        if s["n"] == 0:
            print(f"{name:>8} | no data")
            continue
        ident_pct = s['frac_identical'] * 100.0
        print(
            f"{name:>8} | {s['n']:>6} | {ident_pct:>6.1f}% | "
            f"{s['sigma_0_mad']:>14.6g} | "
            f"{s['sigma_0_std']:>14.6g} | "
            f"{s['sigma_0_p84_differing']:>20.6g} | "
            f"{s['rel_median']*100:>10.2f}% | "
            f"{s['median_magnitude']:>12.6g}"
        )

    print()
    print("Observations:")
    print("  - 'sigma_0 (MAD)' is robust but collapses to 0 if >50% of pairs are identical.")
    print("  - 'sigma_0 (std)' is sensitive to outliers (real anomalies inflate it).")
    print("  - 'sigma_0 (p84 differ)' uses only pairs that actually differ — most usable when")
    print("    two banks are largely derived from the same raw recordings.")
    print()

    # Estimator selection:
    #   1. Prefer MAD when it is non-zero (robust against outliers).
    #   2. Fall back to p84 over differing-only pairs when MAD collapses to zero —
    #      this happens for bimodal parameters like `a1` where >half the population
    #      sits exactly at 1.0 but a long tail exists.
    #   3. If even p84 is zero, emit warning and use a minimum floor (spec heuristic).
    SPEC_HEURISTIC = {"tau1": 0.06, "tau2": 0.08, "A0": 0.12, "a1": 0.05, "f_coef": 0.003, "beat_hz": 0.30}

    print("=== Recommended SIGMA_0 (context-aware) ===\n")
    print("SIGMA_0: Final[dict[str, float]] = {")
    for name in ("tau1", "tau2", "A0", "a1", "f_coef", "beat_hz"):
        s = all_stats[name]
        if s.get("n", 0) == 0:
            val = SPEC_HEURISTIC[name]
            source = "spec heuristic (no data)"
        elif s["sigma_0_mad"] > 0:
            val = s["sigma_0_mad"]
            source = "MAD"
        elif s["sigma_0_p84_differing"] > 0:
            val = s["sigma_0_p84_differing"]
            source = "p84-differing (MAD=0, bimodal distribution)"
        else:
            val = SPEC_HEURISTIC[name]
            source = "spec heuristic (all pairs identical)"

        if val > 0:
            exp = math.floor(math.log10(abs(val)))
            rounded = round(val, -(exp - 2))
        else:
            rounded = val
        print(f'    "{name}": {rounded:.4g},   # source: {source}, n={s.get("n", 0)}, %identical={s.get("frac_identical", 0)*100:.0f}%')
    print("}")
    print()

    # Context-aware warning
    high_identity = [n for n in ("tau1", "tau2", "A0", "a1", "f_coef")
                     if all_stats[n].get("frac_identical", 0) > 0.9]
    if high_identity:
        print(f"WARNING: parameters with >90% identical pairs: {high_identity}")
        print("  Some bank pairs likely share raw source data. Calibration still valid in aggregate")
        print("  because all bank pairs are pooled, but single-pair SIGMA_0 would be misleading.")
    else:
        print("Calibration looks healthy — no parameter has >90% identical pairs across the pooled set.")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
