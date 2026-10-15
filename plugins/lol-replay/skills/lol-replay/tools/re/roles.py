"""Which packet types play the kill / shop / cast / waypoint roles, and which object offsets hold the
fields lolreplay needs — found from a replay's statistics, without the client.

    LOL_REPLAY=game.rofl python3 roles.py types
        candidate packet types per role (run specgen.py on them)
    LOL_REPLAY=game.rofl python3 roles.py fields --specs ../../lolreplay/specs/16.20 \\
        --kill 421 --shop 63 --cast 1215 --waypoint 61
        the "packets" / "waypoint" / "fields" blocks of <patch>.json, with the evidence for each offset
"""
import argparse
import json
import math
import os
import struct
import sys
from collections import Counter, defaultdict

from replay import SKILL, blocks, of  # noqa: F401  (sets sys.path for lolreplay)
from lolreplay import spells
from lolreplay.calibrate import champion_net_ids, count_matrix, detect_profile
from lolreplay.container import read_rofl
from lolreplay.metadata import parse_game
from lolreplay.payload.generic import DecodeError, decode_packet
from lolreplay.payload.spec import PayloadSpec
from lolreplay.payload.waypoints import parse_records

TAG = 0x40000000
SPELL_KEYS = ('SPELL1_CAST', 'SPELL2_CAST', 'SPELL3_CAST', 'SPELL4_CAST', 'SUMMON_SPELL1_CAST', 'SUMMON_SPELL2_CAST')


def _game():
    path = os.environ['LOL_REPLAY']
    r = read_rofl(path)
    return parse_game(r.metadata(), r.game_version)


def _f(v):
    return struct.unpack('<f', struct.pack('<I', v & 0xFFFFFFFF))[0] if isinstance(v, int) else None


def _corr(a, b):
    ma, mb = sum(a) / len(a), sum(b) / len(b)
    num = sum((x - ma) * (y - mb) for x, y in zip(a, b))
    den = math.sqrt(sum((x - ma) ** 2 for x in a) * sum((y - mb) ** 2 for y in b))
    return num / den if den else 0.0


def cmd_types(args):
    game = _game()
    bl = blocks()
    ids = champion_net_ids(bl)
    matrix = count_matrix(bl, ids)
    prof = detect_profile(game, matrix, bl, ids)
    print('death types (kill = the one whose spec has a killer net id):', prof.death_types)
    print('shop type:', prof.shop_type, ' level type:', prof.level_type)
    target = [sum(p.int(k) for k in SPELL_KEYS) for p in game.players]
    casts = sorted(((round(_corr(v, target), 3), t, sum(v)) for t, v in matrix.items() if sum(v) > 50), reverse=True)[:4]
    print('cast candidates (correlation with spell casts, packets):', casts)
    bc = Counter(b.type for b in bl if b.net_id == 0)
    print('waypoint candidates (broadcast types by count):', bc.most_common(6))


def _flatten(values, prefix=()):
    for k, v in values.items():
        yield prefix + (k,), v


def _decoded(spec, ptype):
    out = []
    for b in of(ptype):
        try:
            out.append((b, decode_packet(spec, b.content)))
        except DecodeError:
            pass
    return out


def _vec3s(rows, lo=-1500.0, hi=16500.0):
    """offsets o where (o, o+4, o+8) hold (x, height, z) map coordinates: x and z inside the map and
    away from the origin in most packets, height small. Best spread first."""
    hits = Counter()
    spread = defaultdict(set)
    for _, v in rows:
        for o, x in v.items():
            vals = [v.get(o), v.get(o + 4), v.get(o + 8)]
            if not all(isinstance(t, int) for t in vals):
                continue
            fx, fy, fz = (_f(t) for t in vals)
            if lo < fx < hi and lo < fz < hi and -300 < fy < 900 and abs(fx) > 100 and abs(fz) > 100:
                hits[o] += 1
                spread[o].add((round(fx, -2), round(fz, -2)))
    good = [o for o, n in hits.items() if n > 0.6 * len(rows)]
    return sorted(good, key=lambda o: -len(spread[o]))


