"""Generic packet-content decoder driven by a JSON spec produced by tools/re/specgen.py.

Spec model: content = [flag bytes][fields in `order`]. Each field owns a 1/2/3-bit group in the flag
bit-stream; the mode selects a constant (stored-domain bytes) or an explicit read: a helper
(fixed-K bytes with per-byte tables, varint with a 7-bit table, or a blob) or inline code with
equivalent tables. Nested objects carry their own flag bytes and fields at `base`.
"""
from __future__ import annotations

import struct
from dataclasses import dataclass
from typing import Any, Dict, Optional, Tuple


class DecodeError(ValueError):
    """The content does not fit the spec (truncated or unknown mode)."""


@dataclass(frozen=True)
class Cursor:
    data: bytes
    pos: int

    def take(self, n: int) -> Tuple[bytes, "Cursor"]:
        if self.pos + n > len(self.data):
            raise DecodeError(f"need {n} bytes at {self.pos}, have {len(self.data) - self.pos}")
        return self.data[self.pos:self.pos + n], Cursor(self.data, self.pos + n)


def _flag_value(flags: bytes, bit: int, width: int) -> int:
    value = int.from_bytes(flags, "little")
    return (value >> bit) & ((1 << width) - 1)


def _assemble(plain_bytes, order: str, shifts) -> int:
    if order == "be":
        v = 0
        for b in plain_bytes:
            v = (v << 8) | b
        return v
    if order == "le":
        return int.from_bytes(bytes(plain_bytes), "little")
    v = 0
    for b, sh in zip(plain_bytes, shifts):
        v |= b << (sh or 0)
    return v


def _decode_fixed(tables: dict, cur: Cursor) -> Tuple[int, Cursor]:
    width = len(tables["plain"])
    raw, cur = cur.take(width)
    plain = [tables["plain"][i][raw[i]] for i in range(width)]
    return _assemble(plain, tables.get("order"), tables.get("shifts")), cur


def _decode_fixedv(h: dict, cur: Cursor, base_off: int) -> Tuple[Dict[int, int], Cursor]:
    """Multi-word fixed read (e.g. a vec3): each component gathers its own payload positions."""
    raw, cur = cur.take(h["width"])
    out: Dict[int, int] = {}
    for comp in h["components"]:
        plain = [comp["plain"][i][raw[p]] for i, p in enumerate(comp["positions"])]
        out[base_off + comp["offset"]] = _assemble(plain, comp.get("order"), comp.get("shifts"))
    return out, cur


def _decode_count(action: dict, cur: Cursor) -> Tuple[int, Cursor]:
    """Element count of a vector: a 7-bit varint with per-byte tables (or a plain 1-byte table)."""
    if "count_table" in action:
        raw, cur = cur.take(1)
        return action["count_table"][raw[0]], cur
    value = 0
    shift = 0
    while True:
        raw, cur = cur.take(1)
        value |= action["val7"][raw[0]] << shift
        if not action["cont"][raw[0]]:
            return value, cur
        shift += 7
        if shift > 28:
            raise DecodeError("vector count too long")


def _decode_varint(h: dict, cur: Cursor) -> Tuple[int, Cursor]:
    value = 0
    shift = 0
    while True:
        raw, cur = cur.take(1)
        b = raw[0]
        value |= h["val7"][b] << shift
        shift += 7
        if not h["cont"][b]:
            break
        if shift > 35:
            raise DecodeError("varint too long")
    if h.get("netid_tag") and value & 0xFFFFFF:
        value |= 0x40000000
    return value, cur


def _decode_blob(h: dict, cur: Cursor) -> Tuple[bytes, Cursor]:
    table = h["table"]
    if table is None:
        raise DecodeError("blob helper without table")
    # length: 7-bit varint through the same byte table
    n = 0
    shift = 0
    while True:
        raw, cur = cur.take(1)
        d = table[raw[0]]
        n |= (d & 0x7F) << shift
        shift += 7
        if not d & 0x80:
            break
    raw, cur = cur.take(n)
    out = bytearray(n)
    order = h.get("order8")
    i, j, k = 0, n - 1, 0
    while i < j:
        out[i] = table[raw[k]]
        out[j] = table[raw[k + 1]]
        i += 1
        j -= 1
        k += 2
    if i == j:
        out[i] = table[raw[k]]
    return bytes(out), cur


def _stored_to_plain(stored_value: int, size: int, tabs: Optional[dict]) -> Any:
    stored = stored_value.to_bytes(size, "little")
    if tabs and tabs.get("stored_to_plain"):
        plain = [tabs["stored_to_plain"][i][stored[i]] for i in range(min(size, len(tabs["stored_to_plain"])))]
        return _assemble(plain, tabs.get("order"), tabs.get("shifts"))
    return {"stored": stored_value}


