"""Unit tests for SysEx encoders — pure byte-level, no rtmidi required."""

from __future__ import annotations

import json

import pytest

from piano_web.midi_bridge import (
    BANK_CHUNK_SIZE,
    CMD_PING,
    CMD_SET_BANK,
    CMD_SET_NOTE_PARAM,
    CMD_SET_NOTE_PARTIAL,
    CMD_SWITCH_CORE,
    CORE_ACTIVE,
    CORE_ADDITIVE,
    DEVICE_ID,
    MANUFACTURER,
    NOTE_PARAM_IDS,
    PARTIAL_PARAM_IDS,
    SYSEX_END,
    SYSEX_START,
    decode_float5,
    encode_float5,
    encode_ping,
    encode_set_bank_chunked,
    encode_set_note_param,
    encode_set_note_partial,
    encode_switch_core,
    is_pong,
)


# ---- float5 round-trip ---------------------------------------------------

@pytest.mark.parametrize("value", [0.0, 1.0, -1.0, 3.14159, 1e-6, 1e6, 0.5 * (2 ** -23)])
def test_float5_roundtrip(value: float):
    packed = encode_float5(value)
    assert len(packed) == 5
    assert all(b <= 0x7F for b in packed)
    decoded = decode_float5(packed)
    assert decoded == pytest.approx(value, rel=1e-6, abs=1e-12)


def test_float5_all_bytes_7bit_clean():
    for v in (1.23, 123.456, -0.001, 1e10, -1e10):
        for byte in encode_float5(v):
            assert byte < 0x80


# ---- PING -----------------------------------------------------------------

def test_ping_matches_spec():
    frame = encode_ping()
    assert list(frame) == [0xF0, 0x7D, 0x01, 0x70, 0xF7]


def test_pong_detection():
    assert is_pong(bytes([0xF0, 0x7D, 0x01, 0x71, 0xF7]))
    assert not is_pong(bytes([0xF0, 0x7D, 0x01, 0x70, 0xF7]))     # ping, not pong
    assert not is_pong(bytes([0xF0, 0x7E, 0x01, 0x71, 0xF7]))     # wrong mfg
    assert not is_pong(bytes([]))
    assert not is_pong(bytes([0xF0, 0x7D, 0x01, 0x71, 0x00, 0xF7]))   # too long


# ---- SET_NOTE_PARAM -------------------------------------------------------

def test_set_note_param_frame_shape():
    frame = encode_set_note_param(
        core_id=CORE_ADDITIVE, midi=60, velocity=100,
        parameter="f0_hz", value=261.6,
    )
    # F0 7D 01 01 <core> <midi> <vel> <pid> <5 float bytes> F7
    assert frame[0] == SYSEX_START
    assert frame[1] == MANUFACTURER
    assert frame[2] == DEVICE_ID
    assert frame[3] == CMD_SET_NOTE_PARAM
    assert frame[4] == CORE_ADDITIVE
    assert frame[5] == 60
    assert frame[6] == 100
    assert frame[7] == NOTE_PARAM_IDS["f0_hz"]
    assert frame[-1] == SYSEX_END
    assert len(frame) == 5 + 3 + 5 + 1   # header+core + midi/vel/pid + float5 + F7


def test_set_note_param_all_bytes_7bit_safe():
    frame = encode_set_note_param(
        core_id=CORE_ADDITIVE, midi=127, velocity=127,
        parameter="f0_hz", value=1.5e30,
    )
    # All bytes inside the frame (excluding F0/F7 sentinels) must be < 0x80
    inner = frame[1:-1]
    assert all(b < 0x80 for b in inner)


def test_set_note_param_rejects_bad_midi():
    with pytest.raises(ValueError):
        encode_set_note_param(
            core_id=CORE_ADDITIVE, midi=200, velocity=0,
            parameter="f0_hz", value=0.0,
        )


def test_set_note_param_rejects_unknown_parameter():
    with pytest.raises(ValueError):
        encode_set_note_param(
            core_id=CORE_ADDITIVE, midi=60, velocity=100,
            parameter="bogus", value=0.0,
        )


# ---- SET_NOTE_PARTIAL ----------------------------------------------------

def test_set_note_partial_uses_partial_param_id():
    frame = encode_set_note_partial(
        core_id=CORE_ADDITIVE, midi=60, velocity=5, k=3,
        parameter="tau1", value=0.5,
    )
    assert frame[3] == CMD_SET_NOTE_PARTIAL
    assert frame[8] == PARTIAL_PARAM_IDS["tau1"]
    # Total length: header 3 + cmd 1 + core 1 + midi/vel/k/pid 4 + float5 + F7 = 15
    assert len(frame) == 15


def test_set_note_partial_rejects_zero_k():
    with pytest.raises(ValueError):
        encode_set_note_partial(
            core_id=CORE_ADDITIVE, midi=60, velocity=5, k=0,
            parameter="tau1", value=0.1,
        )


def test_set_note_partial_maps_all_partial_params():
    """Every PARTIAL_PARAM_ID must produce a valid frame."""
    for name, pid in PARTIAL_PARAM_IDS.items():
        frame = encode_set_note_partial(
            core_id=CORE_ADDITIVE, midi=60, velocity=5, k=1,
            parameter=name, value=1.0,
        )
        assert frame[8] == pid


# ---- SWITCH_CORE ---------------------------------------------------------

def test_switch_core_frame():
    frame = encode_switch_core(core_id=CORE_ADDITIVE)
    assert list(frame) == [0xF0, 0x7D, 0x01, 0x04, 0x01, 0xF7]


# ---- SET_BANK chunked ----------------------------------------------------

def test_set_bank_produces_at_least_one_chunk_for_empty_json():
    chunks = list(encode_set_bank_chunked(core_id=CORE_ADDITIVE, bank_json="{}"))
    assert len(chunks) == 1


def test_set_bank_chunks_split_at_chunk_size():
    # 500-byte JSON should split into 3 chunks of default size 240
    payload = "a" * 500
    chunks = list(encode_set_bank_chunked(core_id=CORE_ADDITIVE, bank_json=payload))
    assert len(chunks) == 3


def test_set_bank_chunks_are_7bit_clean_on_ascii_input():
    payload = json.dumps({"metadata": {"name": "test"}, "notes": {}})
    for frame in encode_set_bank_chunked(core_id=CORE_ADDITIVE, bank_json=payload):
        assert frame[0] == SYSEX_START
        assert frame[-1] == SYSEX_END
        inner = frame[1:-1]
        assert all(b < 0x80 for b in inner)


def test_set_bank_chunk_index_and_total_packed_in_first_payload_bytes():
    payload = "x" * 100
    chunks = list(encode_set_bank_chunked(core_id=CORE_ADDITIVE, bank_json=payload))
    assert len(chunks) == 1
    frame = chunks[0]
    # After F0 7D 01 03 <core_id>: bytes 5..10 are (idx hi, mid, lo, total hi, mid, lo)
    idx_bytes = frame[5:8]
    total_bytes = frame[8:11]
    assert list(idx_bytes) == [0, 0, 0]
    assert list(total_bytes) == [0, 0, 1]


def test_set_bank_cmd_and_core_id():
    payload = "hello"
    frame = next(iter(encode_set_bank_chunked(core_id=CORE_ADDITIVE, bank_json=payload)))
    assert frame[3] == CMD_SET_BANK
    assert frame[4] == CORE_ADDITIVE


def test_set_bank_rejects_nonsense_chunk_size():
    with pytest.raises(ValueError):
        list(encode_set_bank_chunked(core_id=CORE_ACTIVE, bank_json="x", chunk_size=0))
