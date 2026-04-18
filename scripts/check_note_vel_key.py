"""Verify whether each ICR bank in idea/ carries `vel` key inside the note dict.

Reports:
- Bank-level: does every note in the bank have `vel` on the dict?
- Also checks `midi` key presence, which is the companion field.
- Notes any mismatch between the key pattern (m021_vel0) and the dict values.
"""

from __future__ import annotations

import json
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
IDEA_DIR = REPO_ROOT / "idea"


def check_bank(path: Path) -> None:
    with path.open("r", encoding="utf-8") as f:
        raw = json.load(f)
    notes = raw.get("notes") or {}
    if not isinstance(notes, dict) or not notes:
        print(f"{path.name}: SKIP (no notes dict)")
        return

    n_total = len(notes)
    n_has_vel = 0
    n_has_midi = 0
    n_key_dict_mismatch = 0
    first_key = next(iter(notes))
    first_dict_keys = sorted(notes[first_key].keys())[:8]

    for key, nd in notes.items():
        if not isinstance(nd, dict):
            continue
        if "vel" in nd:
            n_has_vel += 1
        if "midi" in nd:
            n_has_midi += 1

        # Parse expected (midi, vel) from key
        try:
            m_part = key.lstrip("m").split("_")[0]
            expected_midi = int(m_part)
            expected_vel = 0
            if "_vel" in key:
                expected_vel = int(key.split("_vel")[1])
        except Exception:
            continue

        if "midi" in nd and int(nd["midi"]) != expected_midi:
            n_key_dict_mismatch += 1
        if "vel" in nd and int(nd["vel"]) != expected_vel:
            n_key_dict_mismatch += 1

    print(f"{path.name}:")
    print(f"  notes total: {n_total}")
    print(f"  have 'vel' key: {n_has_vel}/{n_total} ({100*n_has_vel/n_total:.0f}%)")
    print(f"  have 'midi' key: {n_has_midi}/{n_total} ({100*n_has_midi/n_total:.0f}%)")
    print(f"  key/dict mismatches: {n_key_dict_mismatch}")
    print(f"  example key: {first_key!r}")
    print(f"  example dict keys (first 8): {first_dict_keys}")
    print()


def main() -> None:
    for p in sorted(IDEA_DIR.glob("*.json")):
        try:
            check_bank(p)
        except json.JSONDecodeError:
            print(f"{p.name}: SKIP (invalid JSON)")


if __name__ == "__main__":
    main()