def _const_plain(action: dict, field: dict, helpers: dict, base: int) -> Dict[int, Any]:
    """Constant stored bytes -> plain using the stored->plain tables of the field's explicit helper.
    Multi-word constants (vec3) carry `extra` = [[offset, size, stored], ...]."""
    off = action.get("offset", 0)
    parts = action.get("extra") or [[off, action.get("size", 4), action["stored"]]]
    out: Dict[int, Any] = {}
    for o, size, stored in parts:
        out[base + o] = _stored_to_plain(stored, size, _field_tables(field, helpers, o - off))
    return out


def _field_tables(field: dict, helpers: dict, comp_offset: int = 0) -> Optional[dict]:
    for a in field["modes"].values():
        tabs = None
        if a["kind"] == "helper":
            tabs = helpers.get(a["helper"])
        elif a["kind"] == "inline":
            tabs = a.get("tables")
        if not tabs:
            continue
        if tabs.get("kind") == "fixedv":
            for comp in tabs["components"]:
                if comp["offset"] == comp_offset:
                    return comp
        elif comp_offset == 0 and tabs.get("plain"):
            return tabs
    return None


class PartialDecode(DecodeError):
    """Raised inside a partial decode to unwind with what was decoded so far."""

    def __init__(self, out: Dict[int, Any], cur: Cursor, cause: DecodeError):
        super().__init__(str(cause))
        self.out = out
        self.cur = cur


def decode_object(spec: dict, cur: Cursor, helpers: dict, base: int = 0, partial: bool = False) -> Tuple[Dict[int, Any], Cursor]:
    flags, cur = cur.take(spec["flag_bytes"])
    out: Dict[int, Any] = {}
    for field in spec["fields"]:
        try:
            out_field, cur = _decode_field(field, flags, cur, helpers, base, partial)
        except PartialDecode as exc:
            out.update(exc.out)
            raise PartialDecode(out, exc.cur, exc)
        except DecodeError as exc:
            if partial:
                raise PartialDecode(out, cur, exc)
            raise
        out.update(out_field)
    return out, cur


def _decode_field(field: dict, flags: bytes, cur: Cursor, helpers: dict, base: int, partial: bool) -> Tuple[Dict[int, Any], Cursor]:
    width = {"3bit": 3, "2bit": 2, "bit": 1}[field["kind"]]
    mode = _flag_value(flags, field["bit"], width)
    action = field["modes"].get(str(mode), field["modes"].get(mode))
    if action is None:
        return {}, cur
    kind = action["kind"]
    off = base + action.get("offset", 0)
    out: Dict[int, Any] = {}
    if kind in ("none", "nested_default"):
        return out, cur
    if kind == "const":
        out.update(_const_plain(action, field, helpers, base))
    elif kind == "helper":
        h = helpers[action["helper"]]
        if h["kind"] == "fixed":
            out[off], cur = _decode_fixed(h, cur)
        elif h["kind"] == "fixedv":
            vals, cur = _decode_fixedv(h, cur, off)
            out.update(vals)
        elif h["kind"] == "varint":
            out[off], cur = _decode_varint(h, cur)
        elif h["kind"] == "blob":
            out[off], cur = _decode_blob(h, cur)
        else:
            raise DecodeError(f"unsupported helper kind {h['kind']} at {off:#x}")
    elif kind == "inline":
        tabs = action.get("tables")
        if tabs and tabs.get("kind") == "fixedv":
            vals, cur = _decode_fixedv(tabs, cur, off)
            out.update(vals)
        elif not tabs or not tabs.get("plain"):
            raw, cur = cur.take(action["consumed"])
            out[off] = {"raw": raw.hex()}
        else:
            out[off], cur = _decode_fixed(tabs, cur)
    elif kind == "vector":
        count, cur = _decode_count(action, cur)
        body, cur = cur.take(count * action.get("elem", 1))
        out[off] = {"count": count, "raw": body.hex()}
    elif kind == "nested":
        sub = action.get("nested")
        if not sub:
            raise DecodeError(f"nested field at {off:#x} without spec")
        nested_out, cur = decode_object(sub, cur, helpers, base + action["base"], partial)
        out.update(nested_out)
    else:
        raise DecodeError(f"unknown action kind {kind}")
    return out, cur


def decode_packet(spec: dict, content: bytes, partial: bool = False) -> Dict[int, Any]:
    """Decode one packet's content into {object offset: plain value}.

    With `partial`, a field that does not fit ends the decode and the fields decoded before it are
    returned (the trailing fields of a spec may be less certain than the leading ones)."""
    try:
        out, cur = decode_object(spec, Cursor(content, 0), spec.get("helpers", {}), 0, partial)
    except PartialDecode as exc:
        return exc.out
    return out


def as_float(v: Any) -> Optional[float]:
    if isinstance(v, int):
        return struct.unpack("<f", struct.pack("<I", v & 0xFFFFFFFF))[0]
    return None
