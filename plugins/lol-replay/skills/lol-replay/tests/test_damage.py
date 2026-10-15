import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolreplay import damage, spells  # noqa: E402
from lolreplay.blocks import Block  # noqa: E402
from lolreplay.timeline import Attack, Damage, build_attacks  # noqa: E402
from lolreplay.calibrate import PacketProfile  # noqa: E402
from lolreplay.timeline import Timeline  # noqa: E402

TALON, CAIT, KASSA = 0, 1, 2
H = spells.elf_hash
ATTACK, CRIT, W_MISSILE, SOUL = H("TalonBasicAttack"), H("TalonCritAttack"), H("TalonWMissileOne"), H("SRX_DragonSoulBuffHextech")


def timeline(dmg=(), attacks=()):
    profile = PacketProfile("t", (), None, None, True)
    return Timeline((), (), (), ((),), (), profile, ("damage", "attacks"), (), tuple(dmg), tuple(attacks))


def hit(t, src, tgt, amount, h):
    return Damage(t, tgt, None, src, None, amount, h)


def test_summary_splits_damage_by_ability_and_counts_attacks():
    tl = timeline(
        [hit(1.0, TALON, KASSA, 100, ATTACK), hit(2.0, TALON, KASSA, 180, CRIT), hit(2.5, TALON, KASSA, 300, W_MISSILE),
         hit(3.0, TALON, None, 40, ATTACK), hit(3.5, TALON, KASSA, 20, SOUL), hit(4.0, KASSA, TALON, 250, None),
         hit(4.5, None, TALON, 90, None)],
        [Attack(0.8, TALON, KASSA, 7), Attack(1.8, TALON, KASSA, 7), Attack(2.9, TALON, None, 99)])
    s = damage.summarize(tl, TALON)
    assert s.total == 600                       # the minion hit is not damage to champions
    assert dict(s.by_slot) == {"W": 300, "A": 280, "?": 20}
    assert (s.attack_hits, s.crits, s.attacks, s.attacks_on_champions) == (2, 1, 3, 2)
    assert s.crit_rate == 0.5 and s.attack_hit_rate == 1.0
    assert s.taken == 340 and s.taken_by == ((KASSA, 250), (None, 90))
    assert s.other_by == (("海克斯龍魂", 20),)
    text = damage.breakdown_text(s, {"W": "迴力匕首"})
    assert text.startswith("W(迴力匕首) 300（50%）、普攻 280（47%）") and "海克斯龍魂 20" in text


def test_window_summary():
    tl = timeline([hit(10.0, TALON, KASSA, 100, ATTACK), hit(50.0, TALON, KASSA, 70, ATTACK)])
    assert damage.summarize(tl, TALON, 0, 20).total == 100


class _Spec:
    def __init__(self):
        self.calls = []

    def packet_type(self, role):
        return 972

    def packet_spec(self, role):
        return {}

    def role_fields(self, role):
        return {"target": 16, "pos": [20, 24, 28]}


def test_attack_resends_and_attack_moves_are_dropped(monkeypatch):
    from lolreplay import timeline as T
    from lolreplay.payload.events import AttackInfo
    seq = iter([AttackInfo(0x40000007, None), AttackInfo(0x40000007, None), AttackInfo(None, (1.0, 2.0)),
                AttackInfo(0x40000007, None), AttackInfo(0x40000008, None)])
    monkeypatch.setattr(T, "decode_attack", lambda spec, content, fields: next(seq))
    blocks = tuple(Block(time=t, type=972, net_id=0x400000AE, content=b"", marker=0, frame_id=0)
                   for t in (1.0, 1.2, 1.3, 2.0, 2.1))
    attacks = build_attacks(blocks, (0x400000AE,), _Spec())
    assert [(a.time, a.target_netid) for a in attacks] == [(1.0, 0x40000007), (2.0, 0x40000007), (2.1, 0x40000008)]
