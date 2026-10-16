"""Generic decoder: the field kinds added for the 16.20 specs (identity tables keep the cases readable)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolreplay.payload.generic import DecodeError, decode_packet  # noqa: E402

VAL7 = [b & 0x7F for b in range(256)]
CONT = [1 if b & 0x80 else 0 for b in range(256)]


def varint(size=None, tag=0):
    h = {"kind": "varint", "val7": VAL7, "cont": CONT, "netid_tag": tag}
    if size:
        h["size"] = size
    return h


def obj(fields, flag_bytes=1, helpers=None):
    spec = {"label": "t", "flag_bytes": flag_bytes, "fields": fields}
    if helpers is not None:
        spec["helpers"] = helpers
    return spec


def test_four_bit_group_selects_mode():
    spec = obj([{"bit": 2, "kind": "4bit", "modes": {"9": {"kind": "helper", "offset": 16, "helper": "v"}}}],
               helpers={"v": varint()})
    assert decode_packet(spec, bytes([9 << 2, 5])) == {16: 5}


def test_client_exact_varint_has_no_length_cap_and_xors_the_tag():
    spec = obj([{"bit": 0, "kind": "bit", "modes": {"1": {"kind": "helper", "offset": 8, "helper": "v"}}}],
               helpers={"v": varint(size=4, tag=1)})
    six_bytes = bytes([1, 0x81, 0x80, 0x80, 0x80, 0x80, 0x00])
    assert decode_packet(spec, six_bytes) == {8: 0x40000001}
    # bit 30 already set by the payload: the client's xor clears it
    tagged = bytes([1, 0x81, 0x80, 0x80, 0x80, 0x04])
    assert decode_packet(spec, tagged)[8] == 0x00000001


def test_legacy_varint_keeps_the_cap():
    spec = obj([{"bit": 0, "kind": "bit", "modes": {"1": {"kind": "helper", "offset": 8, "helper": "v"}}}],
               helpers={"v": varint()})
    with pytest.raises(DecodeError):
        decode_packet(spec, bytes([1]) + bytes([0x81] * 7) + b"\x00")


def test_inline_varint_and_skip_helper():
    spec = obj([
        {"bit": 0, "kind": "bit", "modes": {"1": {"kind": "inline_varint", "offset": 4, "val7": VAL7, "cont": CONT}}},
        {"bit": 1, "kind": "bit", "modes": {"1": {"kind": "helper", "offset": 8, "helper": "s"}}},
        {"bit": 2, "kind": "bit", "modes": {"1": {"kind": "inline", "offset": None, "consumed": 1}}},
    ], helpers={"s": {"kind": "skip", "width": 0}})
    assert decode_packet(spec, bytes([0b111, 0x85, 0x01, 0xEE])) == {4: 5 | (1 << 7)}


def test_rejected_mode_raises():
    spec = obj([{"bit": 0, "kind": "bit", "modes": {"0": {"kind": "invalid"}}}])
    with pytest.raises(DecodeError):
        decode_packet(spec, b"\x00")


def test_const_with_plain_value():
    spec = obj([{"bit": 0, "kind": "bit", "modes": {
        "1": {"kind": "const", "offset": 0x84, "size": 4, "stored": 0x85858585, "plain": 0},
        "0": {"kind": "const", "offset": 0x30, "size": 4, "stored": 1, "plain": 7,
              "extra": [[0x30, 4, 1], [0x34, 4, 2]], "extra_plain": [7, 8]}}}])
    assert decode_packet(spec, b"\x01") == {0x84: 0}
    assert decode_packet(spec, b"\x00") == {0x30: 7, 0x34: 8}


def test_byte_vector_with_element_table():
    table = [(b + 1) & 0xFF for b in range(256)]
    spec = obj([{"bit": 0, "kind": "bit", "modes": {"1": {"kind": "helper", "offset": 0x18, "helper": "b"}}}],
               helpers={"b": {"kind": "vector", "val7": VAL7, "cont": CONT, "elem": 1, "elem_table": table}})
    assert decode_packet(spec, bytes([1, 3, 0x10, 0x20, 0x30])) == {0x18: {"count": 3, "raw": "112131"}}


def test_object_vector_decodes_each_element():
    element = obj([{"bit": 0, "kind": "bit", "modes": {"1": {"kind": "helper", "offset": 8, "helper": "n"}}}])
    spec = obj([{"bit": 0, "kind": "bit", "modes": {"1": {"kind": "helper", "offset": 0x20, "helper": "list"}}}],
               helpers={"n": varint(size=4, tag=1),
                        "list": {"kind": "object_vector", "val7": VAL7, "cont": CONT, "element": element}})
    out = decode_packet(spec, bytes([1, 2, 1, 0x37, 1, 0x31]))
    assert out == {0x20: {"count": 2, "items": [{8: 0x40000037}, {8: 0x40000031}]}}


def test_varint_vector_fills_from_the_last_element():
    spec = obj([{"bit": 0, "kind": "bit", "modes": {"1": {"kind": "helper", "offset": 0x28, "helper": "vv"}}}],
               helpers={"vv": {"kind": "varint_vector", "val7": VAL7, "cont": CONT, "reverse": True}})
    # count 2, then 0x85 0x01 (= 133) and 7; the client stores them last-first
    assert decode_packet(spec, bytes([1, 2, 0x85, 0x01, 7])) == {0x28: {"count": 2, "values": [7, 133]}}
    with pytest.raises(DecodeError):  # the client refuses a count larger than the bytes left
        decode_packet(spec, bytes([1, 5, 1, 2]))


def test_inline_object_vector():
    element = obj([{"bit": 0, "kind": "bit", "modes": {"1": {"kind": "helper", "offset": 8, "helper": "n"}}}])
    spec = obj([{"bit": 0, "kind": "bit", "modes": {
        "1": {"kind": "object_vector", "offset": 0x10, "val7": VAL7, "cont": CONT, "element": element}}}],
        helpers={"n": varint(size=4)})
    assert decode_packet(spec, bytes([1, 2, 1, 5, 0])) == {0x10: {"count": 2, "items": [{8: 5}, {}]}}
