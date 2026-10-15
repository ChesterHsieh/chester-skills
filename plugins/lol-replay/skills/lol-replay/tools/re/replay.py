"""Packet blocks of a sample replay ($LOL_REPLAY) for validation, cached next to the replay."""
import os
import pickle
import sys
from collections import namedtuple

SKILL = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, SKILL)

B = namedtuple('B', 'time type net_id content')
_cache = {}


def blocks(path=None):
    path = path or os.environ.get('LOL_REPLAY', '')
    if not path:
        raise SystemExit('set LOL_REPLAY to a replay of the same patch as the client')
    if path in _cache:
        return _cache[path]
    pk = path + '.blocks.pkl'
    if os.path.exists(pk):
        out = [B(*x) for x in pickle.load(open(pk, 'rb'))]
    else:
        from lolreplay.blocks import game_blocks
        from lolreplay.container import read_rofl
        out = [B(b.time, b.type, b.net_id, bytes(b.content)) for b in game_blocks(read_rofl(path))]
        pickle.dump([tuple(b) for b in out], open(pk, 'wb'))
    _cache[path] = out
    return out


def of(ptype, path=None):
    return [b for b in blocks(path) if b.type == ptype]
