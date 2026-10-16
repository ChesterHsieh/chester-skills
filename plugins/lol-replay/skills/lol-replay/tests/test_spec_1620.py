"""Regression: real 16.20 packet contents (TW2-454798823) decode to the values the client produces."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolreplay import spells  # noqa: E402
from lolreplay.payload.blob import decode_waypoint_packet  # noqa: E402
from lolreplay.payload.events import decode_attack, decode_cast, decode_damage, decode_kill, decode_purchase  # noqa: E402
from lolreplay.payload.spec import load_spec  # noqa: E402
from lolreplay.payload.waypoints import parse_records  # noqa: E402

SPEC = load_spec("16.20")
CAITLYN_BLUE, SHACO, BRIAR, CAITLYN_RED, MALPHITE = 0x400000B1, 0x400000AF, 0x400000B4, 0x400000B6, 0x400000B7

KILL = bytes.fromhex("4050187e035ebd2d87ebbc044ae920167436cb45aa4d775582adbf77aacda54076766e4e")
SHOP = bytes.fromhex("a649497bd9a59378fbb50dd19585")
CAST = bytes.fromhex(
    "d28d5be9770893150c42e04cf8ca489f3f0abfcc0a61287d8c34ba34346b3d479b2901394d5a71972629969c3e2bb060122cea4f"
    "fb3157578204315c0e0e60efaaa751ef83e8f0ecececec406ddc8ace203e0bd248a563e269a93656cef331c17522e3ee9f63022b"
    "c0017af7647c5a49ef363f3d6df68a3e3e693fe8399559b9766677e9fd8c93")
WAYPOINT = bytes.fromhex("2c42c080798080888009900b05221d22693f")
DAMAGE = bytes.fromhex("c533b3432308b66b1fd3ff2fed8215c84a")   # 3:50 Briar crit on Shaco
ATTACK = bytes.fromhex("c2b4b8dcdc90e1f432c11e156e6dfd")       # 0:35 red Caitlyn starts an attack on blue Caitlyn


def test_patch_roles():
    assert SPEC is not None
    assert {r: SPEC.packet_type(r) for r in ("kill", "shop", "cast", "waypoint")} == \
        {"kill": 421, "shop": 63, "cast": 1215, "waypoint": 61}
    assert SPEC.packet_type("damage") == 150 and SPEC.packet_type("attack") == 972


def test_kill_packet():
    info = decode_kill(SPEC.packet_spec("kill"), KILL, SPEC.role_fields("kill"))
    assert info.killer_netid == CAITLYN_RED
    assert info.respawn == 12.0
    assert round(info.pos[0]) == 13122 and round(info.pos[1]) == 2248


def test_shop_packet():
    info = decode_purchase(SPEC.packet_spec("shop"), SHOP, SPEC.role_fields("shop"))
    assert (info.item, info.price, info.gold_after) == (1038, 1300.0, 100.0)  # B. F. Sword


def test_cast_packet_with_target_list():
    info = decode_cast(SPEC.packet_spec("cast"), CAST, SPEC.role_fields("cast"))
    assert info.caster_netid == MALPHITE
    ref = spells.lookup(info.spell_hash)
    assert (ref.champion, ref.slot) == ("Malphite", "E")
    assert info.seq == 52
    assert abs(info.cast_time - 36.307) < 0.01
    assert info.targets == (MALPHITE, CAITLYN_BLUE)   # Ground Slam hits the caster's area: self + blue Caitlyn
    assert round(info.start[0]) == 12897


def test_waypoint_blob_from_packet_spec():
    records, used = parse_records(SPEC, decode_waypoint_packet(SPEC, WAYPOINT))
    assert [r.net_id for r in records] == [MALPHITE]
    assert records[0].points == ((14054.0, 14492.0),)   # red fountain


def test_damage_packet_names_dealer_and_script():
    info = decode_damage(SPEC.packet_spec("damage"), DAMAGE, SPEC.role_fields("damage"))
    assert (info.target_netid, info.source_netid) == (SHACO, BRIAR)
    assert round(info.amount, 1) == 164.8
    ref = spells.lookup(info.script_hash)
    assert (ref.champion, ref.slot, ref.script) == ("Briar", "A", "BriarCritAttack")


def test_attack_packet_target_and_position():
    info = decode_attack(SPEC.packet_spec("attack"), ATTACK, SPEC.role_fields("attack"))
    assert info.target_netid == CAITLYN_BLUE
    assert round(info.target_pos[0]) == 12689 and round(info.target_pos[1]) == 1675


def test_champion_sub_scripts_resolve_to_their_ability():
    ref = spells.lookup(spells.elf_hash("TalonWMissileOne"))
    assert (ref.champion, ref.slot) == ("Talon", "W")
    assert spells.lookup(spells.elf_hash("SRX_DragonSoulBuffHextech")).zh == "海克斯龍魂"
