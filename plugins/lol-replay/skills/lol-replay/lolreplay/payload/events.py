"""Named fields decoded from packet contents through the per-patch packet specs.

The generic decoder returns {object offset: plain value}; this module knows which offsets carry
the kill, purchase and spell-cast information for the packet layouts described by the specs.
Offsets are object-relative and stable within a patch (they come from the client's class layout),
so a patch only needs its `packet_<type>.json` spec and the role→type mapping in `<patch>.json`.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple

from .generic import DecodeError, as_float, decode_packet

NETID_TAG = 0x40000000

# 16.17 object layouts (root-relative offsets; nested objects are flattened by the decoder)
KILL_FIELDS = {"respawn": 0x10, "killer": 0x18, "x": 0x24, "height": 0x28, "z": 0x2c}
SHOP_FIELDS = {"gold_after": 0x10, "item": 0x4c, "price": 0x50}
CAST_FIELDS = {
    "caster": 0x18, "seq": 0x1c, "spell_hash": 0x48, "cast_time": 0x6c,
    "vec_a": (0x7c, 0x80, 0x84), "direction": (0x90, 0x94, 0x98), "start": (0xa8, 0xac, 0xb0),
    "target": 0xb4, "end": (0xf0, 0xf4, 0xf8), "time": 0xe4,
}


@dataclass(frozen=True)
class KillInfo:
    killer_netid: Optional[int]
    respawn: Optional[float]
    pos: Optional[Tuple[float, float]]


@dataclass(frozen=True)
class PurchaseInfo:
    item: Optional[int]
    price: Optional[float]
    gold_after: Optional[float]


@dataclass(frozen=True)
class CastInfo:
    caster_netid: Optional[int]
    spell_hash: Optional[int]
    target_netid: Optional[int]
    start: Optional[Tuple[float, float]]
    end: Optional[Tuple[float, float]]
    cast_time: Optional[float]


def _int(values: Dict[int, Any], off: int) -> Optional[int]:
    v = values.get(off)
    return v if isinstance(v, int) else None


def _float(values: Dict[int, Any], off: int) -> Optional[float]:
    v = values.get(off)
    return as_float(v) if isinstance(v, int) else None


def _netid(values: Dict[int, Any], off: int) -> Optional[int]:
    v = _int(values, off)
    if v is None:
        return None
    return v if v & NETID_TAG else None


def _xz(values: Dict[int, Any], offs) -> Optional[Tuple[float, float]]:
    """Map position from a (x, height, z) triple; the 2-D map uses x and z."""
    x, z = _float(values, offs[0]), _float(values, offs[2])
    if x is None or z is None or not (-2000.0 < x < 20000.0 and -2000.0 < z < 20000.0):
        return None
    return (x, z)


def decode_kill(spec: dict, content: bytes) -> Optional[KillInfo]:
    try:
        v = decode_packet(spec, content)
    except DecodeError:
        return None
    f = KILL_FIELDS
    return KillInfo(killer_netid=_netid(v, f["killer"]), respawn=_float(v, f["respawn"]),
                    pos=_xz(v, (f["x"], f["height"], f["z"])))


def decode_purchase(spec: dict, content: bytes) -> Optional[PurchaseInfo]:
    try:
        v = decode_packet(spec, content)
    except DecodeError:
        return None
    f = SHOP_FIELDS
    return PurchaseInfo(item=_int(v, f["item"]), price=_float(v, f["price"]), gold_after=_float(v, f["gold_after"]))


def decode_cast(spec: dict, content: bytes) -> Optional[CastInfo]:
    v = decode_packet(spec, content, partial=True)  # the fields after the end position are not needed
    if not v:
        return None
    f = CAST_FIELDS
    return CastInfo(caster_netid=_netid(v, f["caster"]), spell_hash=_int(v, f["spell_hash"]),
                    target_netid=_netid(v, f["target"]), start=_xz(v, f["start"]), end=_xz(v, f["end"]),
                    cast_time=_float(v, f["cast_time"]))
