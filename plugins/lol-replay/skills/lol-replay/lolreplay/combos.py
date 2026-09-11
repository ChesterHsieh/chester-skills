"""Combo analysis: group one champion's spell casts into combos and judge each one.

A combo is a burst of combat casts (Q/W/E/R, summoner spells, item actives) whose consecutive casts
are at most `gap_s` apart. Cast packets give the caster, the spell, the caster position and the aim
point but no decodable target list, so the target is inferred from positions (the enemy champion
nearest the aim points, else nearest the caster). The result comes from the kill packets: an enemy
death around the combo, or the caster's own death, up to OUTCOME_WINDOW_S after the last cast.
Basic attacks are not cast packets, so auto-attack weaving only shows as the pauses between casts."""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from statistics import median
from typing import Callable, Dict, Iterable, Optional, Sequence, Tuple

from . import spells
from .positions import distance
from .timeline import Cast, Event, fmt_clock, merge_key_presses

Pos = Tuple[float, float]
PositionFn = Callable[[int, float], Optional[Pos]]
NameFn = Callable[[int], str]

COMBO_GAP_S = 1.5          # longest pause between two casts of the same combo
MIN_STEPS = 2
ENGAGE_RADIUS = 1200.0     # an enemy champion this close to the caster makes the combo a fight
AIM_RADIUS = 450.0         # an enemy this close to an aim point is taken as that spell's target
AIM_MIN = 50.0             # an aim point this close to the caster is a self-cast
OUTCOME_WINDOW_S = 3.0     # deaths up to this long after the last cast are the combo's result
CORPSE_S = 6.0             # a champion that died this recently is not "near" (its track still shows the death spot)
FLASH_RANGE = 400.0
FLASH_ENEMY_RADIUS = 1500.0
FLASH_MOVE_MIN = 150.0     # change of distance to the nearest enemy that makes a flash offensive / an escape
ABILITY_SLOTS = ("Q", "W", "E", "R")
NON_COMBAT_SCRIPTS = frozenset({"SummonerTeleport", "SummonerTeleportUpgrade", "S12_SummonerTeleportUpgrade"})
FLASH_SCRIPTS = frozenset({"SummonerFlash", "SummonerFlashPerksHextechFlashtraptionV2"})


@dataclass(frozen=True)
class Step:
    cast: Cast
    token: str
    aimed_at: Optional[int]        # enemy champion (player index) within AIM_RADIUS of the aim point

    @property
    def time(self) -> float:
        return self.cast.time


@dataclass(frozen=True)
class FlashUse:
    time: float
    kind: str                      # 進攻閃 / 逃生閃 / 橫向閃 / 附近沒有敵人 / 無位置
    enemy: Optional[int] = None    # nearest enemy champion when flashing
    before: Optional[float] = None
    after: Optional[float] = None

    @property
    def clock(self) -> str:
        return fmt_clock(self.time)


@dataclass(frozen=True)
class Combo:
    player: int
    steps: Tuple[Step, ...]
    enemies_near: Tuple[int, ...]  # enemy champions near the caster or aimed at, closest first
    target: Optional[int]
    kills: Tuple[Event, ...]       # deaths of those enemies (or anyone the caster last-hit) in the window
    died: Optional[Event]          # the caster's own death in the window
    flashes: Tuple[FlashUse, ...] = ()

    @property
    def start(self) -> float:
        return self.steps[0].time

    @property
    def end(self) -> float:
        return self.steps[-1].time

    @property
    def duration(self) -> float:
        return self.end - self.start

    @property
    def clock(self) -> str:
        return fmt_clock(self.start)

    @property
    def is_fight(self) -> bool:
        return bool(self.enemies_near)

    @property
    def tokens(self) -> Tuple[str, ...]:
        return tuple(s.token for s in self.steps)

    @property
    def sequence(self) -> str:
        return " → ".join(compress(self.tokens))

    @property
    def opener(self) -> str:
        return " → ".join(compress(self.tokens)[:2])

    @property
    def uses_r(self) -> bool:
        return any(t.startswith("R") for t in self.tokens)

    @property
    def gaps(self) -> Tuple[float, ...]:
        return tuple(b.time - a.time for a, b in zip(self.steps, self.steps[1:]))

    @property
    def outcome(self) -> str:
        if not self.is_fight:
            return "非交戰"
        if self.kills and self.died:
            return "換命"
        if self.kills:
            return "擊殺"
        return "陣亡" if self.died else "無擊殺"


