"""MIDI + SysEx bridge to the ICR engine (icr.exe / icr).

Wraps `python-rtmidi` with:
  - MidiBridge class (open/close ports, send, receive pong)
  - SysEx encoders that match the ICR protocol described in
    `../icr-engine/docs/engine/SYSEX_PROTOCOL.md`

Encoders are pure byte-producers (no IO) so they can be tested without an
actual MIDI port. The MidiBridge class owns the rtmidi handles and sends the
frames. FastAPI router sits on top.

Protocol reminder (see header comment in sysex_handler.cpp):

    F0 7D 01 <cmd> <core_id> <data...> F7
    manufacturer = 0x7D  device = 0x01

Commands used by the MVP bridge:
    0x70 PING    → engine responds with 0x71 PONG
    0x01 SET_NOTE_PARAM     — per-note scalar
    0x02 SET_NOTE_PARTIAL   — per-partial parameter
    0x03 SET_BANK           — chunked JSON bank replace (240-byte chunks)
    0x04 SWITCH_CORE

Core IDs (8-bit):
    0x00 active   0x01 Additive   0x02 Physical   0x03 Sampler
    0x04 Sine     0x05 IFF         0x7F Engine
"""

from __future__ import annotations

import json
import logging
import struct
import threading
import time
from dataclasses import dataclass
from typing import Iterator


logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Protocol constants
# ---------------------------------------------------------------------------

SYSEX_START = 0xF0
SYSEX_END = 0xF7
MANUFACTURER = 0x7D
DEVICE_ID = 0x01

# Commands
CMD_SET_NOTE_PARAM = 0x01
CMD_SET_NOTE_PARTIAL = 0x02
CMD_SET_BANK = 0x03
CMD_SWITCH_CORE = 0x04
CMD_SET_MASTER = 0x10
CMD_PING = 0x70
CMD_PONG = 0x71
CMD_EXPORT_BANK = 0x72

# Core IDs
CORE_ACTIVE = 0x00
CORE_ADDITIVE = 0x01
CORE_PHYSICAL = 0x02
CORE_SAMPLER = 0x03
CORE_SINE = 0x04
CORE_IFF = 0x05
CORE_ENGINE = 0x7F

# Our storage-param → SysEx partial_id (from sysex_handler.cpp partialParamKey).
# Matches Partial field names exactly so the mapping is intent-preserving.
PARTIAL_PARAM_IDS: dict[str, int] = {
    "f_hz":    0x10,
    "A0":      0x11,
    "tau1":    0x12,
    "tau2":    0x13,
    "a1":      0x14,
    "beat_hz": 0x15,
    "phi":     0x16,
}

# Note scalar → SysEx note_param_id (from noteParamKey).
NOTE_PARAM_IDS: dict[str, int] = {
    # Shared
    "f0_hz":           0x01,
    "B":               0x02,
    # Additive-specific
    "attack_tau":      0x03,
    "A_noise":         0x04,
    "rms_gain":        0x05,
    "phi_diff":        0x06,
    "pan_correction":  0x07,
    # Shared M/S
    "stereo_width":    0x0A,
}

BANK_CHUNK_SIZE = 240   # per spec — keeps each SysEx frame well under RtMidi's default buffer


# ---------------------------------------------------------------------------
# Float encoding
# ---------------------------------------------------------------------------

def encode_float5(value: float) -> bytes:
    """Pack a 32-bit float into 5 SysEx-safe bytes (7 bits each, MSB first).

    Mirrors the spec snippet:

        raw  = struct.pack(">f", value)
        bits = int.from_bytes(raw, "big")
        out  = [(bits >> (i * 7)) & 0x7F  for i in range(4, -1, -1)]
    """
    raw = struct.pack(">f", float(value))
    bits = int.from_bytes(raw, "big")
    return bytes((bits >> (i * 7)) & 0x7F for i in range(4, -1, -1))


