"""Per-replay workspace: parse once, write a compact context for the conversation, answer follow-up queries."""
from __future__ import annotations

import json
from collections import Counter
import os
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from . import ddragon, spells, report
from .analysis import Metrics, flags_for, opponent_of
from .metadata import Game
from .names import resolve_player
from .pipeline import Session, load_session
from .positions import Track, distance, region_name
from .timeline import Timeline, fmt_clock

CACHE_DIR = Path(os.environ.get("LOL_REPLAY_CACHE", "~/.cache/lol-replay")).expanduser()
CURRENT_FILE = "current.json"
PRESENCE_RADIUS = 1500.0
FIGHT_SPACE_RADIUS = 2500.0
FIGHT_TIME_GAP_S = 12.0
FIGHT_LEAD_S = 8.0
FIGHT_TAIL_S = 5.0
DEATH_LEAD_S = 10.0
NEARBY_RADIUS = 1200.0
SAMPLE_STEP_S = 5.0


@dataclass(frozen=True)
class Workspace:
    directory: Path
    rofl_path: str
    focus: Optional[int]
    question: str
    session: Session

    @property
    def game(self) -> Game:
        return self.session.game

    @property
    def tracks(self) -> Dict[int, Track]:
        return self.session.tracks or {}


def workspace_dir(rofl_path: str) -> Path:
    p = Path(rofl_path)
    return CACHE_DIR / f"{p.stem}-{p.stat().st_size}"


def create_workspace(rofl_path: str, focus: Optional[str], question: Optional[str]) -> Workspace:
    session = load_session(rofl_path)
    focus_idx = None
    if focus:
        res = resolve_player(session.game, focus)
        if not res.ok:
            options = ", ".join(f"[{p.index}] {p.champion} {p.name}" for p in res.candidates)
            raise ValueError(f"無法唯一對應「{focus}」（{res.how}）。候選：{options}")
        focus_idx = res.player.index
    d = workspace_dir(rofl_path)
    d.mkdir(parents=True, exist_ok=True)
    ws = Workspace(d, str(Path(rofl_path).resolve()), focus_idx, question or "", session)
    (d / "context.md").write_text(render_context(ws), encoding="utf-8")
    (d / "game.json").write_text(game_json(ws), encoding="utf-8")
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    (CACHE_DIR / CURRENT_FILE).write_text(json.dumps({"dir": str(d), "rofl": ws.rofl_path, "focus": focus_idx, "question": ws.question}, ensure_ascii=False), encoding="utf-8")
    return ws


def load_current(rofl_path: Optional[str] = None) -> Workspace:
    if rofl_path is None:
        pointer = CACHE_DIR / CURRENT_FILE
        if not pointer.is_file():
            raise FileNotFoundError("沒有進行中的 session，請先執行 session 指令。")
        meta = json.loads(pointer.read_text(encoding="utf-8"))
        rofl_path, focus, question = meta["rofl"], meta.get("focus"), meta.get("question", "")
    else:
        focus, question = None, ""
    session = load_session(rofl_path)
    return Workspace(workspace_dir(rofl_path), rofl_path, focus, question, session)


# ---------------------------------------------------------------- queries
def parse_time(text: str) -> float:
    if ":" in text:
        m, s = text.split(":", 1)
        return int(m) * 60 + float(s)
    return float(text)


def position_of(ws: Workspace, index: int, t: float) -> Optional[Tuple[float, float]]:
    track = ws.tracks.get(index)
    return track.at(t) if track else None


def nearby(ws: Workspace, index: int, t: float, radius: float = NEARBY_RADIUS):
    me = position_of(ws, index, t)
    if me is None:
        return ()
    out = []
    for other in ws.game.players:
        if other.index == index:
            continue
        pos = position_of(ws, other.index, t)
        if pos is None:
            continue
        dist = distance(me, pos)
        if dist <= radius:
            out.append((other, dist, pos))
    return tuple(sorted(out, key=lambda x: x[1]))


@dataclass(frozen=True)
class SpatialFight:
    start: float
    end: float
    deaths: tuple
    center: Tuple[float, float]

    @property
    def clock(self) -> str:
        return fmt_clock(self.start)


