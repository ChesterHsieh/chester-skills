"""Markdown and JSON rendering of summaries, player deep-dives and timelines."""
from __future__ import annotations

import json
from dataclasses import asdict
from typing import Optional, Sequence, Tuple

from . import ddragon
from .analysis import Flag, Metrics, average, opponent_of, team_of
from .metadata import Game
from .positions import region_name
from .timeline import Timeline, fmt_clock

POS_ZH = {"TOP": "上路", "JUNGLE": "打野", "MIDDLE": "中路", "BOTTOM": "下路", "UTILITY": "輔助"}
TEAM_ZH = {100: "藍方", 200: "紅方"}
SEVERITY_MARK = {"high": "🔴", "medium": "🟠", "low": "🟡", "info": "ℹ️"}


def champ(m: Metrics) -> str:
    return f"{ddragon.champion_name(m.champion)}({m.champion})"


def pct(x: float) -> str:
    return f"{x:.0%}"


def render_summary(game: Game, metrics: Sequence[Metrics], timeline: Optional[Timeline]) -> str:
    winner = TEAM_ZH.get(game.winning_team, "?")
    surrender = "是" if any(m.surrendered for m in metrics) else "否（旗標全為 0）"
    lines = ["# 對局摘要",
             f"- 版本 {game.version}｜時長 {game.duration_label}｜勝方 {winner}｜投降結束：{surrender}｜資料表 Data Dragon {ddragon.data_version()}"]
    for team in (100, 200):
        tm = team_of(tuple(metrics), team)
        lines.append("")
        lines.append(f"## {TEAM_ZH[team]}（{'勝' if tm and tm[0].win else '敗'}）"
                     f" 擊殺 {sum(m.kills for m in tm)}｜金 {sum(m.gold for m in tm)}｜對英雄傷害 {sum(m.dmg_champs for m in tm)}")
        lines.append("| 位置 | 英雄 | 玩家 | 等級 | K/D/A | 參與 | CS(/分) | 金(/分) | 對英雄傷害(/分) | 佔比 | 承傷 | 視野 | 死亡秒 |")
        lines.append("|---|---|---|---|---|---|---|---|---|---|---|---|---|")
        for m in tm:
            lines.append(f"| {POS_ZH.get(m.position, m.position)} | {champ(m)} | {m.name} | {m.level} | {m.kills}/{m.deaths}/{m.assists} "
                         f"| {pct(m.kill_participation)} | {m.cs} ({m.cs_per_min}) | {m.gold} ({m.gold_per_min}) "
                         f"| {m.dmg_champs} ({m.dmg_per_min}) | {pct(m.dmg_share)} | {m.dmg_taken} | {m.vision_score} | {m.time_dead_s} |")
    if timeline is not None:
        lines.extend(_render_fights(game, metrics, timeline))
    return "\n".join(lines)


def _render_fights(game: Game, metrics: Sequence[Metrics], tl: Timeline) -> list:
    if not tl.fights:
        return ["", "## 交戰時間軸", "（payload 中沒有識別到死亡事件）"]
    lines = ["", "## 交戰時間軸（依死亡事件聚類，12 秒內視為同一波）"]
    for f in tl.fights:
        blue = [ddragon.champion_name(metrics[e.player].champion) for e in f.deaths_of_team(game, 100)]
        red = [ddragon.champion_name(metrics[e.player].champion) for e in f.deaths_of_team(game, 200)]
        lines.append(f"- {f.clock}：藍方倒下 {len(blue)}（{', '.join(blue) or '-'}）／紅方倒下 {len(red)}（{', '.join(red) or '-'}）")
    return lines


def _row(label: str, mine, opp, team_avg, game_avg) -> str:
    return f"| {label} | {mine} | {opp if opp is not None else '-'} | {team_avg} | {game_avg} |"


