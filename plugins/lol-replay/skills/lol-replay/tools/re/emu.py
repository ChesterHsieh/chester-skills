"""Unicorn harness around the client's packet factory and deserializers (offline research only).

Addresses are detected from the binary (see `detect`), so a new patch needs no hand-edited constants:
  factory     the switch over packet type ids that allocates a packet object and sets its vtable
  alloc       the allocator the factory calls (`mov edi, SIZE; call alloc`)
  pool        the small-object allocator `alloc` tail-jumps to: pool(pool_ptr, size)
  base_deser  the base-class deserializer every packet deserializer calls first; replay contents do
              not include the bytes it reads, so it is stubbed to return true without consuming
  pool_init   the allocator's one-time setup (mmap...): stubbed, the pool itself is replaced
  pool_free   the pool's free: stubbed
  team_cfg    a global the team-mask reader needs (null until a game is loaded; a team mask is
              `bits` wide, 4 by the constructor's default, and the reader requires +0x70 set)
"""
import json
import os
import re
import struct
from collections import Counter

from unicorn import Uc, UC_ARCH_X86, UC_MODE_64, UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.x86_const import (UC_X86_REG_RAX, UC_X86_REG_RCX, UC_X86_REG_RDI, UC_X86_REG_RDX, UC_X86_REG_RIP,
                               UC_X86_REG_RSI, UC_X86_REG_RSP, UC_X86_REG_R8, UC_X86_REG_R9,
                               UC_X86_REG_FS_BASE, UC_X86_REG_GS_BASE)

import client
from disasm import insn

STACK, STACK_SZ = 0x7f0000000000, 0x100000
HEAP, HEAP_SZ = 0x600000000000, 0x4000000
INBUF = 0x500000000000
RET = 0x700000000000
DUMMY = 0x710000000000
PAGE = 0x1000


def _al(x):
    return (x + PAGE - 1) & ~(PAGE - 1)


def _calls_in(start, limit):
    out = []
    addr = start
    end = start + limit
    while addr < end:
        try:
            m, ops, sz = insn(addr)
        except StopIteration:
            break
        if m == 'call' and ops.startswith('0x'):
            out.append(int(ops, 16))
        if m == 'ret':
            break
        addr += sz
    return out


def detect():
    """Find factory / alloc / pool / base_deser in the client; cached next to the executable."""
    cache = client.CLIENT + '.addr.json'
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(client.CLIENT):
        cached = {k: int(v, 16) for k, v in json.load(open(cache)).items()}
        if 'team_cfg' in cached:  # caches from before pool_init / pool_free / team_cfg are redone
            return cached
    factory = None
    for f in client.FUNCS:
        try:
            b = client.read(f, 24)
        except KeyError:
            continue
        i = b.find(b'\x81\xfe')  # cmp esi, imm32
        if 0 <= i <= 14 and b[i + 6:i + 8] == b'\x0f\x87':
            n = struct.unpack_from('<I', b, i + 2)[0]
            if 900 <= n <= 4000:
                if factory is not None:
                    raise SystemExit(f'two factory candidates: {factory:#x} {f:#x}')
                factory = f
    if factory is None:
        raise SystemExit('packet factory not found')
    calls = Counter(_calls_in(factory, client.func_end(factory) - factory))
    alloc = calls.most_common(1)[0][0]
    pool = None
    addr = alloc
    for _ in range(40):
        m, ops, sz = insn(addr)
        if m == 'jmp' and ops.startswith('0x'):
            pool = int(ops, 16)
            break
        addr += sz
    found = {'factory': factory, 'alloc': alloc, 'pool': pool or 0}
    init = _calls_in(alloc, 40)
    found['pool_init'] = init[0] if init else 0
    found['pool_free'] = _pool_free(alloc, pool)
    found['team_cfg'] = _team_cfg()
    emu = Emu(found)
    firsts = Counter()
    for t in range(1, 400, 7):
        try:
            o = emu.make_packet(t)
            fn = emu.q(emu.q(o) + 8)
        except Exception:
            continue
        c = _calls_in(fn, 64)
        if c:
            firsts[c[0]] += 1
    found['base_deser'] = firsts.most_common(1)[0][0]
    json.dump({k: hex(v) for k, v in found.items()}, open(cache, 'w'), indent=1)
    return found


