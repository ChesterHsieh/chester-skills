"""Black-box characterization of field reader helpers: helper(dst, &cursor, end) -> bool.

A helper reads payload bytes at the cursor and stores a value at dst. In memory the value passes
through an accumulate phase (the *plain* value) and is then rewritten by a tail transform
(the *stored* value). `plain_of` recovers the plain value from the write trace:

  writes to the slot = [optional init 0] [accumulate writes, one rip] [tail writes, other rips]

plain = last accumulate value, byte-swapped when a `bswap` instruction ran after it.
"""
import random, struct
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.x86_const import *
import client
import emu
from disasm import call_target, insn, reg_value

DST_SIZE = 0x40


class HelperRun:
    def __init__(self, ok, consumed, writes, bswaps, final):
        self.ok, self.consumed, self.writes, self.bswaps, self.final = ok, consumed, writes, bswaps, final


TEMPLATE = {}  # helper -> destination bytes from the constructed object (containers need valid pointers)


def run_helper(e, fn, data, end_pad=True):
    """Call helper fn on `data`; returns HelperRun. Writes are (seq, rip, off, size, value)."""
    mu = e.mu
    save = e.heap
    dst = e.alloc(DST_SIZE + 0x100)
    if fn in TEMPLATE:
        mu.mem_write(dst, TEMPLATE[fn][:DST_SIZE])
    cur = emu.STACK
    mu.mem_write(emu.INBUF, bytes(data) + b'\0' * 16)
    mu.mem_write(cur, struct.pack('<Q', emu.INBUF))
    writes, bswaps = [], []
    seq = [0]

    def wh(mu, access, addr, size, value, ud):
        if dst <= addr < dst + DST_SIZE:
            seq[0] += 1
            writes.append((seq[0], mu.reg_read(UC_X86_REG_RIP), addr - dst, size, value & ((1 << 8 * size) - 1)))

    def code(mu, addr, size, ud):
        m, ops, sz = insn(addr)
        if m == 'bswap':
            seq[0] += 1
            bswaps.append((seq[0], reg_value(mu, ops)))

    hooks = [(UC_HOOK_MEM_WRITE, wh, {}), (UC_HOOK_CODE, code, {})]
    ok = e.call(fn, dst, cur, emu.INBUF + len(data), hooks=hooks) & 0xff
    consumed = e.q(cur) - emu.INBUF
    final = bytes(mu.mem_read(dst, DST_SIZE))
    e.heap = save
    return HelperRun(ok, consumed, writes, bswaps, final)


def _bswap(v, size):
    return int.from_bytes(v.to_bytes(size, 'little'), 'big')


def plain_slots(run):
    """{(off, size): plain} for every written slot (see module doc).

    The last write to a slot is the tail when it is what the slot finally holds and comes from a
    different instruction than the write before it; the plain value is then the write before it."""
    keys = []
    for s, rip, off, size, val in run.writes:
        if (off, size) not in keys:
            keys.append((off, size))
    out = {}
    for off, size in keys:
        ws = [w for w in run.writes if w[2] == off and w[3] == size]
        final = int.from_bytes(run.final[off:off + size], 'little')
        last = ws[-1]
        if len(ws) >= 2 and final == ws[-1][4] and ws[-1][1] != ws[-2][1]:
            last = ws[-2]
        plain = last[4]
        nxt = [w[0] for w in run.writes if w[0] > last[0] and w[2] < off + size and off < w[2] + w[3]]
        limit = nxt[0] if nxt else 10 ** 9
        mask = (1 << 8 * size) - 1
        if any(last[0] < b[0] < limit and (b[1] & mask) == plain for b in run.bswaps):
            plain = _bswap(plain, size)
        out[(off, size)] = plain
    return out


def classify(e, fn, trials=24, seed=1):
    """Consumption pattern of a helper: ('fixed', K) or ('var', None)."""
    rnd = random.Random(seed)
    cons = set()
    oks = set()
    for _ in range(trials):
        data = bytes(rnd.randrange(256) for _ in range(96))
        r = run_helper(e, fn, data)
        cons.add(r.consumed)
        oks.add(r.ok)
    if len(cons) == 1:
        return ('fixed', cons.pop(), oks)
    return ('var', sorted(cons), oks)


