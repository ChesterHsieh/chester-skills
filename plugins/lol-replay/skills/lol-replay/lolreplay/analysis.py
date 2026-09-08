"""Derived per-player metrics, comparisons and heuristic flags.

Thresholds are deliberately relative (vs lane opponent, vs the rest of the lobby) where
gold/damage scales shift between seasons; only CS/min and time-dead use absolute numbers.
"""
from __future__ import annotations

from dataclasses import dataclass
from statistics import mean, median
from typing import Optional, Tuple

from . import ddragon
from .metadata import Game, Player
from .timeline import Timeline, fmt_clock

CARRY_ROLES = ("TOP", "MIDDLE", "BOTTOM")
SHORT_GAME_MIN = 20
LOW_DMG_SHARE = 0.16
LOW_CS_PER_MIN = {"BOTTOM": 6.0, "MIDDLE": 6.0, "TOP": 5.5, "JUNGLE": 4.5}
HIGH_TIME_DEAD_PCT = 12.0
MANY_DEATHS = 6
LOW_KP = 0.40
OPPONENT_RATIO = 0.6
GOLD_BEHIND_RATIO = 0.8
LOBBY_EFFICIENCY_RATIO = 0.7
LATE_LEVEL6_S = 60
LOW_ACTIVITY_RATIO = 0.5
DISCONNECT_S = 30
UNSPENT_GOLD = 2000
EARLY_GAME_S = 600


@dataclass(frozen=True)
class Metrics:
    index: int
    champion: str
    name: str
    team: int
    position: str
    win: bool
    level: int
    kills: int
    deaths: int
    assists: int
    kda: float
    kill_participation: float
    cs: int
    cs_per_min: float
    gold: int
    gold_per_min: float
    gold_spent: int
    dmg_champs: int
    dmg_per_min: float
    dmg_share: float
    dmg_per_1k_gold: float
    dmg_total: int
    dmg_objectives: int
    dmg_turrets: int
    dmg_taken: int
    dmg_taken_champs: int
    dmg_mitigated: int
    dmg_physical: int
    dmg_magic: int
    dmg_true: int
    time_dead_s: int
    time_dead_pct: float
    vision_score: int
    wards_placed: int
    wards_killed: int
    control_wards: int
    spell_casts: int
    summoner_casts: int
    casts_per_min: float
    largest_crit: int
    largest_multi_kill: int
    legendary_items: int
    items: Tuple[int, ...]
    keystone: int
    primary_style: int
    sub_style: int
    runes: Tuple[int, ...]
    stat_perks: Tuple[int, ...]
    summoner_spells: Tuple[int, ...]
    pings: int
    was_afk: bool
    disconnected_s: int
    cc_time_s: int
    heal: int
    shield_on_team: int
    spell_cast_split: Tuple[int, int, int, int]
    summoner_cast_split: Tuple[int, int]
    unspent_gold: int
    surrendered: bool


@dataclass(frozen=True)
class Flag:
    severity: str
    code: str
    message: str


def _pings(p: Player) -> int:
    return sum(p.int(k) for k in p.stats if k.endswith("_PINGS"))


def compute_metrics(game: Game) -> Tuple[Metrics, ...]:
    minutes = max(game.minutes, 1e-6)
    team_kills = {t: sum(p.int("CHAMPIONS_KILLED") for p in game.team(t)) for t in (100, 200)}
    team_dmg = {t: sum(p.int("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS") for p in game.team(t)) for t in (100, 200)}
    return tuple(_metrics_for(p, minutes, team_kills, team_dmg) for p in game.players)