def kill_fields(spec, ptype, game, ids):
    rows = _decoded(spec, ptype)
    kills = [p.int('CHAMPIONS_KILLED') for p in game.players]
    best = None
    for o in {o for _, v in rows for o, x in v.items() if isinstance(x, int) and x & TAG}:
        c = Counter(v.get(o) for _, v in rows if isinstance(v.get(o), int))
        vec = [c.get(n, 0) for n in ids]
        d = sum(abs(a - b) for a, b in zip(vec, kills))
        if best is None or d < best[0]:
            best = (d, o, vec)
    print(f'  kill: killer +{best[1]:#x} (kills per champion {best[2]} vs CHAMPIONS_KILLED {kills})')
    resp = Counter()
    for _, v in rows:
        for o, x in v.items():
            f = _f(x)
            if f is not None and 3.0 <= f <= 120.0 and f == round(f * 4) / 4:
                resp[o] += 1
    respawn = resp.most_common(1)[0][0]
    pos = _vec3s(rows)
    print(f'  kill: respawn +{respawn:#x}, position +{pos[0]:#x}/+{pos[0] + 4:#x}/+{pos[0] + 8:#x}')
    return {'respawn': respawn, 'killer': best[1], 'x': pos[0], 'height': pos[0] + 4, 'z': pos[0] + 8}


def shop_fields(spec, ptype):
    rows = _decoded(spec, ptype)
    items = json.load(open(os.path.join(SKILL, 'lolreplay', 'data', 'items.json')))['items']
    ids = Counter()
    for _, v in rows:
        for o, x in v.items():
            if isinstance(x, int) and str(x) in items:
                ids[o] += 1
    item = ids.most_common(1)[0][0]
    price_hits = Counter()
    for _, v in rows:
        it = items.get(str(v.get(item)))
        for o, x in v.items():
            f = _f(x)
            if f is None or o == item:
                continue
            if it and abs(f - it.get('gold', -1)) < 0.5:
                price_hits[o] += 1
    price = price_hits.most_common(1)[0][0]
    distinct = defaultdict(set)
    for _, v in rows:
        for o, x in v.items():
            f = _f(x)
            if f is not None and o not in (price, item) and 0 < f < 100000 and f == round(f):
                distinct[o].add(f)
    gold = max(distinct, key=lambda o: len(distinct[o]))
    print(f'  shop: item +{item:#x} ({ids[item]}/{len(rows)} known item ids), price +{price:#x} '
          f'({price_hits[price]} equal to the item cost), gold after +{gold:#x}')
    return {'gold_after': gold, 'item': item, 'price': price}


def waypoint_field(spec, ptype, center=(7358.0, 7412.0)):
    rows = _decoded(spec, ptype)[:2000]
    ps = PayloadSpec('x', ptype, 0, 0, 0, center)
    good = Counter()
    for _, v in rows:
        for o, x in v.items():
            if isinstance(x, dict) and 'raw' in x:
                raw = bytes.fromhex(x['raw'])
                try:
                    recs, used = parse_records(ps, raw)
                except Exception:
                    continue
                if recs and used == len(raw):
                    good[o] += 1
    o, n = good.most_common(1)[0]
    print(f'  waypoint: blob +{o:#x} ({n}/{len(rows)} blobs parse exactly as waypoint records)')
    return o


