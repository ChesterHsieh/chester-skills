"""Named fields decoded from packet contents through the per-patch packet specs.

The generic decoder returns {object offset: plain value}; this module knows which offsets carry
the kill, purchase and spell-cast information. Offsets are object-relative and stable within a
patch (they come from the client's class layout). A patch ships its `packet_<type>.json` specs,
the role→type mapping and, from 16.20 on, the role→offset map (`fields` in `<patch>.json`);
patches without one use the 16.17 layout below.
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Dict, Mapping, Optional, Tuple

from .generic import DecodeError, as_float, decode_packet

NETID_TAG = 0x40000000

# 16.17 object layouts (root-relative offsets; nested objects are flattened by the decoder)
KILL_FIELDS = {"respawn": 0x10, "killer": 0x18, "x": 0x24, "height": 0x28, "z": 0x2c}
SHOP_FIELDS = {"gold_after": 0x10, "item": 0x4c, "price": 0x50}
# +0xb4 is the net id of the cast instance itself (it increases with every cast), not the target.
# The target list is the vector at +0x100 (2 obfuscated bytes per target, empty for skillshots) and is
# not decoded yet, so targets are inferred from positions (see combos.py). The client sometimes sends
# the same cast twice; `seq` (+0x1c) identifies the cast.
# `start` (+0xf0) is the caster position (matches the waypoint track within a few units); `end` (+0xa8)
# is the aim point, which for targeted spells is the target's position.
CAST_FIELDS = {
    "caster": 0x18, "seq": 0x1c, "spell_hash": 0x48, "cast_time": 0x6c,
    "vec_a": (0x7c, 0x80, 0x84), "direction": (0x90, 0x94, 0x98), "end": (0xa8, 0xac, 0xb0),
    "instance": 0xb4, "start": (0xf0, 0xf4, 0xf8), "time": 0xe4,
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
    seq: Optional[int]
    start: Optional[Tuple[float, float]]
    end: Optional[Tuple[float, float]]
    cast_time: Optional[float]
    # net ids in the cast's target list (16.20+): the unit a targeted spell is cast on, the units an
    # instant area spell affects (often the caster itself); empty for missiles and skillshots
    targets: Tuple[int, ...] = ()


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


@dataclass(frozen=True)
class DamageInfo:
    target_netid: Optional[int]
    source_netid: Optional[int]
    amount: Optional[float]
    script_hash: Optional[int]     # spell script of the damage source (TalonWMissileOne, CaitlynCritAttack, minion attacks...)
    kind: Optional[int]            # raw flag byte of the hit (meaning not decoded)


@dataclass(frozen=True)
class AttackInfo:
    target_netid: Optional[int]    # None/0 when the attack order has no unit target
    target_pos: Optional[Tuple[float, float]]


def _targets(values: Dict[int, Any], spec: Optional[Mapping[str, int]]) -> Tuple[int, ...]:
    """Net ids of the target-list vector: {"list": offset of the vector, "netid": offset in an element}."""
    if not spec:
        return ()
    field = values.get(spec["list"])
    if not isinstance(field, dict):
        return ()
    out = []
    for item in field.get("items", ()):
        nid = item.get(spec["netid"])
        if isinstance(nid, int) and nid & NETID_TAG:
            out.append(nid)
    return tuple(out)


def decode_kill(spec: dict, content: bytes, fields: Optional[Mapping[str, Any]] = None) -> Optional[KillInfo]:
    try:
        v = decode_packet(spec, content)
    except DecodeError:
        return None
    f = fields or KILL_FIELDS
    return KillInfo(killer_netid=_netid(v, f["killer"]), respawn=_float(v, f["respawn"]),
                    pos=_xz(v, (f["x"], f["height"], f["z"])))


def decode_purchase(spec: dict, content: bytes, fields: Optional[Mapping[str, Any]] = None) -> Optional[PurchaseInfo]:
    try:
        v = decode_packet(spec, content)
    except DecodeError:
        return None
    f = fields or SHOP_FIELDS
    return PurchaseInfo(item=_int(v, f["item"]), price=_float(v, f["price"]), gold_after=_float(v, f["gold_after"]))


def decode_cast(spec: dict, content: bytes, fields: Optional[Mapping[str, Any]] = None) -> Optional[CastInfo]:
    v = decode_packet(spec, content, partial=True)  # the fields after the end position are not needed
    if not v:
        return None
    f = fields or CAST_FIELDS
    return CastInfo(caster_netid=_netid(v, f["caster"]), spell_hash=_int(v, f["spell_hash"]),
                    seq=_int(v, f["seq"]), start=_xz(v, f["start"]), end=_xz(v, f["end"]),
                    cast_time=_float(v, f["cast_time"]), targets=_targets(v, f.get("targets")))


def decode_damage(spec: dict, content: bytes, fields: Mapping[str, Any]) -> Optional[DamageInfo]:
    """One damage instance (16.20+): who took how much from whom, through which spell script."""
    try:
        v = decode_packet(spec, content)
    except DecodeError:
        return None
    f = fields
    script = _int(v, f["script"])
    return DamageInfo(target_netid=_netid(v, f["target"]), source_netid=_netid(v, f["source"]),
                      amount=_float(v, f["amount"]), script_hash=script if script else None,
                      kind=_int(v, f["kind"]) if "kind" in f else None)


def decode_attack(spec: dict, content: bytes, fields: Mapping[str, Any]) -> Optional[AttackInfo]:
    """A basic-attack start (16.20+): the attacker is the packet's object, the target is a unit."""
    try:
        v = decode_packet(spec, content)
    except DecodeError:
        return None
    return AttackInfo(target_netid=_netid(v, fields["target"]), target_pos=_xz(v, fields["pos"]))