def _metrics_for(p: Player, minutes: float, team_kills: dict, team_dmg: dict) -> Metrics:
    kills, deaths, assists = p.int("CHAMPIONS_KILLED"), p.int("NUM_DEATHS"), p.int("ASSISTS")
    cs = p.int("MINIONS_KILLED") + p.int("NEUTRAL_MINIONS_KILLED")
    gold = p.int("GOLD_EARNED")
    dmg = p.int("TOTAL_DAMAGE_DEALT_TO_CHAMPIONS")
    played = p.int("TIME_PLAYED") or int(minutes * 60)
    dead = p.int("TOTAL_TIME_SPENT_DEAD")
    casts = sum(p.int(f"SPELL{i}_CAST") for i in range(1, 5))
    tk = team_kills.get(p.team, 0)
    td = team_dmg.get(p.team, 0)
    return Metrics(
        index=p.index, champion=p.champion, name=p.name, team=p.team, position=p.position, win=p.win,
        level=p.int("LEVEL"), kills=kills, deaths=deaths, assists=assists,
        kda=round((kills + assists) / max(deaths, 1), 2),
        kill_participation=round((kills + assists) / tk, 3) if tk else 0.0,
        cs=cs, cs_per_min=round(cs / minutes, 2),
        gold=gold, gold_per_min=round(gold / minutes, 1), gold_spent=p.int("GOLD_SPENT"),
        dmg_champs=dmg, dmg_per_min=round(dmg / minutes, 1), dmg_share=round(dmg / td, 3) if td else 0.0,
        dmg_per_1k_gold=round(dmg / gold * 1000, 1) if gold else 0.0,
        dmg_total=p.int("TOTAL_DAMAGE_DEALT"), dmg_objectives=p.int("TOTAL_DAMAGE_DEALT_TO_OBJECTIVES"),
        dmg_turrets=p.int("TOTAL_DAMAGE_DEALT_TO_TURRETS"), dmg_taken=p.int("TOTAL_DAMAGE_TAKEN"),
        dmg_taken_champs=p.int("TOTAL_DAMAGE_TAKEN_FROM_CHAMPIONS"),
        dmg_mitigated=p.int("TOTAL_DAMAGE_SELF_MITIGATED"),
        dmg_physical=p.int("PHYSICAL_DAMAGE_DEALT_TO_CHAMPIONS"), dmg_magic=p.int("MAGIC_DAMAGE_DEALT_TO_CHAMPIONS"),
        dmg_true=p.int("TRUE_DAMAGE_DEALT_TO_CHAMPIONS"),
        time_dead_s=dead, time_dead_pct=round(100.0 * dead / played, 1) if played else 0.0,
        vision_score=p.int("VISION_SCORE"), wards_placed=p.int("WARD_PLACED"), wards_killed=p.int("WARD_KILLED"),
        control_wards=p.int("VISION_WARDS_BOUGHT_IN_GAME"),
        spell_casts=casts, summoner_casts=p.int("SUMMON_SPELL1_CAST") + p.int("SUMMON_SPELL2_CAST"),
        casts_per_min=round(casts / minutes, 1),
        largest_crit=p.int("LARGEST_CRITICAL_STRIKE"), largest_multi_kill=p.int("LARGEST_MULTI_KILL"),
        legendary_items=p.int("Missions_LegendaryItems"), items=tuple(p.int(f"ITEM{i}") for i in range(7)),
        keystone=p.int("KEYSTONE_ID"), primary_style=p.int("PERK_PRIMARY_STYLE"), sub_style=p.int("PERK_SUB_STYLE"),
        runes=tuple(p.int(f"PERK{i}") for i in range(6)), stat_perks=tuple(p.int(f"STAT_PERK_{i}") for i in range(3)),
        summoner_spells=(p.int("SUMMONER_SPELL_1"), p.int("SUMMONER_SPELL_2")),
        pings=_pings(p), was_afk=p.int("WAS_AFK") > 0, disconnected_s=p.int("TIME_SPENT_DISCONNECTED"),
        cc_time_s=p.int("TIME_CCING_OTHERS"), heal=p.int("TOTAL_HEAL"),
        shield_on_team=p.int("TOTAL_DAMAGE_SHIELDED_ON_TEAMMATES"),
        spell_cast_split=tuple(p.int(f"SPELL{i}_CAST") for i in range(1, 5)),
        summoner_cast_split=(p.int("SUMMON_SPELL1_CAST"), p.int("SUMMON_SPELL2_CAST")),
        unspent_gold=max(0, gold - p.int("GOLD_SPENT")),
        surrendered=bool(p.int("GAME_ENDED_IN_SURRENDER") or p.int("GAME_ENDED_IN_EARLY_SURRENDER")),
    )


def opponent_of(metrics: Tuple[Metrics, ...], m: Metrics) -> Optional[Metrics]:
    return next((o for o in metrics if o.team != m.team and o.position == m.position), None)


def team_of(metrics: Tuple[Metrics, ...], team: int) -> Tuple[Metrics, ...]:
    return tuple(m for m in metrics if m.team == team)


def average(values) -> float:
    values = list(values)
    return round(mean(values), 1) if values else 0.0