def render_player(game: Game, metrics: Tuple[Metrics, ...], m: Metrics, timeline: Optional[Timeline],
                  flags: Sequence[Flag]) -> str:
    opp = opponent_of(metrics, m)
    tm = team_of(metrics, m.team)
    lines = [f"# {champ(m)} — {m.name}｜{TEAM_ZH.get(m.team)} {POS_ZH.get(m.position, m.position)}｜{'勝' if m.win else '敗'}｜{game.duration_label}"]
    if opp:
        lines.append(f"對位：{champ(opp)}（{opp.name}）")
    lines.append("")
    lines.append("## 數據對照（本人｜對位｜隊伍平均｜全場平均）")
    lines.append("| 指標 | 本人 | 對位 | 隊伍平均 | 全場平均 |")
    lines.append("|---|---|---|---|---|")
    fields = [("等級", "level"), ("擊殺", "kills"), ("死亡", "deaths"), ("助攻", "assists"), ("KDA", "kda"),
              ("擊殺參與率", "kill_participation"), ("CS", "cs"), ("CS/分", "cs_per_min"), ("金錢", "gold"), ("金/分", "gold_per_min"),
              ("對英雄傷害", "dmg_champs"), ("傷害/分", "dmg_per_min"), ("隊伍傷害佔比", "dmg_share"), ("傷害/1000金", "dmg_per_1k_gold"),
              ("總傷害", "dmg_total"), ("對防禦塔傷害", "dmg_turrets"), ("對目標傷害", "dmg_objectives"),
              ("承受英雄傷害", "dmg_taken_champs"), ("自我減傷", "dmg_mitigated"), ("死亡秒數", "time_dead_s"), ("死亡時間%", "time_dead_pct"),
              ("視野分數", "vision_score"), ("插眼", "wards_placed"), ("排眼", "wards_killed"), ("控制守衛", "control_wards"),
              ("技能施放", "spell_casts"), ("施放/分", "casts_per_min"), ("召喚師技能施放", "summoner_casts"),
              ("最大暴擊", "largest_crit"), ("最大連殺", "largest_multi_kill"), ("成型大件", "legendary_items"),
              ("控場秒數", "cc_time_s"), ("治療量", "heal"), ("護盾隊友", "shield_on_team"), ("Ping 次數", "pings")]
    for label, key in fields:
        mine = getattr(m, key)
        fmt = pct if key in ("kill_participation", "dmg_share") else (lambda v: v)
        lines.append(_row(label, fmt(mine), fmt(getattr(opp, key)) if opp else None,
                          fmt(average(getattr(x, key) for x in tm)) if key not in ("kill_participation", "dmg_share") else pct(average(getattr(x, key) for x in tm)),
                          fmt(average(getattr(x, key) for x in metrics)) if key not in ("kill_participation", "dmg_share") else pct(average(getattr(x, key) for x in metrics))))
    lines.extend(_render_damage_mix(m))
    lines.extend(_render_casts(m, opp))
    lines.extend(_render_build(m))
    lines.extend(_render_runes(m))
    if timeline is not None:
        lines.extend(_render_timeline(game, metrics, m, opp, timeline))
    lines.append("")
    lines.append("## 自動判讀")
    if not flags:
        lines.append("- 沒有觸發任何警示。")
    for f in flags:
        lines.append(f"- {SEVERITY_MARK.get(f.severity, '')} [{f.code}] {f.message}")
    return "\n".join(lines)


def _render_damage_mix(m: Metrics) -> list:
    total = max(m.dmg_physical + m.dmg_magic + m.dmg_true, 1)
    return ["", "## 傷害組成",
            f"- 物理 {m.dmg_physical}（{m.dmg_physical / total:.0%}）｜魔法 {m.dmg_magic}（{m.dmg_magic / total:.0%}）｜真實 {m.dmg_true}（{m.dmg_true / total:.0%}）",
            f"- 對英雄 {m.dmg_champs} 只佔總傷害 {m.dmg_total} 的 {m.dmg_champs / max(m.dmg_total, 1):.0%}（其餘打在小兵／野怪／建築）"]


