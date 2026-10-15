"""Download only the macOS game executable matching a replay's patch from Riot's patch CDN.

    python3 fetch_client.py REPLAY.rofl [--region TW2] [--out DIR]

The replay's version (e.g. 16.20.824.8524) names the build (8248524). Riot's sieve service lists the
current `lol-game-client` release per region and platform; its RMAN manifest maps files to chunks in
bundles, and only the chunks of LeagueofLegends.app/Contents/MacOS/LeagueofLegends are fetched (~80 MB).
Sieve only serves the live patch: run this while the replay's patch is current.
"""
import argparse
import io
import json
import os
import struct
import sys
import urllib.request

import zstandard

SIEVE = 'https://sieve.services.riotcdn.net/api/v1/products/lol/version-sets/{region}?q[platform]=macos'
CDN = 'https://lol.secure.dyn.riotcdn.net/channels/public'
EXE = 'LeagueofLegends.app/Contents/MacOS/LeagueofLegends'
UA = {'User-Agent': 'RiotClient/1.0'}


def fetch(url, rng=None):
    req = urllib.request.Request(url, headers=dict(UA))
    if rng:
        req.add_header('Range', 'bytes=%d-%d' % rng)
    with urllib.request.urlopen(req, timeout=120) as r:
        return r.read()


class _Table:
    """Just enough FlatBuffers to walk an RMAN body."""

    def __init__(self, b, pos):
        self.b, self.pos = b, pos
        vt = pos - struct.unpack_from('<i', b, pos)[0]
        n = struct.unpack_from('<H', b, vt)[0]
        self.offs = [struct.unpack_from('<H', b, vt + 4 + 2 * i)[0] for i in range((n - 4) // 2)]

    def _o(self, i):
        return self.offs[i] if i < len(self.offs) else 0

    def scalar(self, i, fmt):
        o = self._o(i)
        return struct.unpack_from('<' + fmt, self.b, self.pos + o)[0] if o else 0

    def _ref(self, i):
        o = self._o(i)
        if not o:
            return None
        p = self.pos + o
        return p + struct.unpack_from('<I', self.b, p)[0]

    def string(self, i):
        p = self._ref(i)
        if p is None:
            return ''
        n = struct.unpack_from('<I', self.b, p)[0]
        return self.b[p + 4:p + 4 + n].decode()

    def tables(self, i):
        p = self._ref(i)
        if p is None:
            return []
        out = []
        for k in range(struct.unpack_from('<I', self.b, p)[0]):
            e = p + 4 + 4 * k
            out.append(_Table(self.b, e + struct.unpack_from('<I', self.b, e)[0]))
        return out

    def scalars(self, i, fmt):
        p = self._ref(i)
        if p is None:
            return []
        sz = struct.calcsize(fmt)
        return [struct.unpack_from('<' + fmt, self.b, p + 4 + sz * k)[0]
                for k in range(struct.unpack_from('<I', self.b, p)[0])]


def parse_manifest(raw):
    """RMAN -> ({path: (size, [chunk ids])}, {chunk id: (bundle, offset, compressed, uncompressed)})."""
    if raw[:4] != b'RMAN':
        raise ValueError('not an RMAN manifest')
    off, ln = struct.unpack_from('<II', raw, 8)
    body = zstandard.ZstdDecompressor().stream_reader(io.BytesIO(raw[off:off + ln]), read_across_frames=True).read()
    root = _Table(body, struct.unpack_from('<I', body, 0)[0])
    chunks = {}
    for b in root.tables(0):
        bid, o = b.scalar(0, 'Q'), 0
        for c in b.tables(1):
            cs, us = c.scalar(1, 'I'), c.scalar(2, 'I')
            chunks[c.scalar(0, 'Q')] = (bid, o, cs, us)
            o += cs
    dirs = {d.scalar(0, 'Q'): (d.scalar(1, 'Q'), d.string(2)) for d in root.tables(3)}

    def path(did):
        parts = []
        while did and did in dirs:
            did, name = dirs[did]
            if name:
                parts.append(name)
        return '/'.join(reversed(parts))
    files = {}
    for f in root.tables(2):
        p = path(f.scalar(1, 'Q'))
        name = f.string(3)
        files[(p + '/' + name) if p else name] = (f.scalar(2, 'I'), f.scalars(7, 'Q'))
    return files, chunks


def replay_version(path):
    with open(path, 'rb') as fh:
        head = fh.read(64)
    if head[:6] != b'RIOT\x02\x00':
        raise SystemExit('not a ROFL2 replay')
    n = head[14]
    return head[15:15 + n].decode()


def client_release(region, build):
    sets = json.loads(fetch(SIEVE.format(region=region)))
    for r in sets['releases']:
        labels = r['release']['labels']
        if labels.get('riot:artifact_type_id', {}).get('values') == ['lol-game-client']:
            version = labels['riot:artifact_version_id']['values'][0]
            return version, r['download']['url']
    raise SystemExit('no lol-game-client release in the version set')


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('replay')
    ap.add_argument('--region', default='TW2')
    ap.add_argument('--out', default='.')
    args = ap.parse_args(argv)
    version = replay_version(args.replay)            # 16.20.824.8524
    major, minor, a, b = version.split('.')
    build = a + b                                    # 8248524
    live, url = client_release(args.region, build)   # 16.20.8248524+branch...
    if not live.startswith(f'{major}.{minor}.{build}'):
        raise SystemExit(f'live client is {live.split("+")[0]}, replay is {version}: the matching build is no longer served')
    files, chunks = parse_manifest(fetch(url))
    size, ids = files[EXE]
    os.makedirs(args.out, exist_ok=True)
    out = os.path.join(args.out, 'LeagueofLegends')
    dz = zstandard.ZstdDecompressor()
    with open(out, 'wb') as fh:
        for cid in ids:
            bid, o, cs, us = chunks[cid]
            fh.write(dz.decompress(fetch('%s/bundles/%016X.bundle' % (CDN, bid), (o, o + cs - 1)), max_output_size=us))
    got = os.path.getsize(out)
    if got != size:
        raise SystemExit(f'size mismatch: {got} != {size}')
    print(f'{live.split("+")[0]} -> {out} ({got} bytes)')
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
