import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolreplay.blocks import Block  # noqa: E402
from lolreplay.calibrate import (_closest_match, count_matrix, detect_profile,  # noqa: E402
                                 level_code_byte)
from lolreplay.timeline import _events, _number_levels  # noqa: E402

A, B = 0x400000AE, 0x400000AF
IDS = (A, B)
LEVEL, DEATH = 969, 5


def block(t, ptype, net, content):
    return Block(time=t, type=ptype, net_id=net, content=content, marker=0, frame_id=0)


class _Player:
    def __init__(self, stats):
        self.stats = stats

    def int(self, key):
        return self.stats.get(key, 0)


class _Game:
    patch = "test"

    def __init__(self, *stats):
        self.players = tuple(_Player(s) for s in stats)


# 16.20 shape: the last byte encodes the level, the first byte changes on its own;
# player A gets the L4 packet twice with a different first byte.
BLOCKS = (
    block(0, LEVEL, A, b"\x81"), block(10, LEVEL, A, b"\x98\xa3"), block(20, LEVEL, A, b"\x9d\x21"),
    block(25, LEVEL, A, b"\x98\x21"),
    block(0, LEVEL, B, b"\x81"), block(12, LEVEL, B, b"\x9e\xa3"), block(22, LEVEL, B, b"\x9f\x21"),
    block(30, DEATH, A, b"\x00"), block(31, DEATH, B, b"\x00"),
)
GAME = _Game({"LEVEL": 4, "NUM_DEATHS": 1}, {"LEVEL": 4, "NUM_DEATHS": 1})


def test_level_code_byte_is_the_byte_every_player_walks_in_order():
    assert level_code_byte(BLOCKS, LEVEL, IDS) == -1


def test_level_type_with_a_resend_is_detected_through_the_code_byte():
    matrix = count_matrix(BLOCKS, IDS)
    assert matrix[LEVEL] == (4, 3)
    assert detect_profile(GAME, matrix).level_type is None
    profile = detect_profile(GAME, matrix, BLOCKS, IDS)
    assert (profile.level_type, profile.level_code_byte) == (LEVEL, -1)
    assert profile.death_types == (DEATH,)


def test_resent_level_is_not_numbered_twice():
    levels = _number_levels(_events(BLOCKS, IDS, LEVEL, "level", code_byte=-1))
    assert [(e.time, e.value) for e in levels if e.player == 0] == [(0, 2), (10, 3), (20, 4)]


def test_shop_tolerance_scales_with_purchase_count():
    target = (27, 34, 29, 21)
    matrix = {63: (27, 29, 24, 21), 437: (33, 30, 44, 26)}
    assert _closest_match(matrix, target, 4) is None
    assert _closest_match(matrix, target, 4, 0.2) == 63