def _render_casts(m: Metrics, opp: Optional[Metrics]) -> list:
    q, w, e, r = m.spell_cast_split
    s1, s2 = m.summoner_cast_split
    names = [ddragon.spell_name(s) for s in m.summoner_spells]
    lines = ["", "## 技能施放分項",
             f"- Q {q}／W {w}／E {e}／R {r}（合計 {m.spell_casts}，每分鐘 {m.casts_per_min}）",
             f"- 召喚師技能：{names[0]} {s1} 次／{names[1]} {s2} 次"]
    if opp:
        oq, ow, oe, orr = opp.spell_cast_split
        lines.append(f"- 對位 {ddragon.champion_name(opp.champion)}：Q {oq}／W {ow}／E {oe}／R {orr}（合計 {opp.spell_casts}）")
    return lines


def _render_build(m: Metrics) -> list:
    lines = ["", "## 終局裝備"]
    total_gold = 0
    for i, item in enumerate(m.items):
        if not item:
            continue
        info = ddragon.item_info(item) or {}
        gold = info.get("gold", 0)
        total_gold += gold
        star = "★" if ddragon.is_legendary(item) else ""
        slot = "飾品" if i == 6 else f"欄{i + 1}"
        lines.append(f"- {slot}：{ddragon.item_name(item)}{star}（{ddragon.item_name(item, 'en')}，{gold} 金）{'｜' + info['plaintext'] if info.get('plaintext') else ''}")
    lines.append(f"- 裝備總價值約 {total_gold} 金｜花費 {m.gold_spent}｜賺取 {m.gold}｜未花費 {m.unspent_gold}（★ = 成型大件）")
    return lines


def _render_runes(m: Metrics) -> list:
    primary = ddragon.rune_name(m.primary_style)
    sub = ddragon.rune_name(m.sub_style)
    names = [ddragon.rune_name(r) for r in m.runes]
    stats = [ddragon.rune_name(r) for r in m.stat_perks]
    spells = [ddragon.spell_name(s) for s in m.summoner_spells]
    return ["", "## 符文與召喚師技能",
            f"- 主系 {primary}：{' / '.join(names[:4])}",
            f"- 副系 {sub}：{' / '.join(names[4:6])}",
            f"- 屬性碎片：{' / '.join(stats)}",
            f"- 召喚師技能：{' / '.join(spells)}"]


def _render_timeline(game: Game, metrics, m: Metrics, opp: Optional[Metrics], tl: Timeline) -> list:
    lines = ["", "## 時間軸（來自 payload）"]
    deaths = tl.deaths_of(m.index)
    lines.append(f"- 死亡時間：{', '.join(_death_label(metrics, e) for e in deaths) if deaths else '無'}")
    kills = tl.kills_of(m.index)
    if "killer" in tl.detail:
        lines.append(f"- 本人擊殺（最後一擊）：{', '.join(f'{e.clock} {champ(metrics[e.player])}' for e in kills) if kills else '無'}")
    if opp:
        od = tl.deaths_of(opp.index)
        lines.append(f"- 對位 {ddragon.champion_name(opp.champion)} 死亡時間：{', '.join(_death_label(metrics, e) for e in od) if od else '無'}")
    for lv in (6, 11, 16):
        mine = tl.level_time(m.index, lv)
        theirs = tl.level_time(opp.index, lv) if opp else None
        if mine is not None or theirs is not None:
            lines.append(f"- {lv} 等：本人 {fmt_clock(mine) if mine is not None else '未達'}｜對位 {fmt_clock(theirs) if theirs is not None else '未達'}")
    ups = tl.level_ups_of(m.index)
    if ups:
        start = tl.starting_level(m.index)
        later = [e for e in ups if e.value > start]
        prefix = f"開場即 L{start}（加速模式）｜" if start > 1 else ""
        lines.append(f"- 升級序列：{prefix}{' '.join(f'L{e.value}@{e.clock}' for e in later)}")
    shop = tl.shop_of(m.index)
    purchases = tl.purchases_of(m.index)
    if purchases:
        lines.append(f"- 購買順序（{len(purchases)} 件）：{'、'.join(f'{e.clock} {ddragon.item_name(e.item)}({e.price:.0f})' for e in purchases)}")
    elif shop:
        lines.append(f"- 購物事件 {len(shop)} 次：{', '.join(e.clock for e in shop)}")
    act = tl.activity[m.index] if m.index < len(tl.activity) else ()
    if act:
        lines.append(f"- 每分鐘操作量（網路事件數；第 1 格＝0:00–0:59，依序往後）：{' '.join(str(a) for a in act)}")
        if opp and opp.index < len(tl.activity):
            lines.append(f"- 對位每分鐘操作量：{' '.join(str(a) for a in tl.activity[opp.index])}")
    mine_fights = [f for f in tl.fights if any(e.player == m.index for e in f.deaths)]
    if mine_fights:
        lines.append("- 本人陣亡的交戰：")
        for f in mine_fights:
            blue = [ddragon.champion_name(metrics[e.player].champion) for e in f.deaths_of_team(game, 100)]
            red = [ddragon.champion_name(metrics[e.player].champion) for e in f.deaths_of_team(game, 200)]
            lines.append(f"  - {f.clock}：藍方倒下 {', '.join(blue) or '-'}／紅方倒下 {', '.join(red) or '-'}")
    return lines


