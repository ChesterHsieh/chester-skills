"""Discover a deserializer's flag-group structure by differential runs with reader calls intercepted.

Every reader call made by the object's own deserializer body (call with rsi == &cursor) is skipped:
we only record (dst offset, target). The payload is therefore never consumed by helpers, so a run
depends on the flag bytes alone (plus any inline reads in the body, recorded by position).
"""
import struct
from collections import defaultdict
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_READ, UC_HOOK_MEM_WRITE
from unicorn.x86_const import *
import client
import emu
from disasm import call_target, insn

FILL = 0x00
SLOT_SPAN = 4  # bytes of a reader destination owned by its call (vec3 fields have one call per component slot set)


class BodyRun:
    def __init__(self, ok, consumed, events, final, flag_bytes):
        self.ok, self.consumed, self.events, self.final, self.flag_bytes = ok, consumed, events, final, flag_bytes


FILLERS = (0x00, 0x80, 0x40, 0xFF, 0x7F, 0x01, 0xC0, 0x20, 0xA5, 0x5A, 0x10, 0x08)


def run_body(e, fn, this, flags, obj_size, payload=None, max_flag=64):
    """Run `fn` with the given flags; without an explicit payload, try filler bytes until the client
    accepts the object (an inline varint needs a filler that terminates it)."""
    if payload is not None:
        return _run_body(e, fn, this, flags, obj_size, payload)
    first = None
    for f in FILLERS:
        r = _run_body(e, fn, this, flags, obj_size, bytes([f]) * 512)
        r.filler = f
        if r.ok == 1:
            return r
        first = first or r
    return first


def _run_body(e, fn, this, flags, obj_size, payload=None, max_flag=64):
    """Run deserializer `fn` on object `this` with the given flag bytes, intercepting reader calls."""
    mu = e.mu
    cur = emu.STACK
    data = bytes(flags) + (payload if payload is not None else bytes([FILL]) * 512)
    mu.mem_write(emu.INBUF, data)
    mu.mem_write(cur, struct.pack('<Q', emu.INBUF))
    snapshot = bytes(mu.mem_read(this, obj_size))
    events = []
    state = {'pending': None, 'first_cur': None, 'skip_ret': None}
    fn_end = client.func_end(fn)

    def code(mu, addr, size, ud):
        if fn <= addr < fn_end:
            m, ops, sz = insn(addr)
            if m == 'call' and mu.reg_read(UC_X86_REG_RSI) == cur:
                rdi = mu.reg_read(UC_X86_REG_RDI)
                tgt = call_target(mu, addr)
                vt = None
                try:
                    vt = e.q(rdi)
                except Exception:
                    pass
                events.append(('call', rdi - this, tgt, e.q(cur) - emu.INBUF, vt))
                mu.reg_write(UC_X86_REG_RAX, 1)
                mu.reg_write(UC_X86_REG_RIP, addr + sz)

    def rh(mu, access, addr, size, value, ud):
        if emu.INBUF <= addr < emu.INBUF + len(data):
            events.append(('read', addr - emu.INBUF, size, mu.reg_read(UC_X86_REG_RIP)))

    def wh(mu, access, addr, size, value, ud):
        if addr == cur and state['first_cur'] is None:
            state['first_cur'] = value - emu.INBUF
        if this <= addr < this + obj_size:
            events.append(('write', addr - this, size, value & ((1 << 8 * size) - 1), mu.reg_read(UC_X86_REG_RIP)))

    hooks = [(UC_HOOK_CODE, code, {}), (UC_HOOK_MEM_READ, rh, {}), (UC_HOOK_MEM_WRITE, wh, {})]
    try:
        ok = e.call(fn, this, cur, emu.INBUF + len(data), hooks=hooks, count=200000) & 0xff
    except Exception:  # the client faulted: treat like a rejected packet
        ok = -1
    consumed = e.q(cur) - emu.INBUF
    final = bytes(mu.mem_read(this, obj_size))
    mu.mem_write(this, snapshot)
    return BodyRun(ok, consumed, events, final, state['first_cur'])


