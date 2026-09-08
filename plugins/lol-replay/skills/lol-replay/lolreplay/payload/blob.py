"""Byte-array ("blob") field decoding: varint length + per-byte transform + front/back interleave."""
from __future__ import annotations

from typing import Tuple

from .spec import PayloadSpec


class BlobError(ValueError):
    """The blob field is truncated or malformed."""


def read_varint(spec: PayloadSpec, data: bytes, pos: int) -> Tuple[int, int]:
    value = 0
    shift = 0
    while True:
        if pos >= len(data):
            raise BlobError("varint runs past end of content")
        d = spec.tbyte(data[pos])
        pos += 1
        value |= (d & 0x7F) << shift
        shift += 7
        if not d & 0x80:
            return value, pos


def decode_blob(spec: PayloadSpec, data: bytes, pos: int) -> Tuple[bytes, int]:
    """Decode one blob field starting at `pos`; returns (plain bytes, new position)."""
    n, pos = read_varint(spec, data, pos)
    if pos + n > len(data):
        raise BlobError(f"blob length {n} exceeds content ({len(data) - pos} left)")
    out = bytearray(n)
    i, j = 0, n - 1
    k = pos
    while i < j:
        out[i] = spec.tbyte(data[k])
        out[j] = spec.tbyte(data[k + 1])
        i += 1
        j -= 1
        k += 2
    if i == j:
        out[i] = spec.tbyte(data[k])
        k += 1
    return bytes(out), k


def decode_waypoint_packet(spec: PayloadSpec, content: bytes) -> bytes:
    """WaypointGroup packet: flags byte, bit 3 set means a blob follows immediately."""
    if not content or not content[0] & 0x08:
        return b""
    blob, _ = decode_blob(spec, content, 1)
    return blob