def _pool_free(alloc, pool):
    """`mov rsi, rdi; mov rdi, [rip+X]; pop rbp; jmp FREE` next to the allocator (FREE != pool)."""
    pat = re.compile(rb'\x55\x48\x89\xe5\x48\x89\xfe\x48\x8b\x3d(....)\x5d\xe9(....)', re.S)
    lo = alloc - 0x400
    data = client.read(lo, 0x800)
    for m in pat.finditer(data):
        target = lo + m.end() + struct.unpack('<i', m.group(2))[0]
        if target != pool:
            return target
    return 0


def _team_cfg():
    """The global read by `mov rax, [rip+X]; movzx ecx, byte [rax+8]; mov eax, -1; shl eax, cl; not eax;
    and eax, edi` (team mask = value & ((1 << bits) - 1))."""
    pat = re.compile(re.escape(bytes.fromhex('0fb64808b8ffffffffd3e0f7d021f8')))
    for name, base, data in client.SEGS:
        if name != '__TEXT':
            continue
        for m in pat.finditer(data):
            i = m.start() - 7
            if data[i:i + 3] == b'\x48\x8b\x05':
                return base + i + 7 + struct.unpack_from('<i', data, i + 3)[0]
    return 0


TEAM_MASK_BITS = 4


class Emu:
    def __init__(self, addrs=None):
        self.addrs = addrs or detect()
        mu = Uc(UC_ARCH_X86, UC_MODE_64)
        for name, base, data in client.SEGS:
            mu.mem_map(base, _al(len(data)))
            mu.mem_write(base, data)
        for base, size in ((STACK, STACK_SZ), (HEAP, HEAP_SZ), (INBUF, 0x100000), (RET, PAGE), (DUMMY, PAGE),
                           (0x7e0000000000, 0x10000)):
            mu.mem_map(base, size)
        mu.mem_write(RET, b'\xf4')
        mu.mem_write(DUMMY, b'\xc3' * PAGE)
        mu.reg_write(UC_X86_REG_GS_BASE, 0x7e0000000000)
        mu.reg_write(UC_X86_REG_FS_BASE, 0x7e0000008000)
        # unresolved imports in __got (stack guard, function pointers): point them at a page of `ret`
        got = client.section('__got')
        if got:
            for a in range(got[2], got[2] + got[3], 8):
                if struct.unpack('<Q', mu.mem_read(a, 8))[0] == 0:
                    mu.mem_write(a, struct.pack('<Q', DUMMY))
        self.mu = mu
        self.heap = HEAP
        self.unknown = []
        stubs = client.section('__stubs')
        mu.hook_add(UC_HOOK_CODE, self._stub_hook, begin=stubs[2], end=stubs[2] + stubs[3])
        a = self.addrs
        mu.hook_add(UC_HOOK_CODE, lambda mu, x, s, u: self._ret(self.alloc(mu.reg_read(UC_X86_REG_RDI))),
                    begin=a['alloc'], end=a['alloc'])
        if a.get('pool'):
            mu.hook_add(UC_HOOK_CODE, lambda mu, x, s, u: self._ret(self.alloc(mu.reg_read(UC_X86_REG_RSI))),
                        begin=a['pool'], end=a['pool'])
        for k in ('pool_init', 'pool_free'):
            if a.get(k):
                mu.hook_add(UC_HOOK_CODE, lambda mu, x, s, u: self._ret(0), begin=a[k], end=a[k])
        if a.get('team_cfg'):
            cfg = self.alloc(0x100)
            mu.mem_write(cfg + 8, bytes([TEAM_MASK_BITS]))
            mu.mem_write(cfg + 0x70, b'\x01')
            mu.mem_write(a['team_cfg'], struct.pack('<Q', cfg))

    def alloc(self, n):
        p = (self.heap + 15) & ~15
        n = max(n, 16)
        self.heap = p + n
        self.mu.mem_write(p, b'\0' * n)
        return p

    def _ret(self, value=None):
        mu = self.mu
        rsp = mu.reg_read(UC_X86_REG_RSP)
        ret = struct.unpack('<Q', mu.mem_read(rsp, 8))[0]
        mu.reg_write(UC_X86_REG_RSP, rsp + 8)
        if value is not None:
            mu.reg_write(UC_X86_REG_RAX, value)
        mu.reg_write(UC_X86_REG_RIP, ret)

    def _stub_hook(self, mu, addr, size, ud):
        name = client.STUBS.get(addr)
        if name is None:
            return
        rdi, rsi, rdx = (mu.reg_read(r) for r in (UC_X86_REG_RDI, UC_X86_REG_RSI, UC_X86_REG_RDX))
        if name in ('_memcpy', '_memmove', '___memcpy_chk', '___memmove_chk'):
            if rdx:
                mu.mem_write(rdi, bytes(mu.mem_read(rsi, rdx)))
            return self._ret(rdi)
        if name in ('_memset', '___memset_chk'):
            if rdx:
                mu.mem_write(rdi, bytes([rsi & 0xff]) * rdx)
            return self._ret(rdi)
        if name in ('_bzero', '___bzero'):
            if rsi:
                mu.mem_write(rdi, b'\0' * rsi)
            return self._ret(0)
        if name in ('_malloc', '__Znwm', '__Znam', '_calloc'):
            return self._ret(self.alloc(rdi * rsi if name == '_calloc' else rdi))
        if name in ('_free', '__ZdlPv', '__ZdaPv'):
            return self._ret(0)
        self.unknown.append(name)
        return self._ret(0)

    def call(self, fn, *args, count=5_000_000, hooks=()):
        mu = self.mu
        regs = (UC_X86_REG_RDI, UC_X86_REG_RSI, UC_X86_REG_RDX, UC_X86_REG_RCX, UC_X86_REG_R8, UC_X86_REG_R9)
        for r, a in zip(regs, args):
            mu.reg_write(r, a)
        sp = STACK + STACK_SZ - 0x1000
        mu.mem_write(sp, struct.pack('<Q', RET))
        mu.reg_write(UC_X86_REG_RSP, sp)
        hs = [mu.hook_add(t, cb, **kw) for t, cb, kw in hooks]
        try:
            mu.emu_start(fn, RET, count=count)
        finally:
            for h in hs:
                mu.hook_del(h)
        return mu.reg_read(UC_X86_REG_RAX)

    def q(self, a):
        return struct.unpack('<Q', self.mu.mem_read(a, 8))[0]

    def make_packet(self, ptype):
        out = self.alloc(16)
        self.call(self.addrs['factory'], out, ptype)
        return self.q(out)


