"""Verify MIDI + SysEx round-trip against the running ICR engine.

Assumes the engine is already running and its MIDI is configured so:
  - ICR input  = loopMIDI Port 1  (we write here)
  - ICR output = loopMIDI Port 0  (engine writes PONG here; we read)

Procedure:
  1. List available MIDI ports.
  2. Open MidiBridge with those two ports.
  3. Send PING, wait up to 1 s for PONG.
  4. Send a SET_NOTE_PARTIAL frame (no reply expected, just "did the write fail?").
  5. Send a short note-on/off burst.
  6. Print a PASS/FAIL summary.

Run this any time you want to confirm the pipe to the engine is alive.
"""

from __future__ import annotations

import sys
import time

from piano_web.midi_bridge import (
    CORE_ACTIVE,
    MidiBridge,
    encode_set_note_partial,
)


DEFAULT_OUTPUT_HINT = "loopMIDI Port 1"
DEFAULT_INPUT_HINT = "loopMIDI Port 0"


def _find(ports: list[str], hint: str) -> int | None:
    for i, n in enumerate(ports):
        if hint.lower() in n.lower():
            return i
    return None


def main() -> int:
    out_ports = MidiBridge.list_output_ports()
    in_ports = MidiBridge.list_input_ports()
    print("Output ports:", out_ports)
    print("Input ports: ", in_ports)

    out_idx = _find(out_ports, DEFAULT_OUTPUT_HINT)
    in_idx = _find(in_ports, DEFAULT_INPUT_HINT)
    if out_idx is None:
        print(f"\nERROR: '{DEFAULT_OUTPUT_HINT}' not found in output ports.")
        return 1
    if in_idx is None:
        print(f"\nERROR: '{DEFAULT_INPUT_HINT}' not found in input ports.")
        return 1

    bridge = MidiBridge()
    bridge.open(input_port_index=in_idx, output_port_index=out_idx)
    print(f"\nopened OUT[{out_idx}] = {out_ports[out_idx]}")
    print(f"opened IN[{in_idx}]  = {in_ports[in_idx]}")

    ok = True

    # --- PING / PONG ---------------------------------------------------
    print("\n=== PING ===")
    rtt = bridge.ping(timeout_s=1.0)
    if rtt is None:
        print("FAIL: no PONG received within 1 s")
        print("      → is icr.exe / icrgui.exe running? Does it listen on loopMIDI Port 1?")
        ok = False
    else:
        print(f"PASS: PONG received in {rtt * 1000:.2f} ms")

    # --- SET_NOTE_PARTIAL (no reply expected, just verify send doesn't fail) ---
    print("\n=== SET_NOTE_PARTIAL tau1 at (60, 5, k=3) = 0.456 ===")
    try:
        frame = encode_set_note_partial(
            core_id=CORE_ACTIVE, midi=60, velocity=5, k=3,
            parameter="tau1", value=0.456,
        )
        bridge.send_sysex(frame)
        print(f"PASS: wrote {len(frame)}-byte SysEx frame")
    except Exception as exc:
        print(f"FAIL: send failed — {exc}")
        ok = False

    # --- Note-on / off ------------------------------------------------
    print("\n=== Note-on/off m60 v100 (expect a brief sound from the engine) ===")
    try:
        bridge.send_note_on(channel=0, midi=60, velocity=100)
        time.sleep(0.5)
        bridge.send_note_off(channel=0, midi=60)
        print("PASS: note-on + note-off written without error")
    except Exception as exc:
        print(f"FAIL: note send failed — {exc}")
        ok = False

    bridge.close()

    print("\n=== summary ===")
    if ok:
        print("All checks passed. MIDI pipe to ICR engine is alive.")
    else:
        print("One or more checks failed. See messages above.")
    return 0 if ok else 2


if __name__ == "__main__":
    sys.exit(main())
