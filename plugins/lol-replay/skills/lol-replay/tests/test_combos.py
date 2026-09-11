import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolreplay import combos, spells  # noqa: E402
from lolreplay.timeline import Cast, Event, merge_key_presses  # noqa: E402

ZED, ALLY, ENEMY_A, ENEMY_B = 0, 1, 2, 3
TEAMS = (100, 100, 200, 200)
HOME = (1000.0, 1000.0)
POSITIONS = {ZED: HOME, ALLY: (0.0, 0.0), ENEMY_A: (1300.0, 1000.0), ENEMY_B: (9000.0, 9000.0)}


def position(player, t):
    return POSITIONS.get(player)


def spell(champ, slot, suffix=""):
    return spells.elf_hash(spells._champion_spells()[champ][slot]["id"] + suffix)


Q, W, E, R = (spell("Zed", s) for s in "QWER")
FLASH = spells.elf_hash("SummonerFlash")
IGNITE = spells.elf_hash("SummonerDot")


def cast(t, h, start=HOME, end=None, player=ZED):
    return Cast(t, player, h, None, start, end if end is not None else start)


def death(t, victim, killer=None):
    return Event(t, victim, "death", killer=killer)


def test_key_presses_merges_multi_packet_spell():
    casts = [cast(10.0, E), cast(10.0, E), cast(10.15, E), cast(10.5, E), cast(10.5, Q)]
    assert [(c.time, c.spell_hash) for c in merge_key_presses(casts)] == [(10.0, E), (10.5, E), (10.5, Q)]


def test_group_casts_splits_on_gap_and_drops_single_casts():
    casts = [cast(t, Q) for t in (1.0, 1.5, 2.9, 10.0, 20.0, 20.4)]
    groups = combos.group_casts(casts, gap_s=1.5)
    assert [[c.time for c in g] for g in groups] == [[1.0, 1.5, 2.9], [20.0, 20.4]]


def test_combat_ref_keeps_abilities_summoners_and_item_actives_only():
    item_active = spells.elf_hash(next(iter(spells._items())) + "Active")
    assert combats([Q, R, FLASH, IGNITE, item_active]) == [True] * 5
    utility = [spells.elf_hash(n) for n in ("Recall", "TrinketTotemLvl1", "SummonerTeleport", "ItemCrystalFlask")]
    assert combats(utility) == [False] * 4


def combats(hashes):
    return [combos.combat_ref(cast(0.0, h)) is not None for h in hashes]


def test_fight_combo_with_kill_infers_target_from_aim_point():
    casts = [cast(100.0, E), cast(100.5, Q, end=POSITIONS[ENEMY_A]), cast(101.0, R, end=POSITIONS[ENEMY_A])]
    (c,) = combos.build_combos(casts, ZED, TEAMS, [death(102.0, ENEMY_A, killer=ZED)], position)
    assert c.tokens == ("E", "Q", "R")
    assert c.is_fight and c.enemies_near == (ENEMY_A,)
    assert c.target == ENEMY_A
    assert c.outcome == "擊殺" and c.kills[0].player == ENEMY_A
    assert c.uses_r and c.duration == 1.0


def test_burst_without_enemies_is_farming():
    far = (5000.0, 5000.0)
    casts = [cast(50.0, Q, start=far, end=(5300.0, 5000.0)), cast(50.6, E, start=far)]
    (c,) = combos.build_combos(casts, ZED, TEAMS, [], position)
    assert not c.is_fight and c.outcome == "非交戰" and c.target is None
    stats = combos.summarize([c])
    assert (stats.fights, stats.farming) == (0, 1)


def test_own_death_after_combo_is_death_or_trade():
    casts = [cast(200.0, Q, end=POSITIONS[ENEMY_A]), cast(200.4, E)]
    (died,) = combos.build_combos(casts, ZED, TEAMS, [death(202.0, ZED, killer=ENEMY_A)], position)
    assert died.outcome == "陣亡"
    both = [death(201.0, ENEMY_A, killer=ZED), death(202.0, ZED, killer=ENEMY_B)]
    (trade,) = combos.build_combos(casts, ZED, TEAMS, both, position)
    assert trade.outcome == "換命"


def test_recent_corpse_is_not_a_nearby_enemy():
    casts = [cast(298.0, Q, end=POSITIONS[ENEMY_A]), cast(298.5, E)]
    (c,) = combos.build_combos(casts, ZED, TEAMS, [death(295.0, ENEMY_A)], position)
    assert not c.is_fight


def test_flash_direction_relative_to_nearest_enemy():
    toward = cast(10.0, FLASH, end=(1500.0, 1000.0))   # lands at 1400: 300 -> 100 from enemy A
    away = cast(20.0, FLASH, end=(500.0, 1000.0))       # lands at 600: 300 -> 700
    flashes = combos.player_flashes([toward, away], ZED, TEAMS, [], position)
    assert [f.kind for f in flashes] == ["進攻閃", "逃生閃"]
    assert flashes[0].enemy == ENEMY_A and round(flashes[0].after) == 100


def test_compress_collapses_repeats():
    assert combos.compress(("Q", "Q", "Q", "E", "Q")) == ("Q×3", "E", "Q")


def test_tokens_for_recasts_and_evolved_spells():
    assert spells.lookup(spells.elf_hash("ZedW2")).token == "W2"
    evolved = spells.lookup(spells.elf_hash("KhazixQLong"))
    assert evolved.slot == "Q" and evolved.token == "Q"
    assert spells.lookup(spells.elf_hash("ItemGhostWard")).slot == "I"


def test_summarize_counts_openers_and_kill_conversion():
    kill = [death(12.0, ENEMY_A, killer=ZED)]
    fights = [
        combos.build_combos([cast(10.0, E), cast(10.5, Q, end=POSITIONS[ENEMY_A])], ZED, TEAMS, kill, position)[0],
        combos.build_combos([cast(30.0, E), cast(30.4, Q, end=POSITIONS[ENEMY_A])], ZED, TEAMS, [], position)[0],
        combos.build_combos([cast(50.0, W), cast(50.2, E)], ZED, TEAMS, [], position)[0],
    ]
    stats = combos.summarize(fights)
    assert stats.fights == 3 and stats.kills == 1
    assert stats.openers[0] == combos.PatternStat("E → Q", 2, 1)
    assert "接擊殺 1（33%）" in combos.summary_line(stats)