class Run:
    def __init__(self, ok, consumed, obj, writes):
        self.ok, self.consumed, self.obj, self.writes = ok, consumed, obj, writes


class Deser:
    """Run replay packet contents through the client deserializers (base-class header stubbed)."""

    def __init__(self):
        self.e = Emu()
        base = self.e.addrs['base_deser']
        self.e.mu.hook_add(UC_HOOK_CODE, lambda mu, a, s, u: self.e._ret(1), begin=base, end=base)

    def run(self, ptype, content, trace=True, obj_size=0x400):
        e = self.e
        save = e.heap
        o = e.make_packet(ptype)
        fn = e.q(e.q(o) + 8)
        cur = STACK  # the cursor lives at the bottom of the stack mapping, away from the object
        e.mu.mem_write(INBUF, bytes(content) + b'\0' * 64)
        e.mu.mem_write(cur, struct.pack('<Q', INBUF))
        writes = []
        hooks = []
        if trace:
            def wh(mu, access, addr, size, value, ud):
                if o <= addr < o + obj_size:
                    writes.append((mu.reg_read(UC_X86_REG_RIP), addr - o, size, value & ((1 << (8 * size)) - 1)))
            hooks = [(UC_HOOK_MEM_WRITE, wh, {})]
        ok = e.call(fn, o, cur, INBUF + len(content), hooks=hooks) & 0xff
        consumed = e.q(cur) - INBUF
        obj = bytes(e.mu.mem_read(o, obj_size))
        e.heap = save
        return Run(ok, consumed, obj, writes)


if __name__ == '__main__':
    print({k: hex(v) for k, v in detect().items()})