def cast_fields(spec, ptype, ids, tracks):
    rows = [(b, v) for b, v in _decoded(spec, ptype) if b.net_id in ids]
    table = spells.hash_table()
    caster = Counter()
    hashes = Counter()
    times = Counter()
    for b, v in rows:
        for o, x in v.items():
            if x == b.net_id:
                caster[o] += 1
            if isinstance(x, int) and x in table:
                hashes[o] += 1
            f = _f(x)
            if f is not None and abs(f - b.time) < 1.0:
                times[o] += 1
    caster_off = caster.most_common(1)[0][0]
    hash_off = hashes.most_common(1)[0][0]
    time_off = times.most_common(1)[0][0]
    # seq: per caster, increases from one cast packet to the next
    inc = Counter()
    last = {}
    for b, v in rows:
        for o, x in v.items():
            if isinstance(x, int) and not x & TAG and o not in (hash_off,):
                k = (b.net_id, o)
                if k in last and x > last[k]:
                    inc[o] += 1
                last[k] = x
    seq = inc.most_common(1)[0][0]
    # start vs end: the caster position is on the caster's waypoint track
    vecs = _vec3s(rows)
    near = Counter()
    for b, v in rows:
        track = tracks.get(ids.index(b.net_id))
        here = track.at(b.time) if track else None
        if here is None:
            continue
        for o in vecs:
            x, z = _f(v.get(o)), _f(v.get(o + 8))
            if x is not None and z is not None and math.hypot(x - here[0], z - here[1]) < 150:
                near[o] += 1
    start = near.most_common(1)[0][0]
    end = next(o for o in vecs if o != start)
    targets = None
    for b, v in rows:
        for o, x in v.items():
            if isinstance(x, dict) and x.get('items'):
                for item in x['items']:
                    for eo, ev in item.items():
                        if isinstance(ev, int) and ev & TAG:
                            targets = {'list': o, 'netid': eo}
                if targets:
                    break
        if targets:
            break
    print(f'  cast: caster +{caster_off:#x} ({caster[caster_off]}/{len(rows)}), spell hash +{hash_off:#x} '
          f'({hashes[hash_off]} known), time +{time_off:#x}, seq +{seq:#x}, start +{start:#x} '
          f'({near[start]} on the caster track), end +{end:#x}, targets {targets}')
    return {'caster': caster_off, 'seq': seq, 'spell_hash': hash_off, 'cast_time': time_off,
            'end': [end, end + 4, end + 8], 'start': [start, start + 4, start + 8], 'targets': targets}


def cmd_fields(args):
    from lolreplay.positions import build_tracks
    game = _game()
    ids = list(champion_net_ids(blocks()))
    load = lambda t: json.load(open(os.path.join(args.specs, f'packet_{t}.json')))  # noqa: E731
    out = {'packets': {'kill': args.kill, 'shop': args.shop, 'cast': args.cast, 'waypoint': args.waypoint}}
    blob = waypoint_field(load(args.waypoint), args.waypoint)
    out['waypoint'] = {'blob_field': blob}
    fields = {'kill': kill_fields(load(args.kill), args.kill, game, ids),
              'shop': shop_fields(load(args.shop), args.shop)}
    patch = os.path.basename(os.path.normpath(args.specs))
    spec_dir = os.path.dirname(os.path.normpath(args.specs))
    ps = PayloadSpec(patch, args.waypoint, 0, 0, 0, (7358.0, 7412.0), out['packets'], {}, blob)
    import lolreplay.payload.spec as S
    S.SPEC_DIR = __import__('pathlib').Path(spec_dir)
    tracks = build_tracks(ps, tuple(blocks()), tuple(ids))
    fields['cast'] = cast_fields(load(args.cast), args.cast, ids, tracks)
    out['fields'] = fields
    print(json.dumps(out, indent=2))


def main(argv):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = ap.add_subparsers(dest='cmd', required=True)
    sub.add_parser('types')
    f = sub.add_parser('fields')
    f.add_argument('--specs', required=True)
    for role in ('kill', 'shop', 'cast', 'waypoint'):
        f.add_argument('--' + role, type=int, required=True)
    args = ap.parse_args(argv)
    return {'types': cmd_types, 'fields': cmd_fields}[args.cmd](args) or 0


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
