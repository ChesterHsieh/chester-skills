"""Generate a generic packet-content spec (lolreplay/payload/generic.py format) from the client.

    LOL_CLIENT=/path/LeagueofLegends python3 specgen.py --out ../../lolreplay/specs/<patch> TYPE [TYPE ...]

For one packet type: build the object from the client's packet factory, discover its flag-bit groups
(explore.py), give every reader a decoding table (readers.py), recurse into nested objects and object
vectors, learn inline reads, order the fields by dispatch order and turn constant modes into plain
values through the field's tail inverse. Validate the result with validate.py and fuzz.py.
"""
import json
import os
import sys

from unicorn import UC_HOOK_CODE
from unicorn.x86_const import *
import client
import emu
import explore as X
import readers as H

class SpecGen:
    def __init__(self):
        self.d = emu.Deser()
        self.e = self.d.e
        self.helpers = {}
        self.objects = {}
        self.errors = []
        self._alloc_sizes = []
        self.e.mu.hook_add(UC_HOOK_CODE, lambda mu, a, s, u: self._alloc_sizes.append(mu.reg_read(UC_X86_REG_RDI)),
                           begin=self.e.addrs['alloc'], end=self.e.addrs['alloc'])

    def make(self, ptype):
        self._alloc_sizes.clear()
        o = self.e.make_packet(ptype)
        size = self._alloc_sizes[0] if self._alloc_sizes else 0x400
        return o, self.e.q(self.e.q(o) + 8), size

    def helper(self, tgt, template=None):
        key = hex(tgt)
        if template is not None and tgt not in H.TEMPLATE:
            H.TEMPLATE[tgt] = template
        if key not in self.helpers:
            try:
                self.helpers[key] = h = H.characterize(self.e, tgt)
                if h['kind'] == 'object_vector':
                    self._element_spec(key, h)
            except Exception as exc:
                self.errors.append(f'helper {key}: {exc}')
                self.helpers[key] = {'kind': 'unknown', 'error': str(exc),
                                     'consumption': list(map(str, H.classify(self.e, tgt)[:2]))}
        return self.helpers[key]

    def build_object(self, fn, this, size, label):
        e = self.e
        nflag, base, aff, groups = X.explore_object(e, fn, this, size)
        spec = {'label': label, 'deser': hex(fn), 'obj_size': size, 'flag_bytes': nflag, 'fields': []}
        consuming = {}
        for g in groups:
            if g != list(range(g[0], g[0] + len(g))) or len(g) > 3:
                self.errors.append(f'{label}: group {g} not a contiguous 1-3 bit group')
            modes = X.group_modes(e, fn, this, size, nflag, g)
            call_offs, byte_offs, acts = X.describe_group(modes, nflag)
            offs = list(call_offs) + list(byte_offs)
            if not offs and not any(a['inline'] for a in acts.values()):
                continue
            field_off = min(offs) if offs else None
            field = {'bit': g[0], 'kind': {1: 'bit', 2: '2bit', 3: '3bit'}.get(len(g), f'{len(g)}bit'), 'modes': {}}
            helper_sizes = []
            for m, a in acts.items():
                if a['ok'] != 1:
                    field['modes'][str(m)] = {'kind': 'invalid'}
                    continue
                if len(a['calls']) > 1:
                    self.errors.append(f'{label}: group {g} mode {m} makes {len(a["calls"])} calls')
                if a['calls']:
                    off, (tgt, vt) = next(iter(a['calls'].items()))
                    if client.is_vtable(vt) and e.q(vt + 8) == tgt:
                        sub_label = f'{label}@{off:#x}'
                        sub = self.build_object(tgt, this + off, max(0x20, size - off), sub_label)
                        field['modes'][str(m)] = {'kind': 'nested', 'offset': off, 'base': off, 'nested': sub}
                    else:
                        h = self.helper(tgt, bytes(e.mu.mem_read(this + off, H.DST_SIZE)))
                        field['modes'][str(m)] = {'kind': 'helper', 'offset': off, 'helper': hex(tgt)}
                        helper_sizes.append(h)
                    consuming.setdefault(id(field), (g, m))
                elif a['inline']:
                    field['modes'][str(m)] = {'kind': 'inline', 'offset': field_off, 'consumed': a['consumed'],
                                              'rips': list(a['inline'])}
                    consuming.setdefault(id(field), (g, m))
                else:
                    field['modes'][str(m)] = {'kind': 'const_pending', 'offset': field_off, 'mode_bytes': a['bytes']}
            # resolve consts over the field's slot(s)
            for m, act in field['modes'].items():
                if act['kind'] != 'const_pending':
                    continue
                final = modes[int(m)].final
                if field_off is None:
                    act.clear()
                    act['kind'] = 'none'
                    continue
                slots = self._field_slots(field_off, helper_sizes, byte_offs)
                parts = [[o, s, int.from_bytes(final[o:o + s], 'little')] for o, s in slots]
                act.clear()
                act.update({'kind': 'const', 'offset': parts[0][0], 'size': parts[0][1], 'stored': parts[0][2]})
                if len(parts) > 1:
                    act['extra'] = parts
            field['_group'] = g
            spec['fields'].append(field)
        self._order(fn, this, size, nflag, spec, consuming)
        for f in spec['fields']:
            f.pop('_group', None)
        self._learn_inline(fn, this, size, nflag, spec)
        return spec

    def _element_spec(self, key, h):
        """Build the element object's spec from the element sample captured while learning the count."""
        ptr, vt, deser, snap = h.pop('element_sample')
        vt_bytes = snap[:8]
        stride = next((k for k in range(8, len(snap) - 7, 8) if snap[k:k + 8] == vt_bytes), len(snap))
        this = self.e.alloc(stride + 0x40)
        self.e.mu.mem_write(this, snap[:stride])
        h['elem_size'] = stride
        h['element'] = self.build_object(deser, this, stride, f'{key}[]')

    @staticmethod
    def _field_slots(field_off, helper_sizes, byte_offs):
        for h in helper_sizes:
            if h['kind'] == 'fixedv':
                return [(field_off + c['offset'], c['size']) for c in h['components']]
            if h.get('size'):
                return [(field_off, h['size'])]
        if byte_offs:
            lo, hi = min(byte_offs), max(byte_offs) + 1
            size = 1 if hi - lo == 1 else 2 if hi - lo <= 2 else 4 if hi - lo <= 4 else hi - lo
            return [(field_off, size)]
        return [(field_off, 4)]

    def _order(self, fn, this, size, nflag, spec, consuming):
        """Dispatch order: run with every field in a consuming mode and read the event sequence."""
        flags = 0
        for f in spec['fields']:
            gm = consuming.get(id(f))
            if gm:
                g, m = gm
                for k, b in enumerate(g):
                    if m >> k & 1:
                        flags |= 1 << b
        r = X.run_body(self.e, fn, this, flags.to_bytes(nflag, 'little'), size)
        seq = []
        for ev in r.events:
            if ev[0] == 'call' and 0 < ev[1] < size:
                seq.append(('call', ev[1]))
            elif ev[0] == 'read' and ev[1] >= nflag:
                seq.append(('read', ev[1], ev[3]))
        for idx, f in enumerate(spec['fields']):
            gm = consuming.get(id(f))
            order = 10 ** 6 + idx
            if gm:
                act = f['modes'][str(gm[1])]
                if act['kind'] in ('helper', 'nested'):
                    key = ('call', act['offset'])
                    if key in seq:
                        order = seq.index(key)
                elif act['kind'] == 'inline':
                    for k, s in enumerate(seq):
                        if s[0] == 'read' and s[2] in act['rips']:
                            order = k
                            break
            f['order'] = order
        spec['fields'].sort(key=lambda f: f['order'])
        for k, f in enumerate(spec['fields']):
            f['order'] = k

    def _learn_inline(self, fn, this, size, nflag, spec):
        """Inline reads in the body: fixed 1-byte fields get a value table, varints a cont table.
        Only reads made by the field's own instructions count; the varied byte is the first one they read."""
        for f in spec['fields']:
            for m, act in f['modes'].items():
                if act['kind'] != 'inline':
                    continue
                rips = set(act.pop('rips'))
                width = 1 if f['kind'] == 'bit' else int(f['kind'][:-3])
                g = list(range(f['bit'], f['bit'] + width))
                flags = X._set_bits(nflag, g, int(m))
                off = act['offset']
                probe = X.run_body(self.e, fn, this, flags, size)
                fill = getattr(probe, 'filler', 0)
                firsts = [ev[1] for ev in probe.events if ev[0] == 'read' and ev[3] in rips]
                if not firsts:
                    self.errors.append(f'{spec["label"]}: inline field {off} not reproduced')
                    continue
                p = firsts[0] - nflag
                def plain_at(r):
                    ws = [(k, ev[4], ev[1], ev[2], ev[3]) for k, ev in enumerate(r.events) if ev[0] == 'write']
                    ps = H.plain_slots(H.HelperRun(1, 0, ws, [], r.final))
                    cands = [k for k in ps if off is not None and k[0] == off and k[1] <= 4]
                    if not cands:
                        return None
                    return ps[max(cands, key=lambda k: k[1])]
                def run_with(seq):
                    payload = bytearray([fill]) * 512
                    payload[p:p + len(seq)] = bytes(seq)
                    r = X.run_body(self.e, fn, this, flags, size, payload=bytes(payload))
                    return r, sum(ev[2] for ev in r.events if ev[0] == 'read' and ev[3] in rips)
                cons, values = [], []
                for v in range(256):
                    r, c = run_with([v])
                    cons.append(c)
                    values.append(plain_at(r))
                base_cons = min(cons)
                if max(cons) > 2:
                    self._inline_vector(act, cons, run_with)
                    continue
                if len(set(cons)) == 1:
                    act['consumed'] = cons[0]
                    if cons[0] == 1 and None not in values:
                        act['tables'] = {'kind': 'fixed', 'plain': [values], 'shifts': [0]}
                    continue
                if set(cons) != {base_cons, base_cons + 1} or base_cons != 1:
                    self.errors.append(f'{spec["label"]}: inline field {off} consumption {sorted(set(cons))}')
                    continue
                cont = [1 if c > base_cons else 0 for c in cons]
                terms = [v for v in range(256) if not cont[v]]
                tvals = {v: values[v] for v in terms}
                tag = int(any(x is not None and x & 0x40000000 for x in tvals.values()))
                val7 = [0] * 256
                ok_values = all(x is not None for x in tvals.values())
                if ok_values:
                    for v, x in tvals.items():
                        val7[v] = x & ~0x40000000 if tag else x
                    ok_values = all(val7[v] <= 0x7F for v in terms)
                if ok_values:
                    t = next((v for v in terms if val7[v] == 1), None)
                    for v in range(256):
                        if cont[v] and t is not None:
                            r, c = run_with([v, t])
                            x = plain_at(r)
                            if x is None:
                                ok_values = False
                                break
                            x = x & ~0x40000000 if tag else x
                            if x >> 7 != 1:
                                ok_values = False
                                break
                            val7[v] = x & 0x7F
                act['kind'] = 'inline_varint'
                act['size'] = 4
                act['cont'] = cont
                act['val7'] = val7 if ok_values else [0] * 256
                act['netid_tag'] = tag
                if not ok_values:
                    act['value_unknown'] = True
                act.pop('consumed', None)

    def _inline_vector(self, act, cons, run_with):
        """Inline count-prefixed byte vector: count = 7-bit varint (per-byte tables), then count bytes."""
        val7, cont = [0] * 256, [0] * 256
        for v in range(256):
            r, c = run_with([v])
            if r.ok == 1:
                val7[v] = c - 1
            else:
                cont[v] = 1
        t = next((v for v in range(256) if not cont[v] and val7[v] == 1), None)
        for v in range(256):
            if cont[v]:
                r, c = run_with([v, t])
                n = c - 2
                if r.ok != 1 or n >> 7 != 1:
                    act['kind'] = 'invalid'
                    act['note'] = 'inline vector count not learned'
                    return
                val7[v] = n & 0x7F
        if sorted(val7[v] for v in range(256) if not cont[v]) != list(range(128)):
            act['kind'] = 'invalid'
            act['note'] = 'inline vector count bytes do not cover 0..127'
            return
        off = act.get('offset')
        act.clear()
        act.update({'kind': 'vector', 'offset': off, 'val7': val7, 'cont': cont, 'elem': 1})

    def _tail_inverse(self, helper_key, slot_off, slot_size, samples=1500):
        """stored byte -> plain byte, per byte of a helper's slot, from random inputs (bytewise tail)."""
        cache = self.__dict__.setdefault('_tails', {})
        key = (helper_key, slot_off, slot_size)
        if key in cache:
            return cache[key]
        import random
        h = self.helpers.get(helper_key) or {}
        fn = int(helper_key, 16)
        rnd = random.Random(11)
        inv = [dict() for _ in range(slot_size)]
        bad = False
        width = h.get('width') or 6
        for _ in range(samples):
            if h.get('kind') in ('varint', 'object_vector', 'vector'):
                n = rnd.randrange(1, 6)
                data = bytes(rnd.choice([b for b in range(256) if h['cont'][b]]) for _ in range(n - 1))
                data += bytes([rnd.choice([b for b in range(256) if not h['cont'][b]])])
            else:
                data = bytes(rnd.randrange(256) for _ in range(width))
            r = H.run_helper(self.e, fn, data + bytes(16))
            plain = H.plain_slots(r).get((slot_off, slot_size))
            if plain is None:
                bad = True
                break
            stored = r.final[slot_off:slot_off + slot_size]
            for j in range(slot_size):
                pb, sb = (plain >> 8 * j) & 0xFF, stored[j]
                if inv[j].get(sb, pb) != pb:
                    bad = True
                inv[j][sb] = pb
        cache[key] = None if bad else inv
        return cache[key]

    def _const_plains(self, obj):
        """Give const modes their plain value when a helper of the same field reveals the tail."""
        for f in obj['fields']:
            helpers = [a['helper'] for a in f['modes'].values() if a['kind'] == 'helper'
                       and self.helpers.get(a['helper'], {}).get('kind') in ('fixed', 'fixedv', 'varint')
                       and not self.helpers[a['helper']].get('value_unknown')]
            for a in f['modes'].values():
                if a['kind'] == 'nested':
                    self._const_plains(a['nested'])
                if a['kind'] != 'const' or not helpers:
                    continue
                parts = a.get('extra') or [[a['offset'], a['size'], a['stored']]]
                base = a['offset']
                plains = []
                for off, size, stored in parts:
                    value = None
                    for hk in helpers:
                        inv = self._tail_inverse(hk, off - base, size)
                        if inv is None:
                            continue
                        sb = stored.to_bytes(size, 'little')
                        if all(sb[j] in inv[j] for j in range(size)):
                            value = sum(inv[j][sb[j]] << 8 * j for j in range(size))
                            break
                    plains.append(value)
                if all(v is not None for v in plains):
                    a['plain'] = plains[0]
                    if len(plains) > 1:
                        a['extra_plain'] = plains

    def build(self, ptype):
        o, fn, size = self.make(ptype)
        spec = self.build_object(fn, o, size, f'type{ptype}')
        spec['helpers'] = self.helpers
        self._const_plains(spec)
        for h in self.helpers.values():
            if h.get('kind') == 'object_vector':
                self._const_plains(h['element'])
        return spec


def main(argv):
    import argparse
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('types', nargs='+', type=int, help='packet type ids')
    ap.add_argument('--out', required=True, help='directory for packet_<type>.json (e.g. lolreplay/specs/16.20)')
    args = ap.parse_args(argv)
    os.makedirs(args.out, exist_ok=True)
    for t in args.types:
        g = SpecGen()
        spec = g.build(t)
        path = os.path.join(args.out, f'packet_{t}.json')
        with open(path, 'w') as fh:
            json.dump(spec, fh)
        print(t, 'fields', len(spec['fields']), 'helpers', len(spec['helpers']), 'errors', len(g.errors), path)
        for err in g.errors:
            print('  !', err[:300])
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