@dataclass(frozen=True)
class PatternStat:
    text: str
    count: int
    kills: int


@dataclass(frozen=True)
class ComboStats:
    fights: int
    farming: int
    kills: int
    deaths: int
    trades: int
    avg_duration: float
    avg_steps: float
    median_gap: Optional[float]
    with_r: int
    openers: Tuple[PatternStat, ...]
    patterns: Tuple[PatternStat, ...]

    @property
    def kill_rate(self) -> float:
        return self.kills / self.fights if self.fights else 0.0


# ---------------------------------------------------------------- building blocks
def compress(tokens: Sequence[str]) -> Tuple[str, ...]:
    """Collapse repeats: (Q, Q, Q, E) -> (Q×3, E)."""
    runs: list = []
    for t in tokens:
        if runs and runs[-1][0] == t:
            runs[-1] = (t, runs[-1][1] + 1)
        else:
            runs.append((t, 1))
    return tuple(t if n == 1 else f"{t}×{n}" for t, n in runs)


def combat_ref(cast: Cast) -> Optional[spells.SpellRef]:
    """The cast's spell if it belongs in a combo: abilities, summoner spells (not Teleport), item actives."""
    ref = spells.lookup(cast.spell_hash)
    if ref is None:
        return None
    if ref.slot in ABILITY_SLOTS:
        return ref
    if ref.slot == "S" and ref.script not in NON_COMBAT_SCRIPTS:
        return ref
    if ref.slot == "I" and ref.script.endswith("Active"):
        return ref
    return None


def group_casts(casts: Sequence[Cast], gap_s: float = COMBO_GAP_S, min_steps: int = MIN_STEPS) -> Tuple[Tuple[Cast, ...], ...]:
    """Split time-sorted casts into bursts whose consecutive casts are at most `gap_s` apart."""
    groups, current = [], []
    for c in casts:
        if current and c.time - current[-1].time > gap_s:
            groups.append(tuple(current))
            current = []
        current.append(c)
    if current:
        groups.append(tuple(current))
    return tuple(g for g in groups if len(g) >= min_steps)


def aim_point(cast: Cast) -> Optional[Pos]:
    """Where the spell was aimed, or None for a self-cast (aim point on the caster)."""
    if cast.end is None or (cast.start is not None and distance(cast.start, cast.end) < AIM_MIN):
        return None
    return cast.end


def _nearest(origin: Pos, candidates: Sequence[int], t: float, position: PositionFn, radius: float) -> Optional[Tuple[int, float]]:
    best = None
    for e in candidates:
        pos = position(e, t)
        if pos is None:
            continue
        d = distance(origin, pos)
        if d <= radius and (best is None or d < best[1]):
            best = (e, d)
    return best


def aimed_enemy(cast: Cast, enemies: Sequence[int], position: PositionFn, radius: float = AIM_RADIUS) -> Optional[Tuple[int, float]]:
    """(enemy, distance) of the enemy champion nearest the aim point, if one is within `radius`."""
    aim = aim_point(cast)
    return _nearest(aim, enemies, cast.time, position, radius) if aim is not None else None


def living(position: PositionFn, deaths: Sequence[Event]) -> PositionFn:
    """Position lookup that hides champions who died less than CORPSE_S ago."""
    times: Dict[int, list] = {}
    for e in deaths:
        times.setdefault(e.player, []).append(e.time)

    def lookup(player: int, t: float) -> Optional[Pos]:
        if any(0 < t - td < CORPSE_S for td in times.get(player, ())):
            return None
        return position(player, t)
    return lookup


