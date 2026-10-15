"""Mutated contents: whatever the client accepts, the Python decoder must accept with the same values.

    LOL_CLIENT=... LOL_REPLAY=game.rofl python3 fuzz.py TYPE packet_TYPE.json [N]

Flag bytes of real packets are randomized, so modes the sample replay never uses get exercised."""
import json, random, sys
from collections import Counter
import calltrace as T
import validate as V
from replay import of
from lolreplay.payload.generic import DecodeError, decode_packet


def main(ptype, spec_path, n=300, seed=1):
    spec = json.load(open(spec_path))
    tr = T.Tracer()
    rnd = random.Random(seed)
    real = of(ptype)
    st = Counter()
    bad_fields = Counter()
    for i in range(n):
        # mutate a real packet: random flag bytes, real-ish payload
        b = rnd.choice(real).content
        data = bytearray(b) + bytes(rnd.randrange(256) for _ in range(64))
        k = rnd.randrange(1, 4)
        for _ in range(k):
            data[rnd.randrange(min(len(b), 16))] = rnd.randrange(256)
        data = bytes(data)
        try:
            ok, consumed, orc = V.oracle(tr, ptype, data)
        except Exception:
            st['oracle_crash'] += 1
            continue
        if ok != 1:
            st['client_rejects'] += 1
            continue
        content = data[:consumed]
        try:
            dec = decode_packet(spec, content)
        except DecodeError as exc:
            st['decode_error'] += 1
            if st['decode_error'] <= 3:
                print('  err', exc, content.hex())
            continue
        st['decoded'] += 1
        mism = 0
        for off, v in dec.items():
            if not isinstance(v, int):
                continue
            cands = [(kk, ov) for kk, ov in orc.items() if kk[0] == off]
            if cands and not any(ov == v or ov == (v & ((1 << 8 * kk[1]) - 1)) for kk, ov in cands):
                bad_fields[off] += 1
                mism += 1
        st['value_mismatch_pkts' if mism else 'exact'] += 1
    print(ptype, dict(st), {hex(k): v for k, v in bad_fields.most_common(8)})


if __name__ == '__main__':
    main(int(sys.argv[1]), sys.argv[2], int(sys.argv[3]) if len(sys.argv) > 3 else 300)
