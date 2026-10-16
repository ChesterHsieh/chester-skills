"""Compare the generic Python decoder with the client (emulator) on every packet of a replay.

    LOL_CLIENT=... LOL_REPLAY=game.rofl python3 validate.py TYPE packet_TYPE.json [LIMIT]

Prints decode errors and, per object offset, how many packets decode to the client's plain value."""
import json, sys
from collections import Counter
import calltrace as T
import readers as H
from replay import of
from lolreplay.payload.generic import DecodeError, decode_packet


def oracle(tr, ptype, content):
    """{absolute offset: plain} for every helper read (nested objects flattened), plus consumed."""
    ok, consumed, events, o, fn = tr.trace(ptype, content)
    final = tr.final
    out = {}
    for ev in sorted(events, key=lambda ev: -ev['depth']):
        if not ev['writes'] or ev['off'] <= 0:
            continue
        base = ev['off']
        ws = [(k, w[0], w[1] - base, w[2], w[3]) for k, w in enumerate(ev['writes'])]
        bs = [(p, v) for p, v in ev.get('bswaps', []) if v is not None]
        hr = H.HelperRun(ev.get('ret', 1), 0, ws, bs, final[base:base + H.DST_SIZE] + bytes(H.DST_SIZE))
        for (so, sz), v in H.plain_slots(hr).items():
            out.setdefault((base + so, sz), v)
    return ok, consumed, out


def without_const_plains(node):
    """Constant modes have no reader call in the client trace (the oracle would see their stored bytes),
    so compare reads only: drop the const plain values and let consts decode to {'stored': ...}."""
    if isinstance(node, dict):
        if node.get('kind') == 'const':
            node.pop('plain', None)
            node.pop('extra_plain', None)
        for v in node.values():
            without_const_plains(v)
    elif isinstance(node, list):
        for v in node:
            without_const_plains(v)
    return node


def main(ptype, spec_path, limit=None):
    spec = without_const_plains(json.load(open(spec_path)))
    tr = T.Tracer()
    stats = Counter()
    field_bad = Counter()
    field_n = Counter()
    pk = of(ptype)[:limit] if limit else of(ptype)
    for b in pk:
        try:
            dec = decode_packet(spec, b.content)
        except DecodeError:
            stats['decode_error'] += 1
            continue
        ok, consumed, orc = oracle(tr, ptype, b.content)
        stats['ok'] += 1
        for off, v in dec.items():
            if not isinstance(v, int):
                continue
            cands = [(k, ov) for k, ov in orc.items() if k[0] == off]
            if not cands:
                continue
            field_n[off] += 1
            if not any(ov == v or ov == (v & ((1 << 8 * k[1]) - 1)) for k, ov in cands):
                field_bad[off] += 1
    print(ptype, dict(stats))
    for off in sorted(field_n):
        print(f'  {off:#x}: {field_n[off] - field_bad[off]}/{field_n[off]}')


if __name__ == '__main__':
    main(int(sys.argv[1]), sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else None)