def _enemies(teams: Sequence[int], player: int) -> Tuple[int, ...]:
    return tuple(i for i, team in enumerate(teams) if team != teams[player])


# ---------------------------------------------------------------- flash
def _toward(origin: Pos, aim: Pos, max_dist: float) -> Pos:
    d = distance(origin, aim)
    if d <= max_dist:
        return aim
    f = max_dist / d
    return (origin[0] + (aim[0] - origin[0]) * f, origin[1] + (aim[1] - origin[1]) * f)


def classify_flash(cast: Cast, enemies: Sequence[int], position: PositionFn) -> FlashUse:
    """Offensive / escape / sideways by how the distance to the nearest enemy changes."""
    origin = cast.start or position(cast.player, cast.time)
    if origin is None:
        return FlashUse(cast.time, "無位置")
    near = _nearest(origin, enemies, cast.time, position, FLASH_ENEMY_RADIUS)
    if near is None:
        return FlashUse(cast.time, "附近沒有敵人")
    enemy, before = near
    dest = _toward(origin, cast.end, FLASH_RANGE) if cast.end else origin
    after = distance(dest, position(enemy, cast.time))
    kind = "進攻閃" if after < before - FLASH_MOVE_MIN else "逃生閃" if after > before + FLASH_MOVE_MIN else "橫向閃"
    return FlashUse(cast.time, kind, enemy, before, after)


def player_flashes(casts: Sequence[Cast], player: int, teams: Sequence[int], deaths: Sequence[Event],
                   position: PositionFn) -> Tuple[FlashUse, ...]:
    enemies, alive = _enemies(teams, player), living(position, deaths)
    return tuple(classify_flash(c, enemies, alive) for c in casts
                 if c.player == player and (spells.lookup(c.spell_hash) or spells.SpellRef("", "?", "")).script in FLASH_SCRIPTS)


# ---------------------------------------------------------------- combos
def _enemies_near(group: Sequence[Cast], enemies: Sequence[int], position: PositionFn) -> Dict[int, float]:
    best: Dict[int, float] = {}
    for c in group:
        origin = c.start or position(c.player, c.time)
        if origin is None:
            continue
        for e in enemies:
            pos = position(e, c.time)
            if pos is None:
                continue
            d = distance(origin, pos)
            if d <= ENGAGE_RADIUS and d < best.get(e, float("inf")):
                best[e] = d
    return best


def _combo(group: Tuple[Cast, ...], player: int, enemies: Sequence[int], deaths: Sequence[Event],
           position: PositionFn, flashes: Sequence[FlashUse]) -> Combo:
    steps = tuple(Step(c, combat_ref(c).token, (aimed_enemy(c, enemies, position) or (None,))[0]) for c in group)
    near = _enemies_near(group, enemies, position)
    aimed = Counter(s.aimed_at for s in steps if s.aimed_at is not None)
    involved = tuple(sorted(set(near) | set(aimed), key=lambda e: near.get(e, ENGAGE_RADIUS)))
    target = aimed.most_common(1)[0][0] if aimed else (involved[0] if involved else None)
    start, until = group[0].time, group[-1].time + OUTCOME_WINDOW_S
    kills = tuple(e for e in deaths if e.player in enemies and start <= e.time <= until
                  and (e.player in involved or e.killer == player))
    died = next((e for e in deaths if e.player == player and start <= e.time <= until), None)
    inside = tuple(f for f in flashes if group[0].time <= f.time <= group[-1].time)
    return Combo(player, steps, involved, target, kills, died, inside)


def build_combos(casts: Sequence[Cast], player: int, teams: Sequence[int], deaths: Sequence[Event],
                 position: PositionFn, gap_s: float = COMBO_GAP_S) -> Tuple[Combo, ...]:
    """All combos of `player` (fights and farming bursts) in time order."""
    enemies, alive = _enemies(teams, player), living(position, deaths)
    mine = merge_key_presses(c for c in casts if c.player == player and combat_ref(c) is not None)
    flashes = player_flashes(mine, player, teams, deaths, position)
    return tuple(_combo(g, player, enemies, deaths, alive, flashes) for g in group_casts(mine, gap_s))