def slot_actions(run, nflag):
    """{offset: action} from one body run. Inline reads are payload reads past the flag bytes."""
    acts = {}
    inline_pos = []
    for ev in run.events:
        if ev[0] == 'call':
            if ev[1] != 0:  # offset 0 is the base-class header read
                acts[ev[1]] = ('call', ev[2])
        elif ev[0] == 'read' and ev[1] >= nflag:
            inline_pos.append(ev[1])
    return acts, inline_pos


def _set_bits(nflag, bits, value):
    v = 0
    for k, b in enumerate(bits):
        if value >> k & 1:
            v |= 1 << b
    return v.to_bytes(nflag, 'little')


def _calls(run, obj_size=None):
    size = obj_size or len(run.final)
    return {ev[1]: (ev[2], ev[4]) for ev in run.events if ev[0] == 'call' and 0 < ev[1] < size}


def _slots(run):
    """write slots (off, size) made by the body, in order."""
    out = []
    for ev in run.events:
        if ev[0] == 'write' and (ev[1], ev[2]) not in out:
            out.append((ev[1], ev[2]))
    return out


def _inline(run, nflag):
    return tuple(ev[1] for ev in run.events if ev[0] == 'read' and ev[1] >= nflag)


def _signature(run, nflag, obj_size=None):
    """Structural events of a run (independent of the payload filler): calls by destination,
    writes by (offset, size, instruction) and payload reads by instruction."""
    size = obj_size or len(run.final)
    sig = set()
    for ev in run.events:
        if ev[0] == 'call' and 0 < ev[1] < size:
            sig.add(('call', ev[1], ev[2]))
        elif ev[0] == 'write':
            sig.add(('w', ev[1], ev[2], ev[4]))
        elif ev[0] == 'read' and ev[1] >= nflag:
            sig.add(('r', ev[3]))
    return sig


def _affected(a, b, nflag):
    """units (destination offsets, payload-read instructions) whose behaviour differs between runs."""
    offs = set()
    def writes(run):
        return [(ev[1], ev[2], ev[4], ev[3]) for ev in run.events if ev[0] == 'write']
    wa, wb = writes(a), writes(b)
    def unmatched(xs, ys):
        by_rip = {(o, n, r) for o, n, r, v in ys}
        by_val = {(o, n, v) for o, n, r, v in ys}
        return [x for x in xs if (x[0], x[1], x[2]) not in by_rip and (x[0], x[1], x[3]) not in by_val]
    for o, n, r, v in unmatched(wa, wb) + unmatched(wb, wa):
        for i in range(o, o + n):
            offs.add(('byte', i))
    for ev in _signature(a, nflag) ^ _signature(b, nflag):
        if ev[0] == 'call':
            offs.add(('call', ev[1]))
        elif ev[0] == 'r':
            offs.add(('rip', ev[1]))
    # const modes write the same instructions with other immediates: compare final bytes too when
    # both runs used the same filler (otherwise inline values differ for unrelated reasons)
    if getattr(a, 'filler', None) == getattr(b, 'filler', None):
        for i, (x, y) in enumerate(zip(a.final, b.final)):
            if x != y:
                offs.add(('byte', i))
    return offs


_SPAN = {}


def span_of(e, tgt, vt):
    """Bytes of the destination a reader call owns: its value slots (nested objects: 0)."""
    if tgt in _SPAN:
        return _SPAN[tgt]
    if vt is not None and client.is_vtable(vt) and e.q(vt + 8) == tgt:
        _SPAN[tgt] = 0  # nested object: its bytes belong to its own fields
        return 0
    import readers as H, random
    try:
        c = H.classify(e, tgt, trials=6)
        if c[0] == 'fixed' and c[1] == 0:
            span = 0
        else:
            width = c[1] if c[0] == 'fixed' else 8
            slots = H._value_slots(e, tgt, width, random.Random(5))
            span = max((o + s for o, s in slots), default=SLOT_SPAN)
    except Exception:
        span = SLOT_SPAN
    _SPAN[tgt] = span
    return span


