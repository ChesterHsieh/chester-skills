"""WaypointGroup records: (net_id, speed, waypoints) with delta-compressed 16-bit coordinates."""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Tuple

from .spec import PayloadSpec

HEADER_FMT = "<HIf"
HEADER_LEN = struct.calcsize(HEADER_FMT)


@dataclass(frozen=True)
class WaypointRecord:
    net_id: int
    speed: float
    points: Tuple[Tuple[float, float], ...]
    flags: int


def parse_records(spec: PayloadSpec, blob: bytes) -> Tuple[Tuple[WaypointRecord, ...], int]:
    """Parse all records in a decoded blob; returns (records, bytes consumed)."""
    cx, cy = spec.map_center
    p = 0
    out = []
    while p + HEADER_LEN <= len(blob):
        ptype, net, speed = struct.unpack_from(HEADER_FMT, blob, p)
        p += HEADER_LEN
        low, high = ptype & 0xFF, ptype >> 8
        if low & 1:
            p += 1
        count = low >> 1
        if count == 0:
            break
        nbits = 0 if count <= 1 else ((count - 2) >> 2) + 1
        bitmask = blob[p:p + nbits]
        p += nbits
        points = []
        x = y = 0
        bit = 0
        for i in range(count):
            short_x = short_y = False
            if i:
                short_x = bool(bitmask[bit >> 3] & (1 << (bit & 7)))
                bit += 1
                short_y = bool(bitmask[bit >> 3] & (1 << (bit & 7)))
                bit += 1
            x, p = _coord(blob, p, x, short_x)
            y, p = _coord(blob, p, y, short_y)
            points.append((_signed16(x) * 2.0 + cx, _signed16(y) * 2.0 + cy))
        out.append(WaypointRecord(net, speed, tuple(points), high))
    return tuple(out), p


def _coord(blob: bytes, p: int, prev: int, short: bool) -> Tuple[int, int]:
    if short:
        return (prev + struct.unpack_from("<b", blob, p)[0]) & 0xFFFF, p + 1
    return struct.unpack_from("<H", blob, p)[0], p + 2


def _signed16(v: int) -> int:
    return v - 0x10000 if v >= 0x8000 else v