def _mean(values: Iterable[float]) -> float:
    values = tuple(values)
    return sum(values) / len(values) if values else 0.0


def _patterns(combos: Sequence[Combo], key: Callable[[Combo], str], top: int) -> Tuple[PatternStat, ...]:
    counts = Counter(key(c) for c in combos)
    kills = Counter(key(c) for c in combos if c.kills)
    return tuple(PatternStat(k, n, kills[k]) for k, n in counts.most_common(top))


def summarize(combos: Sequence[Combo], top: int = 5) -> ComboStats:
    fights = tuple(c for c in combos if c.is_fight)
    gaps = [g for c in fights for g in c.gaps]
    return ComboStats(
        fights=len(fights), farming=len(combos) - len(fights),
        kills=sum(1 for c in fights if c.kills), deaths=sum(1 for c in fights if c.died),
        trades=sum(1 for c in fights if c.kills and c.died),
        avg_duration=_mean(c.duration for c in fights), avg_steps=_mean(len(c.steps) for c in fights),
        median_gap=median(gaps) if gaps else None, with_r=sum(1 for c in fights if c.uses_r),
        openers=_patterns(fights, lambda c: c.opener, top), patterns=_patterns(fights, lambda c: c.sequence, top))


# ---------------------------------------------------------------- text
def summary_line(stats: ComboStats) -> str:
    if not stats.fights:
        return f"交戰連招 0 套（清線／打野連段 {stats.farming} 套）"
    gap = f"、輸入間隔中位數 {stats.median_gap:.2f} 秒" if stats.median_gap is not None else ""
    return (f"交戰連招 {stats.fights} 套（另有清線／打野連段 {stats.farming} 套）：接擊殺 {stats.kills}（{stats.kill_rate:.0%}）、"
            f"打完陣亡 {stats.deaths}（其中換命 {stats.trades}）；平均 {stats.avg_duration:.1f} 秒、{stats.avg_steps:.1f} 個技能{gap}；含 R {stats.with_r} 套")


def pattern_text(items: Sequence[PatternStat], min_count: int = 1) -> str:
    return "｜".join(f"{p.text} ×{p.count}（接擊殺 {p.kills}）" for p in items if p.count >= min_count)


def flash_text(f: FlashUse, name: NameFn) -> str:
    if f.enemy is None:
        return f"閃現 {f.clock} {f.kind}"
    return f"閃現 {f.clock} {f.kind}（與 {name(f.enemy)} 距離 {f.before:.0f}→{f.after:.0f}）"


def outcome_text(combo: Combo, name: NameFn) -> str:
    bits = []
    for e in combo.kills:
        who = "本人最後一擊" if e.killer == combo.player else (f"{name(e.killer)} 收頭" if e.killer is not None else "非英雄收頭")
        bits.append(f"{name(e.player)} {e.clock} 陣亡（{who}）")
    if combo.died is not None:
        by = f"（被 {name(combo.died.killer)} 擊殺）" if combo.died.killer is not None else ""
        bits.append(f"本人 {combo.died.clock} 陣亡{by}")
    return "；".join(bits) or ("無擊殺" if combo.is_fight else "非交戰（附近沒有敵方英雄）")


def describe(combo: Combo, name: NameFn) -> str:
    """One line: time, sequence with offsets, duration, inferred target, enemies nearby, flashes, result."""
    seq = " → ".join(s.token if i == 0 else f"{s.token} +{s.time - combo.start:.1f}" for i, s in enumerate(combo.steps))
    parts = [f"{combo.clock} {seq}", f"{combo.duration:.1f} 秒"]
    if combo.target is not None:
        parts.append(f"目標(推定) {name(combo.target)}")
    if combo.enemies_near:
        parts.append("附近敵人 " + "、".join(name(e) for e in combo.enemies_near))
    parts.extend(flash_text(f, name) for f in combo.flashes)
    parts.append("結果：" + outcome_text(combo, name))
    return "｜".join(parts)