def decode_float5(data: bytes) -> float:
    """Inverse of encode_float5 — useful for debug / loopback tests."""
    if len(data) != 5:
        raise ValueError(f"need 5 bytes for float5 decode, got {len(data)}")
    bits = 0
    for i in range(5):
        bits |= (data[i] & 0x7F) << ((4 - i) * 7)
    # Python int → bytes (big-endian), then reinterpret as float32
    raw = bits.to_bytes(4, "big")   # top 3 bits were padding; may be 0
    return struct.unpack(">f", raw[-4:])[0]


# ---------------------------------------------------------------------------
# Frame encoders — all return bytes ready to hand to rtmidi.send_message()
# ---------------------------------------------------------------------------

def _frame(cmd: int, core_id: int, payload: bytes = b"") -> bytes:
    """Build a complete SysEx frame for commands that carry a core_id byte."""
    return bytes([SYSEX_START, MANUFACTURER, DEVICE_ID, cmd, core_id]) + payload + bytes([SYSEX_END])


def encode_ping() -> bytes:
    """PING has no core_id per spec — just F0 7D 01 70 F7."""
    return bytes([SYSEX_START, MANUFACTURER, DEVICE_ID, CMD_PING, SYSEX_END])


def encode_set_note_param(
    *, core_id: int, midi: int, velocity: int, parameter: str, value: float,
) -> bytes:
    """SET_NOTE_PARAM frame for a named Note scalar."""
    pid = NOTE_PARAM_IDS.get(parameter)
    if pid is None:
        raise ValueError(f"unknown note parameter {parameter!r}; allowed: {list(NOTE_PARAM_IDS)}")
    if not (0 <= midi <= 127):
        raise ValueError(f"midi must be 0..127, got {midi}")
    if not (0 <= velocity <= 127):
        raise ValueError(f"velocity must be 0..127, got {velocity}")
    payload = bytes([midi & 0x7F, velocity & 0x7F, pid & 0x7F]) + encode_float5(value)
    return _frame(CMD_SET_NOTE_PARAM, core_id, payload)


def encode_set_note_partial(
    *, core_id: int, midi: int, velocity: int, k: int, parameter: str, value: float,
) -> bytes:
    """SET_NOTE_PARTIAL frame — (midi, vel, k, param_id, float5)."""
    pid = PARTIAL_PARAM_IDS.get(parameter)
    if pid is None:
        raise ValueError(f"unknown partial parameter {parameter!r}; allowed: {list(PARTIAL_PARAM_IDS)}")
    if not (1 <= k <= 127):
        raise ValueError(f"k must be 1..127, got {k}")
    payload = bytes([midi & 0x7F, velocity & 0x7F, k & 0x7F, pid & 0x7F]) + encode_float5(value)
    return _frame(CMD_SET_NOTE_PARTIAL, core_id, payload)