def flags_for(game: Game, metrics: Tuple[Metrics, ...], m: Metrics,
              timeline: Optional[Timeline] = None) -> Tuple[Flag, ...]:
    opp = opponent_of(metrics, m)
    flags = []
    flags.extend(_context_flags(game, m, timeline))
    flags.extend(_economy_flags(m, opp))
    flags.extend(_damage_flags(metrics, m, opp))
    flags.extend(_death_flags(m, timeline))
    flags.extend(_build_flags(m, opp))
    if timeline is not None:
        flags.extend(_timeline_flags(m, opp, timeline))
    order = {"high": 0, "medium": 1, "low": 2, "info": 3}
    return tuple(sorted(flags, key=lambda f: order.get(f.severity, 9)))


def _context_flags(game: Game, m: Metrics, timeline: Optional[Timeline]) -> list:
    out = []
    accelerated = timeline is not None and timeline.accelerated
    if accelerated:
        lvl = timeline.starting_level(m.index)
        out.append(Flag("info", "accelerated_mode",
                        f"開場即 L{lvl}、金錢與經驗明顯加速（Swiftplay 類快速模式）：CS/分之類的絕對門檻是以一般召喚峽谷為準，請以對位差距與全場平均為主。"))
    if game.minutes < SHORT_GAME_MIN:
        surrender = any(p.int("GAME_ENDED_IN_SURRENDER") or p.int("GAME_ENDED_IN_EARLY_SURRENDER") for p in game.players)
        how = "投降結束" if surrender else ("快速模式正常長度" if accelerated else "未偵測到投降旗標")
        out.append(Flag("info", "short_game",
                        f"比賽只打了 {game.duration_label}（{how}），所有人的總傷害都會偏低；比較時請看每分鐘傷害與傷害佔比，而不是總量。"))
    if m.was_afk or m.disconnected_s > DISCONNECT_S:
        out.append(Flag("high", "afk", f"曾經掛機／斷線（離線 {m.disconnected_s} 秒），這會直接壓低所有數據。"))
    return out


def _economy_flags(m: Metrics, opp: Optional[Metrics]) -> list:
    out = []
    threshold = LOW_CS_PER_MIN.get(m.position)
    if threshold and m.cs_per_min < threshold:
        out.append(Flag("high", "low_cs",
                        f"補刀效率低：每分鐘 {m.cs_per_min} 隻（{m.position} 位期望至少 {threshold}），總共 {m.cs} 隻。裝備成型慢，輸出自然出不來。"))
    if opp and opp.gold and m.gold < opp.gold * GOLD_BEHIND_RATIO:
        out.append(Flag("high", "gold_behind_lane",
                        f"對位經濟差距：{m.gold} vs {opp.gold}（{opp.champion}），落後 {opp.gold - m.gold} 金（{1 - m.gold / opp.gold:.0%}）。"))
    return out


def _damage_flags(metrics: Tuple[Metrics, ...], m: Metrics, opp: Optional[Metrics]) -> list:
    out = []
    if m.position in CARRY_ROLES and m.dmg_share < LOW_DMG_SHARE:
        out.append(Flag("high", "low_dmg_share", f"隊伍傷害佔比只有 {m.dmg_share:.0%}，carry 位通常要 20% 以上。"))
    if opp and opp.dmg_per_min and m.dmg_per_min < opp.dmg_per_min * OPPONENT_RATIO:
        out.append(Flag("high", "dmg_behind_lane",
                        f"每分鐘對英雄傷害 {m.dmg_per_min}，只有對位 {opp.champion}（{opp.dmg_per_min}）的 {m.dmg_per_min / opp.dmg_per_min:.0%}。"))
    lobby_eff = average(x.dmg_per_1k_gold for x in metrics)
    if opp and opp.dmg_per_1k_gold and m.dmg_per_1k_gold < opp.dmg_per_1k_gold * OPPONENT_RATIO:
        out.append(Flag("medium", "low_dmg_efficiency",
                        f"每 1000 金轉換的傷害 {m.dmg_per_1k_gold}，對位是 {opp.dmg_per_1k_gold}：不只是缺錢，有錢也沒打出傷害（站位／參戰／目標選擇）。"))
    elif m.position in CARRY_ROLES and lobby_eff and m.dmg_per_1k_gold < lobby_eff * LOBBY_EFFICIENCY_RATIO:
        out.append(Flag("medium", "low_dmg_efficiency",
                        f"每 1000 金轉換的傷害 {m.dmg_per_1k_gold}，全場平均 {lobby_eff}：經濟有到位但沒轉成傷害（參戰少、打不到人或打在建築上）。"))
    if opp and opp.casts_per_min and m.casts_per_min < opp.casts_per_min * OPPONENT_RATIO and m.position != "BOTTOM":
        out.append(Flag("medium", "low_casts", f"技能施放每分鐘 {m.casts_per_min} 次，對位 {opp.casts_per_min} 次：交戰參與或操作量偏低。"))
    if m.kill_participation and m.kill_participation < LOW_KP:
        out.append(Flag("medium", "low_kp", f"擊殺參與率 {m.kill_participation:.0%}，隊伍多數擊殺發生時不在場。"))
    if m.position in CARRY_ROLES and m.dmg_taken_champs > m.dmg_champs * 1.2:
        out.append(Flag("medium", "focused",
                        f"承受英雄傷害 {m.dmg_taken_champs} 高於造成的 {m.dmg_champs}：常被集火或站位過前，還沒輸出就先倒。"))
    return out


