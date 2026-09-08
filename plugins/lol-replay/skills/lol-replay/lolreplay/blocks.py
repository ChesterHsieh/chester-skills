"""Packet block stream inside a decompressed ROFL2 frame.

Every block::

    marker  u8   flag bits:
                 0x80 time is a u8 delta in milliseconds from the previous block,
                      otherwise a float32 absolute game time in seconds
                 0x10 content length is u8, otherwise u32
                 0x40 packet type is omitted (same as previous block), otherwise u16
                 0x20 net id is a signed u8 delta from the previous block's net id,
                      otherwise a u32 (champions are 0x400000xx, 0 = broadcast)
                 0x0F low nibble: channel (observed 1, 2, 3)
    content  <len> bytes, per-type obfuscated payload

Only the envelope (time, type, net id) is decoded. Packet contents are opaque here.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Tuple

FLAG_TIME_RELATIVE = 0x80
FLAG_TYPE_SAME = 0x40
FLAG_NETID_RELATIVE = 0x20
FLAG_LEN_BYTE = 0x10
CHANNEL_MASK = 0x0F
NETID_OBJECT_BASE = 0x40000000


class BlockParseError(ValueError):
    """The frame does not follow the expected block layout."""


@dataclass(frozen=True)
class Block:
    time: float
    type: int
    net_id: int
    content: bytes
    marker: int
    frame_id: int = -1

    @property
    def channel(self) -> int:
        return self.marker & CHANNEL_MASK


def parse_blocks(data: bytes, frame_id: int = -1) -> Tuple[Block, ...]:
    """Decode one frame's block stream; raises BlockParseError if it does not line up."""
    try:
        return tuple(_iter_blocks(data, frame_id))
    except (struct.error, IndexError) as exc:
        raise BlockParseError(f"frame {frame_id}: truncated block ({exc})") from exc


def _iter_blocks(data: bytes, frame_id: int):
    n = len(data)
    i = 0
    time = 0.0
    last_type = 0
    last_net = 0
    while i < n:
        marker = data[i]
        i += 1
        if marker & FLAG_TIME_RELATIVE:
            time += data[i] / 1000.0
            i += 1
        else:
            time = struct.unpack_from("<f", data, i)[0]
            i += 4
        if marker & FLAG_LEN_BYTE:
            length = data[i]
            i += 1
        else:
            length = struct.unpack_from("<I", data, i)[0]
            i += 4
        if marker & FLAG_TYPE_SAME:
            ptype = last_type
        else:
            ptype = struct.unpack_from("<H", data, i)[0]
            i += 2
        if marker & FLAG_NETID_RELATIVE:
            delta = data[i]
            i += 1
            net = last_net + (delta - 256 if delta >= 128 else delta)
        else:
            net = struct.unpack_from("<I", data, i)[0]
            i += 4
        if i + length > n:
            raise BlockParseError(f"frame {frame_id}: content overruns frame at offset {i}")
        yield Block(time, ptype, net, data[i:i + length], marker, frame_id)
        i += length
        last_type, last_net = ptype, net


def game_blocks(rofl) -> Tuple[Block, ...]:
    """All blocks of the live game stream (chunks only, keyframes excluded), in chunk order."""
    out = []
    for frame in rofl.chunks():
        out.extend(parse_blocks(rofl.frame_data(frame), frame.id))
    return tuple(out)


def keyframe_blocks(rofl, index: int = 0) -> Tuple[Block, ...]:
    frames = rofl.keyframes()
    if not frames:
        return ()
    frame = frames[min(index, len(frames) - 1)]
    return parse_blocks(rofl.frame_data(frame), frame.id)