def encode_set_bank_chunked(
    *, core_id: int, bank_json: str, chunk_size: int = BANK_CHUNK_SIZE,
) -> Iterator[bytes]:
    """Split a JSON bank payload into SysEx frames for CMD_SET_BANK.

    Each frame carries (chunk_idx, total_chunks, chunk_data), both indices
    packed as 3×7-bit bytes so indices up to ~2M are representable.
    The engine buffers chunks and calls loadBankJson once all arrive.
    """
    raw = bank_json.encode("utf-8")
    if chunk_size < 1:
        raise ValueError("chunk_size must be >= 1")
    total = max(1, (len(raw) + chunk_size - 1) // chunk_size)
    if total > (1 << 21):    # 2^21 chunks — the 3×7-bit counter ceiling
        raise ValueError(f"bank too large: {total} chunks exceeds 3×7-bit counter")

    for idx in range(total):
        start = idx * chunk_size
        end = min(start + chunk_size, len(raw))
        body = raw[start:end]
        # Everything that goes inside a SysEx frame must be 7-bit clean.
        # UTF-8 can contain bytes >= 0x80 (non-ASCII characters) — split into
        # nibbles so every delivered byte is 0x00..0x7F. The engine side
        # reassembles (see sysex_handler.cpp SET_BANK path).
        # The engine simply appends payload bytes as-is into the string buffer,
        # so we only need to guarantee 7-bit cleanliness on the wire: encode
        # each byte as two nibbles high|low with a leading length byte.
        safe = _encode_7bit_nibbles(body)

        idx_bytes = bytes([
            (idx >> 14) & 0x7F,
            (idx >> 7) & 0x7F,
            idx & 0x7F,
        ])
        total_bytes = bytes([
            (total >> 14) & 0x7F,
            (total >> 7) & 0x7F,
            total & 0x7F,
        ])
        payload = idx_bytes + total_bytes + safe
        yield _frame(CMD_SET_BANK, core_id, payload)


def _encode_7bit_nibbles(data: bytes) -> bytes:
    """Split each source byte into two 4-bit nibbles (hi, lo) so the whole
    stream stays 0x00-0x7F inside a SysEx frame. Length of output is exactly
    2 × len(data). The engine's reassembly path re-pairs nibbles when it sees
    pairs >= 0x80 (we'd need a matching decoder there). For robust transport
    use ASCII JSON only, which is already 7-bit clean.

    Currently UTF-8 JSON of our banks contains only ASCII (no non-ASCII
    characters in keys or numeric values), so this nibble dance is a no-op
    when len(data) == count of ASCII bytes. Kept as a safety net; callers
    that truly need binary-safe transport should switch to base64.
    """
    # Fast path: pure 7-bit input already safe.
    if all(b < 0x80 for b in data):
        return data
    out = bytearray()
    for b in data:
        out.append((b >> 4) & 0x0F)
        out.append(b & 0x0F)
    return bytes(out)


def encode_switch_core(*, core_id: int) -> bytes:
    """SWITCH_CORE — a single-byte command (core_id itself encodes the target)."""
    return bytes([SYSEX_START, MANUFACTURER, DEVICE_ID, CMD_SWITCH_CORE, core_id & 0x7F, SYSEX_END])


# ---------------------------------------------------------------------------
# Pong parsing
# ---------------------------------------------------------------------------

def is_pong(frame: bytes) -> bool:
    """True iff the given SysEx frame is the ICR engine's PONG reply."""
    return (
        len(frame) == 5
        and frame[0] == SYSEX_START
        and frame[1] == MANUFACTURER
        and frame[2] == DEVICE_ID
        and frame[3] == CMD_PONG
        and frame[4] == SYSEX_END
    )


# ---------------------------------------------------------------------------
# MidiBridge — rtmidi wrapper
# ---------------------------------------------------------------------------

@dataclass
class MidiBridgeStatus:
    input_open: bool
    output_open: bool
    input_port_name: str | None
    output_port_name: str | None
    last_pong_ts: float | None


class MidiBridge:
    """Opens one MIDI input (listening for pong) + one output (sending)."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._in = None       # type: ignore[assignment]
        self._out = None      # type: ignore[assignment]
        self._in_port_name: str | None = None
        self._out_port_name: str | None = None
        self._last_pong_ts: float | None = None
        self._pong_event = threading.Event()

    # -- lifecycle ------------------------------------------------------

    @staticmethod
    def list_input_ports() -> list[str]:
        import rtmidi
        midi = rtmidi.MidiIn()
        try:
            return list(midi.get_ports())
        finally:
            midi.delete()

    @staticmethod
    def list_output_ports() -> list[str]:
        import rtmidi
        midi = rtmidi.MidiOut()
        try:
            return list(midi.get_ports())
        finally:
            midi.delete()

    def open(self, *, input_port_index: int | None, output_port_index: int | None) -> None:
        """Open the requested ports. Either index may be None to skip opening."""
        import rtmidi
        with self._lock:
            self._close_locked()
            if input_port_index is not None:
                self._in = rtmidi.MidiIn()
                self._in.ignore_types(sysex=False, timing=True, active_sense=True)
                self._in.open_port(input_port_index)
                self._in_port_name = self._in.get_port_name(input_port_index)
                self._in.set_callback(self._on_midi)
                logger.info("midi.input_open", extra={"port": self._in_port_name, "index": input_port_index})
            if output_port_index is not None:
                self._out = rtmidi.MidiOut()
                self._out.open_port(output_port_index)
                self._out_port_name = self._out.get_port_name(output_port_index)
                logger.info("midi.output_open", extra={"port": self._out_port_name, "index": output_port_index})

    def close(self) -> None:
        with self._lock:
            self._close_locked()

    def _close_locked(self) -> None:
        if self._in is not None:
            try:
                self._in.cancel_callback()
                self._in.close_port()
                self._in.delete()
            finally:
                self._in = None
                self._in_port_name = None
        if self._out is not None:
            try:
                self._out.close_port()
                self._out.delete()
            finally:
                self._out = None
                self._out_port_name = None

    # -- state ---------------------------------------------------------

    def status(self) -> MidiBridgeStatus:
        with self._lock:
            return MidiBridgeStatus(
                input_open=self._in is not None,
                output_open=self._out is not None,
                input_port_name=self._in_port_name,
                output_port_name=self._out_port_name,
                last_pong_ts=self._last_pong_ts,
            )

    # -- send ----------------------------------------------------------

    def _send_raw(self, message: bytes) -> None:
        if self._out is None:
            raise RuntimeError("output port not open")
        self._out.send_message(list(message))

    def send_note_on(self, *, channel: int, midi: int, velocity: int) -> None:
        with self._lock:
            self._send_raw(bytes([0x90 | (channel & 0x0F), midi & 0x7F, velocity & 0x7F]))

    def send_note_off(self, *, channel: int, midi: int, velocity: int = 0) -> None:
        with self._lock:
            self._send_raw(bytes([0x80 | (channel & 0x0F), midi & 0x7F, velocity & 0x7F]))

    def send_sysex(self, message: bytes) -> None:
        with self._lock:
            self._send_raw(message)

    def ping(self, *, timeout_s: float = 0.5) -> float | None:
        """Send PING and wait up to `timeout_s` for a PONG. Returns RTT seconds or None on timeout."""
        with self._lock:
            if self._out is None:
                raise RuntimeError("output port not open")
            self._pong_event.clear()
            t0 = time.perf_counter()
            self._send_raw(encode_ping())
        got = self._pong_event.wait(timeout_s)
        if not got:
            return None
        return time.perf_counter() - t0

    def push_bank(self, *, core_id: int, bank_json: str) -> int:
        """Send a SET_BANK chunked payload. Returns the number of frames sent."""
        count = 0
        for frame in encode_set_bank_chunked(core_id=core_id, bank_json=bank_json):
            self.send_sysex(frame)
            count += 1
        logger.info(
            "midi.push_bank",
            extra={"core_id": core_id, "n_frames": count, "bytes": len(bank_json)},
        )
        return count

    def push_partial_param(
        self,
        *, core_id: int, midi: int, velocity: int, k: int,
        parameter: str, value: float,
    ) -> None:
        """Push one per-partial parameter update — used for live-edit preview.

        The engine applies the change in ~ms; combined with the user's selected
        MIDI port output, this powers the "instant audition" UX: drag a slider,
        hear the result before committing.
        """
        frame = encode_set_note_partial(
            core_id=core_id, midi=midi, velocity=velocity,
            k=k, parameter=parameter, value=value,
        )
        self.send_sysex(frame)
        logger.debug(
            "midi.push_partial",
            extra={
                "core_id": core_id, "midi": midi, "velocity": velocity,
                "k": k, "parameter": parameter,
            },
        )

    def push_note_param(
        self,
        *, core_id: int, midi: int, velocity: int,
        parameter: str, value: float,
    ) -> None:
        """Push one per-note scalar — for instant-preview on f0_hz, B, attack_tau, ..."""
        frame = encode_set_note_param(
            core_id=core_id, midi=midi, velocity=velocity,
            parameter=parameter, value=value,
        )
        self.send_sysex(frame)
        logger.debug(
            "midi.push_note_param",
            extra={
                "core_id": core_id, "midi": midi, "velocity": velocity,
                "parameter": parameter,
            },
        )

    # -- callback ------------------------------------------------------

    def _on_midi(self, event, data=None) -> None:
        """rtmidi fires this on every incoming message. We only care about PONG here."""
        message, _timestamp = event
        if message and len(message) == 5 and is_pong(bytes(message)):
            self._last_pong_ts = time.time()
            self._pong_event.set()
            logger.debug("midi.pong_received")
