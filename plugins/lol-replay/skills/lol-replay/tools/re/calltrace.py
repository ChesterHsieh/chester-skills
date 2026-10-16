"""Trace reader calls made by a deserializer on a real packet: which reader (at any depth) read which
payload bytes into which object offset, with the writes and bswaps it made. Used as the oracle by validate.py."""
import struct
from unicorn import UC_HOOK_CODE, UC_HOOK_MEM_WRITE
from unicorn.x86_const import *
import emu
from disasm import insn, reg_value

class Tracer:
    def __init__(self, d=None):
        self.d = d or emu.Deser()

    def trace(self, ptype, content):
        e = self.d.e
        mu = e.mu
        save = e.heap
        o = e.make_packet(ptype)
        fn = e.q(e.q(o) + 8)
        cur = emu.STACK
        mu.mem_write(emu.INBUF, bytes(content) + b'\0' * 64)
        mu.mem_write(cur, struct.pack('<Q', emu.INBUF))
        events = []
        stack = []  # open reader calls: (ret_addr, rsp, event)
        def code(mu, addr, size, ud):
            # close finished calls
            while stack and addr == stack[-1][0] and mu.reg_read(UC_X86_REG_RSP) == stack[-1][1]:
                _, _, ev = stack.pop()
                ev['end'] = e.q(cur) - emu.INBUF
                ev['ret'] = mu.reg_read(UC_X86_REG_RAX) & 0xff
            m, ops, sz = insn(addr)
            if m == 'bswap' and stack:
                ev = stack[-1][2]
                for _, _, outer in stack[:-1]:
                    outer.setdefault('bswaps', []).append((len(outer['writes']) - 0.5, None))
                ev.setdefault('bswaps', []).append((len(ev['writes']) - 0.5, reg_value(mu, ops)))
            if m == 'call' and mu.reg_read(UC_X86_REG_RSI) == cur and o <= mu.reg_read(UC_X86_REG_RDI) < o + 0x400:
                rdi = mu.reg_read(UC_X86_REG_RDI)
                rsp = mu.reg_read(UC_X86_REG_RSP)
                # target
                if ops.startswith('0x'):
                    tgt = int(ops, 16)
                else:
                    tgt = None
                ev = dict(site=addr, target=tgt, ops=ops, off=rdi - o, start=e.q(cur) - emu.INBUF, depth=len(stack), writes=[])
                events.append(ev)
                stack.append((addr + sz, rsp, ev))
        def wh(mu, access, addr, size, value, ud):
            if o <= addr < o + 0x400 and stack:
                stack[-1][2]['writes'].append((mu.reg_read(UC_X86_REG_RIP), addr - o, size, value & ((1 << 8 * size) - 1)))
        hs = [(UC_HOOK_CODE, code, {}), (UC_HOOK_MEM_WRITE, wh, {})]
        ok = e.call(fn, o, cur, emu.INBUF + len(content), hooks=hs) & 0xff
        consumed = e.q(cur) - emu.INBUF
        self.final = bytes(mu.mem_read(o, 0x400))
        e.heap = save
        return ok, consumed, events, o, fn