def _value_slots(e, fn, width, rnd):
    """Plain slots that actually depend on the input (size 1/2/4; 8 only if nothing smaller varies)."""
    samples = []
    for _ in range(6):
        data = bytes(rnd.randrange(256) for _ in range(width)) + bytes(64)
        samples.append(plain_slots(run_helper(e, fn, data)))
    keys = set(samples[0])
    for s in samples[1:]:
        keys &= set(s)
    varying = [k for k in keys if len({s[k] for s in samples}) > 1]
    # drop slots covered by a larger varying slot of size <= 4, drop 8-byte tails if smaller exist
    small = [k for k in varying if k[1] <= 4]
    chosen = []
    for k in sorted(small, key=lambda k: -k[1]):
        if not any(c[0] <= k[0] and k[0] + k[1] <= c[0] + c[1] for c in chosen):
            chosen.append(k)
    if not chosen:
        chosen = varying
    return sorted(chosen)


def learn_fixed(e, fn, width, seed=7):
    """Per raw byte position: which (slot, byte) of the plain value it lands in, and the 256-table."""
    rnd = random.Random(seed)
    slots = _value_slots(e, fn, width, rnd)
    base = bytes(width)
    def plains(data):
        ps = plain_slots(run_helper(e, fn, data + bytes(64)))
        return {k: ps.get(k) for k in slots}
    p0 = plains(base)
    comps = {k: {'offset': k[0], 'size': k[1], 'positions': [], 'plain': [], 'shifts': []} for k in slots}
    for i in range(width):
        table = None
        where = None
        vals = []
        for v in range(256):
            d = bytearray(base); d[i] = v
            vals.append(plains(bytes(d)))
        # find the slot/byte that varies with position i
        for k in slots:
            for bi in range(k[1]):
                col = [(p[k] >> (8 * bi)) & 0xFF for p in vals]
                if len(set(col)) > 1:
                    if where is not None:
                        raise ValueError(f'{fn:#x}: raw byte {i} feeds several plain bytes')
                    where = (k, bi)
                    table = col
        if where is None:
            raise ValueError(f'{fn:#x}: raw byte {i} has no effect')
        c = comps[where[0]]
        c['positions'].append(i)
        c['plain'].append(table)
        c['shifts'].append(8 * where[1])
    # constant part (plain bytes no position feeds) must be zero for an OR assembly
    out = []
    for k in slots:
        c = comps[k]
        fed = {s // 8 for s in c['shifts']}
        for bi in range(k[1]):
            if bi not in fed and (p0[k] >> (8 * bi)) & 0xFF:
                raise ValueError(f'{fn:#x}: plain byte {bi} of {k} is constant {p0[k]:#x}')
        out.append(c)
    # verify on random inputs
    for _ in range(40):
        data = bytes(rnd.randrange(256) for _ in range(width))
        ps = plains(data)
        for c in out:
            v = 0
            for pos, tab, sh in zip(c['positions'], c['plain'], c['shifts']):
                v |= tab[data[pos]] << sh
            if v != ps[(c['offset'], c['size'])]:
                raise ValueError(f'{fn:#x}: not byte-separable at {c["offset"]:#x}')
    if len(out) == 1 and out[0]['offset'] == 0:
        c = out[0]
        return {'kind': 'fixed', 'width': width, 'size': c['size'], 'plain': c['plain'], 'shifts': c['shifts'],
                'positions': c['positions']}
    return {'kind': 'fixedv', 'width': width, 'components': out}


def learn_varint(e, fn):
    """7-bit varint with per-byte tables: val7[b] (7 bits) and cont[b]."""
    val7, cont = [0] * 256, [0] * 256
    raw_plain = {}
    for v in range(256):
        r = run_helper(e, fn, bytes([v]) + bytes(8))
        cont[v] = 1 if r.consumed != 1 else 0
        if not cont[v]:
            ps = plain_slots(r)
            raw_plain[v] = ps
    # value slot = the varying slot among terminators
    keys = None
    for ps in raw_plain.values():
        keys = set(ps) if keys is None else keys & set(ps)
    cands = [k for k in keys if k[0] == 0 and len({ps[k] for ps in raw_plain.values()}) > 1]
    slot = max(cands, key=lambda k: (k[1] <= 4, k[1]))
    tag = 0
    vals = {v: ps[slot] for v, ps in raw_plain.items()}
    if all((x & 0x40000000) or x == 0 for x in vals.values()) and any(x & 0x40000000 for x in vals.values()):
        tag = 1
    for v, x in vals.items():
        val7[v] = x & ~0x40000000 if tag else x
        if val7[v] > 0x7F:
            raise ValueError(f'{fn:#x}: terminator {v:#x} -> {x:#x} exceeds 7 bits')
    # continuation bytes: pair with a terminator t whose val7 is known and nonzero bits land at <<7
    t = next(v for v in range(256) if not cont[v] and val7[v] == 1)
    for v in range(256):
        if cont[v]:
            r = run_helper(e, fn, bytes([v, t]) + bytes(8))
            if r.consumed != 2:
                raise ValueError(f'{fn:#x}: {v:#x},{t:#x} consumed {r.consumed}')
            x = plain_slots(r)[slot]
            if tag:
                x &= ~0x40000000
            low = x & 0x7F
            if x >> 7 != 1:
                raise ValueError(f'{fn:#x}: shift is not 7 ({v:#x} -> {x:#x})')
            val7[v] = low
    h = {'kind': 'varint', 'size': slot[1], 'val7': val7, 'cont': cont, 'netid_tag': tag}
    # verify on random multi-byte sequences
    rnd = random.Random(3)
    for _ in range(60):
        n = rnd.randrange(1, 5)
        seq = [rnd.choice([b for b in range(256) if cont[b]]) for _ in range(n - 1)]
        seq.append(rnd.choice([b for b in range(256) if not cont[b]]))
        r = run_helper(e, fn, bytes(seq) + bytes(8))
        if r.consumed != n:
            raise ValueError(f'{fn:#x}: consumed {r.consumed} != {n}')
        value = 0
        for k, b in enumerate(seq):
            value |= val7[b] << (7 * k)
        value &= (1 << (8 * slot[1])) - 1
        if tag and value & 0xFFFFFF:
            value |= 0x40000000
        got = plain_slots(r)[slot]
        if got != value:
            raise ValueError(f'{fn:#x}: varint mismatch {bytes(seq).hex()} {got:#x} != {value:#x}')
    return h


def characterize(e, fn):
    c = classify(e, fn)
    if c[0] == 'fixed' and c[1] == 0:
        return {'kind': 'skip', 'width': 0}
    if c[0] == 'fixed':
        return learn_fixed(e, fn, c[1])
    try:
        return learn_varint(e, fn)
    except Exception as exc:
        first = exc
    try:
        return learn_object_vector(e, fn)
    except Exception as exc:
        third = exc
    try:
        return learn_varint_vector(e, fn)
    except Exception as exc:
        fourth = exc
    try:
        return learn_vector(e, fn)
    except Exception as exc:
        second = exc
    try:
        return learn_varint_consumption(e, fn)
    except Exception as exc:
        raise ValueError(f'varint: {first}; object-vector: {third}; varint-vector: {fourth}; vector: {second}; '
                         f'varint-consumption: {exc}')


def learn_vector(e, fn, buf=4096):
    """Count-prefixed vector/string: count = 7-bit varint (per-byte tables), then count*elem bytes."""
    import math
    val7, cont = [0] * 256, [0] * 256
    sizes = {}
    for v in range(256):
        r = run_helper(e, fn, bytes([v]) + bytes(buf))
        sizes[v] = (r.ok, r.consumed)
    # bytes whose consumption hits the buffer end or fails are continuation candidates; elem from gcd
    g = 0
    for v, (ok, c) in sizes.items():
        if ok == 1 and c - 1 < 600:
            g = math.gcd(g, c - 1)
    elem = g or 1
    term = {v for v, (ok, c) in sizes.items() if ok == 1 and (c - 1) % elem == 0 and (c - 1) // elem < 128}
    for v in range(256):
        if v in term:
            val7[v] = (sizes[v][1] - 1) // elem
        else:
            cont[v] = 1
    # a continuation byte may also look like a short terminator; check with a second byte
    t = next(v for v in sorted(term) if val7[v] == 1)
    for v in range(256):
        r = run_helper(e, fn, bytes([v, t]) + bytes(buf))
        if cont[v]:
            n = (r.consumed - 2) / elem
            if r.ok != 1 or n != int(n) or int(n) >> 7 != 1:
                raise ValueError(f'{fn:#x}: vector count byte {v:#x} not a 7-bit continuation ({r.ok}, {r.consumed})')
            val7[v] = int(n) & 0x7F
    # element byte table (strings / byte vectors): stored = table[raw] at the data pointer
    table = None
    if elem == 1:
        lead = next(v for v in sorted(term) if val7[v] == 16)
        tab = [None] * 256
        for start in range(0, 256, 16):
            raw = bytes(range(start, start + 16))
            r = run_helper(e, fn, bytes([lead]) + raw + bytes(64))
            ptr = int.from_bytes(r.final[0:8], 'little')
            try:
                stored = bytes(e.mu.mem_read(ptr, 16))
            except Exception:
                stored = None
            if stored is None:
                break
            for k in range(16):
                tab[start + k] = stored[k]
        if None not in tab:
            table = tab
    return {'kind': 'vector', 'val7': val7, 'cont': cont, 'elem': elem, 'elem_table': table}


def learn_varint_vector(e, fn):
    """Vector of u32 varints: {ptr, u32 count, u32 capacity} at dst; a count varint, then `count`
    varints through the same byte tables, accumulated straight into the array (no tail transform).
    The client may fill the array from the last element to the first (`reverse`)."""
    def run(data):
        r = run_helper(e, fn, data)
        ptr, count = struct.unpack_from('<QI', r.final, 0)
        values = []
        if r.ok == 1 and count:
            values = list(struct.unpack(f'<{count}I', bytes(e.mu.mem_read(ptr, 4 * count))))
        return r, count, values
    zero = next((v for v in range(256) if run_helper(e, fn, bytes([v])).ok == 1
                 and run_helper(e, fn, bytes([v])).consumed == 1), None)
    if zero is None:
        raise ValueError(f'{fn:#x}: no empty-vector byte')
    val7, cont = [0] * 256, [0] * 256
    for v in range(256):
        r, count, values = run(bytes([v]) + bytes([zero]) * 200)
        if r.ok != 1 or count > 127 or values != [0] * count:
            raise ValueError(f'{fn:#x}: byte {v:#x} is not a count ({r.ok}, {count})')
        extra = r.consumed - 1 - count
        if extra not in (0, 1):
            raise ValueError(f'{fn:#x}: byte {v:#x} consumed {r.consumed} for {count} elements')
        val7[v], cont[v] = count, extra
    one = next(v for v in range(256) if not cont[v] and val7[v] == 1)
    two = next(v for v in range(256) if not cont[v] and val7[v] == 2)
    for v in range(256):  # element bytes use the same tables
        r, count, values = run(bytes([one, v]) + bytes([zero]) * 8)
        if values != [val7[v]] or r.consumed != 2 + cont[v]:
            raise ValueError(f'{fn:#x}: element byte {v:#x} -> {values} ({r.consumed})')
    a, b = (next(v for v in range(256) if not cont[v] and val7[v] == k) for k in (5, 9))
    _, _, values = run(bytes([two, a, b]))
    if sorted(values) != [5, 9]:
        raise ValueError(f'{fn:#x}: two elements read as {values}')
    h = {'kind': 'varint_vector', 'val7': val7, 'cont': cont, 'reverse': values == [9, 5]}
    rnd = random.Random(5)
    terms = [v for v in range(256) if not cont[v]]
    conts = [v for v in range(256) if cont[v]]
    for _ in range(30):
        n = rnd.randrange(1, 6)
        seq, want = [next(v for v in terms if val7[v] == n)], []
        for _ in range(n):
            k = rnd.randrange(1, 4)
            item = [rnd.choice(conts) for _ in range(k - 1)] + [rnd.choice(terms)]
            seq += item
            want.append(sum(val7[x] << (7 * i) for i, x in enumerate(item)) & 0xFFFFFFFF)
        r, count, values = run(bytes(seq))
        if h['reverse']:
            values = values[::-1]
        if r.consumed != len(seq) or values != want:
            raise ValueError(f'{fn:#x}: varint vector mismatch on {bytes(seq).hex()}')
    return h


def learn_varint_consumption(e, fn):
    """Varint whose plain value never reaches memory (accumulated in a register, only the tail is
    stored): learn the continuation table only; values are left unknown."""
    cont = [0] * 256
    for v in range(256):
        r = run_helper(e, fn, bytes([v]) + bytes(32))
        cont[v] = 0 if (r.ok == 1 and r.consumed == 1) else 1
    terms = [v for v in range(256) if not cont[v]]
    conts = [v for v in range(256) if cont[v]]
    if not terms or len(terms) == 256:
        raise ValueError(f'{fn:#x}: no varint continuation split')
    rnd = random.Random(9)
    for _ in range(40):
        n = rnd.randrange(1, 5)
        seq = [rnd.choice(conts) for _ in range(n - 1)] + [rnd.choice(terms)]
        r = run_helper(e, fn, bytes(seq) + bytes(8))
        if r.consumed != n or r.ok != 1:
            raise ValueError(f'{fn:#x}: consumption model fails on {bytes(seq).hex()} ({r.ok}, {r.consumed})')
    return {'kind': 'varint', 'size': 4, 'val7': [0] * 256, 'cont': cont, 'netid_tag': 0, 'value_unknown': True}


ELEM_SNAP = 0x80


def run_skipping_elements(e, fn, data):
    """Run a vector helper with every element deserializer call skipped; returns (run, element calls)."""
    mu = e.mu
    cur = emu.STACK
    elems = []

    def code(mu, addr, size, ud):
        m, ops, sz = insn(addr)
        if m != 'call' or mu.reg_read(UC_X86_REG_RSI) != cur:
            return
        rdi = mu.reg_read(UC_X86_REG_RDI)
        try:
            vt = e.q(rdi)
            tgt = call_target(mu, addr)
        except Exception:
            return
        if client.is_vtable(vt) and e.q(vt + 8) == tgt:
            elems.append((rdi, vt, tgt, bytes(mu.mem_read(rdi, ELEM_SNAP))))
            mu.reg_write(UC_X86_REG_RAX, 1)
            mu.reg_write(UC_X86_REG_RIP, addr + sz)

    save = e.heap
    dst = e.alloc(DST_SIZE + 0x100)
    if fn in TEMPLATE:
        mu.mem_write(dst, TEMPLATE[fn][:DST_SIZE])
    mu.mem_write(emu.INBUF, bytes(data) + b'\0' * 16)
    mu.mem_write(cur, struct.pack('<Q', emu.INBUF))
    ok = e.call(fn, dst, cur, emu.INBUF + len(data), hooks=[(UC_HOOK_CODE, code, {})]) & 0xff
    consumed = e.q(cur) - emu.INBUF
    e.heap = save
    return ok, consumed, elems


def learn_object_vector(e, fn):
    """Vector of nested objects: count = 7-bit varint (per-byte tables), then count element objects."""
    val7, cont = [0] * 256, [0] * 256
    sample = None
    for v in range(256):
        ok, consumed, elems = run_skipping_elements(e, fn, bytes([v]) + bytes(1024))
        if ok == 1 and consumed == 1:
            val7[v] = len(elems)
            if elems and sample is None:
                sample = elems[0]
        else:
            cont[v] = 1
    if sample is None:
        raise ValueError(f'{fn:#x}: no element object seen')
    terms = [v for v in range(256) if not cont[v]]
    if sorted(val7[v] for v in terms) != list(range(128)):
        raise ValueError(f'{fn:#x}: count bytes do not cover 0..127')
    t = next(v for v in terms if val7[v] == 1)
    for v in range(256):
        if cont[v]:
            ok, consumed, elems = run_skipping_elements(e, fn, bytes([v, t]) + bytes(1024))
            n = len(elems)
            if ok != 1 or consumed != 2 or n >> 7 != 1:
                raise ValueError(f'{fn:#x}: count byte {v:#x} is not a 7-bit continuation ({ok}, {consumed}, {n})')
            val7[v] = n & 0x7F
    return {'kind': 'object_vector', 'val7': val7, 'cont': cont, 'element_sample': sample}