def _death_flags(m: Metrics, timeline: Optional[Timeline]) -> list:
    out = []
    if m.deaths >= MANY_DEATHS:
        out.append(Flag("high", "many_deaths", f"死亡 {m.deaths} 次，死亡時間合計 {m.time_dead_s} 秒（{m.time_dead_pct}% 的比賽時間）。"))
    elif m.time_dead_pct > HIGH_TIME_DEAD_PCT:
        out.append(Flag("medium", "time_dead", f"死亡時間佔比 {m.time_dead_pct}%，偏高。"))
    if timeline is not None:
        early = [e for e in timeline.deaths_of(m.index) if e.time < EARLY_GAME_S]
        if len(early) >= 3:
            out.append(Flag("high", "early_deaths",
                            f"10 分鐘前就死了 {len(early)} 次（{', '.join(e.clock for e in early)}），對線期直接崩。"))
        elif len(early) == 2:
            out.append(Flag("medium", "early_deaths",
                            f"10 分鐘前死了 2 次（{', '.join(e.clock for e in early)}），對線期節奏被打斷。"))
    return out


def _build_flags(m: Metrics, opp: Optional[Metrics]) -> list:
    out = []
    completed = [i for i in m.items if ddragon.is_legendary(i)]
    if m.position in CARRY_ROLES and not completed:
        out.append(Flag("high", "no_legendary", "終局裝備中沒有任何一件成型大件，傷害曲線根本沒起來。"))
    if opp and m.legendary_items + 1 < opp.legendary_items:
        out.append(Flag("medium", "fewer_legendary", f"成型大件 {m.legendary_items} 件，對位 {opp.champion} 有 {opp.legendary_items} 件。"))
    if m.unspent_gold >= UNSPENT_GOLD:
        out.append(Flag("medium", "unspent_gold", f"終局還有 {m.unspent_gold} 金沒花：接近一件大件的錢躺在口袋裡，等於少一件裝備在打團。"))
    if m.gold > 6000 and not any(ddragon.is_boots(i) for i in m.items):
        out.append(Flag("low", "no_boots", "終局沒有鞋子。"))
    if m.items[6] and m.items[6] not in ddragon.TRINKET_IDS:
        out.append(Flag("low", "trinket", f"飾品欄是 {ddragon.item_name(m.items[6])}，不是一般偵查飾品。"))
    return out


def _timeline_flags(m: Metrics, opp: Optional[Metrics], tl: Timeline) -> list:
    out = []
    if opp is not None:
        mine, theirs = tl.level_time(m.index, 6), tl.level_time(opp.index, 6)
        if mine is not None and theirs is not None and mine - theirs > LATE_LEVEL6_S:
            out.append(Flag("medium", "late_level6",
                            f"6 等時間 {fmt_clock(mine)}，比對位 {opp.champion}（{fmt_clock(theirs)}）晚了 {int(mine - theirs)} 秒。"))
    row = tl.activity[m.index] if m.index < len(tl.activity) else ()
    others = [sum(r) for i, r in enumerate(tl.activity) if i != m.index]
    if row and others and sum(row) < median(others) * LOW_ACTIVITY_RATIO:
        out.append(Flag("medium", "low_activity",
                        f"操作量偏低：整場網路事件數 {sum(row)}，其他人中位數 {int(median(others))}（可能長時間閒置／掛機／死亡）。"))
    return out
