"""Instruction lookup (cached) and call-target resolution; `python3 disasm.py ADDR [N]` prints code."""
import struct
import sys

import capstone
import unicorn

import client

_MD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
_MDD = capstone.Cs(capstone.CS_ARCH_X86, capstone.CS_MODE_64)
_MDD.detail = True
_cache = {}


def insn(addr):
    """(mnemonic, operands, size) of the instruction at addr."""
    i = _cache.get(addr)
    if i is None:
        d = next(_MD.disasm(client.read(addr, 16), addr))
        i = _cache[addr] = (d.mnemonic, d.op_str, d.size)
    return i


def reg_value(mu, name):
    return mu.reg_read(getattr(unicorn.x86_const, 'UC_X86_REG_' + name.upper()))


def call_target(mu, addr):
    """Target of the call instruction at addr under the current register state."""
    i = next(_MDD.disasm(client.read(addr, 16), addr))
    op = i.operands[0]
    if op.type == capstone.x86.X86_OP_IMM:
        return op.imm
    if op.type == capstone.x86.X86_OP_REG:
        return reg_value(mu, i.reg_name(op.reg))
    m = op.mem
    base = 0
    if m.base == capstone.x86.X86_REG_RIP:
        base = addr + i.size
    elif m.base:
        base = reg_value(mu, i.reg_name(m.base))
    ea = base + (reg_value(mu, i.reg_name(m.index)) * m.scale if m.index else 0) + m.disp
    return struct.unpack('<Q', mu.mem_read(ea, 8))[0]


def show(addr, n=40):
    for d in _MD.disasm(client.read(addr, n * 15), addr):
        print(hex(d.address), d.mnemonic, d.op_str)
        n -= 1
        if not n:
            break


if __name__ == '__main__':
    show(int(sys.argv[1], 16), int(sys.argv[2]) if len(sys.argv) > 2 else 40)