def explore_object(e, fn, this, obj_size, label=''):
    base = run_body(e, fn, this, b'', obj_size)
    nflag = base.flag_bytes or 0
    base = run_body(e, fn, this, bytes(nflag), obj_size)
    bit_aff = {}
    for b in range(nflag * 8):
        r = run_body(e, fn, this, _set_bits(nflag, [b], 1), obj_size)
        aff = _affected(base, r, nflag)
        if r.ok != 1:
            aff.add(('invalid', b))
        bit_aff[b] = aff
    # union bits sharing affected units; a byte inside a reader call's destination belongs to that call
    spans = {}
    for r in [base] + [run_body(e, fn, this, _set_bits(nflag, [b], 1), obj_size) for b in range(nflag * 8)]:
        for off, (tgt, vt) in _calls(r, obj_size).items():
            spans[off] = max(spans.get(off, 0), span_of(e, tgt, vt))
    def owner_key(u):
        if u[0] == 'byte':
            cs = [c for c, sp in spans.items() if c <= u[1] < c + sp]
            if cs:
                return ('call', max(cs))
        return u
    parent = list(range(nflag * 8))
    def find(x):
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x
    owner = {}
    for b, aff in bit_aff.items():
        for u in aff:
            if u[0] in ('inline', 'invalid'):
                continue
            key = owner_key(u)
            if key in owner:
                parent[find(b)] = find(owner[key])
            else:
                owner[key] = b
    groups = defaultdict(list)
    for b in range(nflag * 8):
        if bit_aff[b]:
            groups[find(b)].append(b)
    return nflag, base, bit_aff, [sorted(g) for g in groups.values()]


def group_modes(e, fn, this, obj_size, nflag, bits):
    """Per mode of a bit group: (ok, calls, final bytes, inline positions, consumed)."""
    out = {}
    for m in range(1 << len(bits)):
        r = run_body(e, fn, this, _set_bits(nflag, bits, m), obj_size)
        out[m] = r
    return out


def _read_rips(run, nflag):
    """payload reads (past the flag bytes) per instruction: {rip: bytes read}"""
    out = {}
    for ev in run.events:
        if ev[0] == 'read' and ev[1] >= nflag:
            out[ev[3]] = out.get(ev[3], 0) + ev[2]
    return out


def describe_group(modes, nflag):
    """Units that differ between modes, and each mode's action on them."""
    calls = {m: _calls(r) for m, r in modes.items()}
    call_offs = set()
    for m in modes:
        for off, (t, vt) in calls[m].items():
            if any(calls[k].get(off, (None,))[0] != t for k in modes):
                call_offs.add(off)
    byte_offs = set()
    ref = modes[0].final
    for m, r in modes.items():
        for i, (x, y) in enumerate(zip(ref, r.final)):
            if x != y:
                byte_offs.add(i)
    rips = {m: _read_rips(r, nflag) for m, r in modes.items()}
    own = set()
    for m in modes:
        for rip, n in rips[m].items():
            if any(rips[k].get(rip) != n for k in modes):
                own.add(rip)
    acts = {}
    for m, r in modes.items():
        mine = {rip: n for rip, n in rips[m].items() if rip in own}
        a = {'ok': r.ok, 'calls': {o: calls[m][o] for o in sorted(call_offs) if o in calls[m]},
             'inline': tuple(sorted(mine)), 'consumed': sum(mine.values())}
        a['bytes'] = {i: r.final[i] for i in sorted(byte_offs)}
        acts[m] = a
    return sorted(call_offs), sorted(byte_offs), acts