def _killer_suffix(metrics, e) -> str:
    if e.killer is not None:
        return f"，被 {champ(metrics[e.killer])} 擊殺"
    if e.killer_netid is not None:
        return "，被非英雄單位擊殺（塔／小兵／野怪）"
    return ""


def _death_label(metrics, e) -> str:
    if e.killer is not None:
        return f"{e.clock}(被{ddragon.champion_name(metrics[e.killer].champion)})"
    if e.killer_netid is not None:
        return f"{e.clock}(被非英雄)"
    return e.clock


def render_timeline(game: Game, metrics, tl: Timeline) -> str:
    lines = ["# 時間軸", f"- 封包對照表：patch {tl.profile.patch}｜死亡 type {tl.profile.death_types}｜升級 type {tl.profile.level_type}｜購物 type {tl.profile.shop_type}｜已驗證 {tl.profile.verified}"]
    lines.append("")
    lines.append("## 死亡事件")
    for e in tl.deaths:
        where = f" 於 {region_name(e.pos)}" if e.pos else ""
        lines.append(f"- {e.clock} {champ(metrics[e.player])}（{TEAM_ZH.get(metrics[e.player].team)}）{_killer_suffix(metrics, e)}{where}")
    lines.append("")
    lines.append("## 6 / 11 / 16 等時間")
    for m in metrics:
        marks = [f"L{lv} {fmt_clock(tl.level_time(m.index, lv))}" for lv in (6, 11, 16) if tl.level_time(m.index, lv) is not None]
        lines.append(f"- {champ(m)}：{'｜'.join(marks) or '無資料'}")
    lines.extend(_render_fights(game, metrics, tl))
    return "\n".join(lines)


def to_json(game: Game, metrics: Sequence[Metrics], timeline: Optional[Timeline],
            flags: Optional[dict] = None) -> str:
    payload = {
        "game": {"version": game.version, "length_ms": game.length_ms, "duration": game.duration_label,
                 "winning_team": game.winning_team, "patch": game.patch},
        "players": [dict(asdict(m), champion_zh=ddragon.champion_name(m.champion),
                         items_named=[ddragon.item_name(i) for i in m.items]) for m in metrics],
    }
    if timeline is not None:
        payload["timeline"] = {
            "profile": asdict(timeline.profile),
            "deaths": [asdict(e) for e in timeline.deaths],
            "level_ups": [asdict(e) for e in timeline.level_ups],
            "shop": [asdict(e) for e in timeline.shop],
            "activity": [list(r) for r in timeline.activity],
            "fights": [{"start": f.start, "end": f.end, "deaths": [asdict(e) for e in f.deaths]} for f in timeline.fights],
        }
    if flags:
        payload["flags"] = {k: [asdict(f) for f in v] for k, v in flags.items()}
    return json.dumps(payload, ensure_ascii=False, indent=2)
