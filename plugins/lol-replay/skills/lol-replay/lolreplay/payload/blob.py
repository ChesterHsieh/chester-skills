"""Byte-array ("blob") field decoding: varint length + per-byte transform + front/back interleave."""
from __future__ import annotations

from typing import Tuple

from .generic import DecodeError, decode_packet
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
    """WaypointGroup packet -> plain record blob.

    16.17: flags byte, bit 3 set means a blob (global byte transform, front/back interleave) follows.
    16.20+: the blob is a byte-vector field of the packet spec, decoded with that field's own tables."""
    if spec.waypoint_blob_field is not None:
        pspec = spec.packet_spec("waypoint")
        if pspec is None:
            raise BlobError(f"no waypoint packet spec for patch {spec.patch}")
        try:
            values = decode_packet(pspec, content)
        except DecodeError as exc:
            raise BlobError(str(exc)) from exc
        field = values.get(spec.waypoint_blob_field)
        if not isinstance(field, dict) or "raw" not in field:
            return b""
        return bytes.fromhex(field["raw"])
    if not content or not content[0] & 0x08:
        return b""
    blob, _ = decode_blob(spec, content, 1)
    return blob