def spatial_fights(ws: Workspace) -> Tuple[SpatialFight, ...]:
    """Cluster deaths that are close in time AND space (positions from the tracks)."""
    tl = ws.session.timeline
    if tl is None:
        return ()
    events = [(e, position_of(ws, e.player, e.time)) for e in tl.deaths]
    parent = list(range(len(events)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i, (a, pa) in enumerate(events):
        for j in range(i + 1, len(events)):
            b, pb = events[j]
            if b.time - a.time > FIGHT_TIME_GAP_S:
                break
            if pa is None or pb is None or distance(pa, pb) <= FIGHT_SPACE_RADIUS:
                parent[find(i)] = find(j)
    groups: Dict[int, list] = {}
    for i, ev in enumerate(events):
        groups.setdefault(find(i), []).append(ev)
    out = []
    for members in groups.values():
        members.sort(key=lambda ev: ev[0].time)
        spots = [pos for _, pos in members if pos is not None]
        center = (sum(s[0] for s in spots) / len(spots), sum(s[1] for s in spots) / len(spots)) if spots else (0.0, 0.0)
        out.append(SpatialFight(members[0][0].time, members[-1][0].time, tuple(e for e, _ in members), center))
    return tuple(sorted(out, key=lambda f: f.start))


def fight_presence(ws: Workspace, index: int, fight: SpatialFight) -> Optional[float]:
    """Distance from the player (at the fight start) to the fight's centre (None if unknown)."""
    me = position_of(ws, index, fight.start)
    if me is None or fight.center == (0.0, 0.0):
        return None
    return distance(me, fight.center)


# ---------------------------------------------------------------- rendering
def _pos_label(pos: Optional[Tuple[float, float]]) -> str:
    return "無位置資料" if pos is None else f"({pos[0]:.0f}, {pos[1]:.0f}) {region_name(pos)}"


def render_positions(ws: Workspace, index: int) -> str:
    game, tl = ws.game, ws.session.timeline
    track = ws.tracks.get(index)
    if track is None or not track.segments:
        return "\n## 位置\n（此版本沒有路徑點規格，無法還原位置）"
    m = ws.session.metrics[index]
    lines = ["", f"## 位置（來自路徑點封包，每分鐘所在區域）"]
    minutes = int(game.minutes) + 1
    per_min = []
    for mm in range(minutes):
        pos = track.at(mm * 60.0)
        per_min.append(f"{mm}'{region_name(pos) if pos else '?'}")
    lines.append("- " + "｜".join(per_min))
    if tl is not None:
        deaths = tl.deaths_of(index)
        if deaths:
            lines.append("- 死亡地點、擊殺者與當時附近英雄（1200 內）：")
            for e in deaths:
                pos = e.pos or track.at(e.time)
                near = nearby(ws, index, e.time)
                allies = [f"{ddragon.champion_name(p.champion)}{d:.0f}" for p, d, _ in near if p.team == m.team]
                enemies = [f"{ddragon.champion_name(p.champion)}{d:.0f}" for p, d, _ in near if p.team != m.team]
                killer = (f"被 {ddragon.champion_name(game.players[e.killer].champion)} 擊殺" if e.killer is not None
                          else "被非英雄單位擊殺" if e.killer_netid is not None else "擊殺者不明")
                lines.append(f"  - {e.clock} {_pos_label(pos)}｜{killer}｜隊友 {', '.join(allies) or '無'}｜敵人 {', '.join(enemies) or '無'}")
        fights = spatial_fights(ws)
        if fights:
            lines.append("- 各波交戰（時間＋地點聚類）與本人的距離（<1500 視為在場）：")
            for f in fights:
                d = fight_presence(ws, index, f)
                blue = [ddragon.champion_name(game.players[e.player].champion) for e in f.deaths if game.players[e.player].team == 100]
                red = [ddragon.champion_name(game.players[e.player].champion) for e in f.deaths if game.players[e.player].team == 200]
                where = region_name(f.center) if f.center != (0.0, 0.0) else "?"
                tag = "在場" if d is not None and d < PRESENCE_RADIUS else ("不在場" if d is not None else "?")
                dist_txt = f"{d:.0f}" if d is not None else "?"
                lines.append(f"  - {f.clock} {where}：藍方倒 {', '.join(blue) or '-'}／紅方倒 {', '.join(red) or '-'}｜本人{tag}（{dist_txt}）")
    return "\n".join(lines)


def render_casts(ws: Workspace, index: int) -> str:
    """Per-spell cast counts, usage inside each fight, and the casts around each death."""
    tl = ws.session.timeline
    if tl is None or "casts" not in tl.detail:
        return ""
    mine = tl.casts_of(index)
    if not mine:
        return "\n## 技能施放\n（這位玩家沒有解出任何施法封包）"
    counts = Counter(spells.label(c.spell_hash) for c in mine)
    lines = ["", f"## 技能施放（來自施法封包，共 {len(mine)} 次；追問用 casts 指令看逐次細節）"]
    lines.append("- 各技能次數：" + "、".join(f"{k} {v}" for k, v in counts.most_common(12)))
    fights = spatial_fights(ws)
    if fights:
        rows = []
        for f in fights:
            used = tl.casts_of(index, f.start - FIGHT_LEAD_S, f.end + FIGHT_TAIL_S)
            abilities = Counter(spells.label(c.spell_hash) for c in used if (spells.lookup(c.spell_hash) or spells.SpellRef("", "?", "")).slot in "QWERS")
            rows.append(f"{f.clock} {'、'.join(f'{k}×{v}' for k, v in abilities.most_common(6)) or '沒有放技能'}")
        lines.append("- 各波交戰（前 8 秒到結束後 5 秒）放了什麼：" + "｜".join(rows))
    for e in tl.deaths_of(index):
        before = tl.casts_of(index, e.time - DEATH_LEAD_S, e.time)
        seq = " → ".join(f"{spells.label(c.spell_hash)}@{c.clock}" for c in before if (spells.lookup(c.spell_hash) or spells.SpellRef("", "?", "")).slot != "A")
        lines.append(f"- 死亡前 {DEATH_LEAD_S:.0f} 秒（{e.clock}）的技能：{seq or '只有普攻或沒有施法'}")
    return "\n".join(lines)


def render_context(ws: Workspace) -> str:
    s = ws.session
    parts = ["# Replay session context", f"- 檔案：{ws.rofl_path}", f"- 問題：{ws.question or '（未指定）'}"]
    if ws.focus is not None:
        p = ws.game.players[ws.focus]
        parts.append(f"- 對象：{ddragon.champion_name(p.champion)}({p.champion}) {p.name}，隊伍 {p.team} {p.position}")
    parts.append(f"- 快取目錄：{ws.directory}（追問用 where/near/track/fight 指令）")
    parts.append("")
    parts.append(report.render_summary(ws.game, s.metrics, s.timeline))
    if ws.focus is not None:
        m = s.metrics[ws.focus]
        flags = flags_for(ws.game, s.metrics, m, s.timeline)
        parts.append("")
        parts.append(report.render_player(ws.game, s.metrics, m, s.timeline, flags))
        parts.append(render_positions(ws, ws.focus))
        parts.append(render_casts(ws, ws.focus))
    return "\n".join(parts)


def game_json(ws: Workspace) -> str:
    s = ws.session
    flags = {str(m.index): flags_for(ws.game, s.metrics, m, s.timeline) for m in s.metrics}
    base = json.loads(report.to_json(ws.game, s.metrics, s.timeline, flags))
    samples = {}
    for idx, track in ws.tracks.items():
        rows = []
        t = 0.0
        while t <= ws.game.minutes * 60:
            pos = track.at(t)
            if pos:
                rows.append([round(t, 1), round(pos[0]), round(pos[1])])
            t += SAMPLE_STEP_S
        samples[str(idx)] = rows
    base.update(focus=ws.focus, question=ws.question, rofl=ws.rofl_path, positions_every_5s=samples)
    return json.dumps(base, ensure_ascii=False)
