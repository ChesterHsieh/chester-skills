"""The x86_64 slice of the macOS game client: segments, sections, function starts and import stubs.

The executable path comes from $LOL_CLIENT (see fetch_client.py). Parsing it with LIEF takes a while,
so the result is cached next to the executable as <name>.x64.pkl.
"""
import bisect
import os
import pickle
import struct

import lief

CLIENT = os.environ.get('LOL_CLIENT', '')
CPU_X86_64 = 0x01000007


def _stub_names(path, b, slice_off):
    """__stubs entry address -> imported symbol name (via the indirect symbol table)."""
    raw = open(path, 'rb').read()
    stubs = next(s for s in b.sections if s.name == '__stubs')
    ind = b.dynamic_symbol_command
    symtab = b.symbol_command
    strtab = raw[slice_off + symtab.strings_offset: slice_off + symtab.strings_offset + symtab.strings_size]

    def symname(i):
        e = slice_off + symtab.symbol_offset + 16 * i
        strx = struct.unpack('<I', raw[e:e + 4])[0]
        return strtab[strx:strtab.index(b'\0', strx)].decode()

    names = {}
    for i in range(stubs.size // 6):
        at = slice_off + ind.indirect_symbol_offset + 4 * (stubs.reserved1 + i)
        idx = struct.unpack('<I', raw[at:at + 4])[0]
        names[stubs.virtual_address + 6 * i] = symname(idx) if idx < 0x40000000 else '?'
    return names


def _slice_offset(path):
    raw = open(path, 'rb').read(4096)
    if raw[:4] != b'\xca\xfe\xba\xbe':
        return 0
    for k in range(struct.unpack('>I', raw[4:8])[0]):
        ct, cs, off, size, al = struct.unpack('>iiIII', raw[8 + 20 * k:28 + 20 * k])
        if ct == CPU_X86_64:
            return off
    raise ValueError('no x86_64 slice in the client')


def _load():
    if not CLIENT or not os.path.isfile(CLIENT):
        raise SystemExit('set LOL_CLIENT to the macOS LeagueofLegends executable (tools/re/fetch_client.py downloads it)')
    cache = CLIENT + '.x64.pkl'
    if os.path.exists(cache) and os.path.getmtime(cache) >= os.path.getmtime(CLIENT):
        return pickle.load(open(cache, 'rb'))
    fat = lief.MachO.parse(CLIENT)
    b = next(x for x in fat if x.header.cpu_type == lief.MachO.Header.CPU_TYPE.X86_64)
    segs = []
    for s in b.segments:
        if s.virtual_address and s.name != '__LINKEDIT':
            segs.append((s.name, s.virtual_address, bytes(s.content) + b'\0' * (s.virtual_size - len(s.content))))
    d = dict(segs=segs,
             sects=[(s.segment.name, s.name, s.virtual_address, s.size) for s in b.sections],
             starts=sorted(b.function_starts.functions) if b.has_function_starts else [],
             stubs=_stub_names(CLIENT, b, _slice_offset(CLIENT)))
    pickle.dump(d, open(cache, 'wb'))
    return d


D = _load()
SEGS = D['segs']
STUBS = D['stubs']
FUNCS = sorted(f + 0x100000000 if f < 0x100000000 else f for f in D['starts'])
TEXT = next(s for s in SEGS if s[0] == '__TEXT')


def section(name):
    return next((s for s in D['sects'] if s[1] == name), None)


def read(va, n):
    for name, base, data in SEGS:
        if base <= va < base + len(data):
            return data[va - base: va - base + n]
    raise KeyError(hex(va))


def q(va):
    return struct.unpack('<Q', read(va, 8))[0]


def func_of(va):
    i = bisect.bisect_right(FUNCS, va) - 1
    return FUNCS[i] if i >= 0 else None


def func_end(f):
    i = bisect.bisect_right(FUNCS, f)
    return FUNCS[i] if i < len(FUNCS) else f + 0x1000


def is_vtable(va):
    return any(name == '__DATA_CONST' and base <= (va or 0) < base + len(data) for name, base, data in SEGS)
