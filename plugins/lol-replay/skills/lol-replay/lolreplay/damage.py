"""Per-hit damage and basic attacks (16.20+ damage / attack packets): what each champion's damage to
champions was made of, who it took damage from, how many basic attacks it started and landed, and
what it dealt and took in each fight.

Damage packets carry every damage instance a champion is involved in, with the dealing object and
the script that dealt it (TalonWMissileOne, CaitlynCritAttack, a minion attack...). Per champion,
damage dealt to champions and damage taken equal the end-of-game TOTAL_DAMAGE_DEALT_TO_CHAMPIONS
and TOTAL_DAMAGE_TAKEN. Damage whose source is not a champion comes from minions, monsters,
turrets or champion-owned units (clones, pets). Attack packets mark the start of
a basic attack (target and target position); the attack landed when a damage instance with that
champion's BasicAttack/CritAttack script follows. Hits on non-champions are only partly in the
replay, so attack hits are counted on champions only."""
from __future__ import annotations

from collections import defaultdict
from dataclasses import dataclass
from typing import Callable, Dict, Optional, Sequence, Tuple

from . import spells
from .timeline import Damage, Timeline

NameFn = Callable[[int], str]
SLOT_ORDER = ("A", "Q", "W", "E", "R", "P", "S", "I", "?")
SLOT_NAMES = {"A": "普攻", "P": "被動", "S": "召喚師技能", "I": "道具", "?": "其他"}
UNNAMED = "未辨識（多為道具／符文效果）"


def slot_of(d: Damage) -> str:
    ref = spells.lookup(d.spell_hash)
    return ref.slot if ref is not None and ref.slot in SLOT_ORDER else "?"


@dataclass(frozen=True)
class DamageSummary:
    total: float                                  # damage to champions
    by_slot: Tuple[Tuple[str, float], ...]        # (slot, amount), largest first
    attack_hits: int                              # basic attacks that hit a champion
    crits: int
    attacks: int                                  # basic attacks started (any target)
    attacks_on_champions: int
    taken: float                                  # damage taken from everything
    taken_by: Tuple[Tuple[Optional[int], float], ...]   # (player or None for minions/monsters/turrets, amount)
    other_by: Tuple[Tuple[str, float], ...] = ()       # the "其他" bucket by script name (dragon souls, burning...)

    @property
    def crit_rate(self) -> Optional[float]:
        return self.crits / self.attack_hits if self.attack_hits else None

    @property
    def attack_hit_rate(self) -> Optional[float]:
        """landed share of the basic attacks started on champions (cancels, misses, blinds, deaths)."""
        return min(1.0, self.attack_hits / self.attacks_on_champions) if self.attacks_on_champions else None


def summarize(tl: Timeline, player: int, start: float = 0.0, end: float = float("inf")) -> DamageSummary:
    dealt = tl.damage_dealt(player, start, end)
    slots: Dict[str, float] = defaultdict(float)
    other: Dict[str, float] = defaultdict(float)
    for d in dealt:
        slot = slot_of(d)
        slots[slot] += d.amount
        if slot == "?":
            ref = spells.lookup(d.spell_hash)
            other[ref.zh if ref is not None else UNNAMED] += d.amount
    hits = [d for d in dealt if d.is_attack]
    attacks = tl.attacks_of(player, start, end)
    taken = tl.damage_taken(player, start, end)
    by: Dict[Optional[int], float] = defaultdict(float)
    for d in taken:
        by[d.source] += d.amount
    return DamageSummary(
        total=sum(d.amount for d in dealt),
        by_slot=tuple(sorted(slots.items(), key=lambda kv: -kv[1])),
        attack_hits=len(hits), crits=sum(1 for d in hits if d.is_crit),
        attacks=len(attacks), attacks_on_champions=sum(1 for a in attacks if a.target is not None),
        taken=sum(d.amount for d in taken),
        taken_by=tuple(sorted(by.items(), key=lambda kv: -kv[1])),
        other_by=tuple(sorted(other.items(), key=lambda kv: -kv[1])))


def slot_label(slot: str, player_spells: Optional[Dict[str, str]] = None) -> str:
    if slot in "QWER" and player_spells and slot in player_spells:
        return f"{slot}({player_spells[slot]})"
    return SLOT_NAMES.get(slot, slot)


def champion_spell_names(champion: str) -> Dict[str, str]:
    return {slot: info["zh"] for slot, info in spells._champion_spells().get(champion, {}).items()}


def breakdown_text(s: DamageSummary, names: Dict[str, str]) -> str:
    if not s.total:
        return "0"
    parts = []
    for slot, amount in s.by_slot:
        if amount < 1:
            continue
        text = f"{slot_label(slot, names)} {amount:.0f}（{amount / s.total:.0%}）"
        if slot == "?" and s.other_by:
            text += "〔" + "、".join(f"{k} {v:.0f}" for k, v in s.other_by[:4] if v >= 1) + "〕"
        parts.append(text)
    return "、".join(parts)


def attack_text(s: DamageSummary) -> str:
    bits = [f"普攻出手 {s.attacks} 次（對英雄 {s.attacks_on_champions}；含被取消、追擊中斷的出手）", f"命中英雄 {s.attack_hits} 下"]
    if s.attack_hit_rate is not None:
        bits.append(f"打英雄的出手有 {s.attack_hit_rate:.0%} 打出傷害")
    if s.crit_rate is not None:
        bits.append(f"暴擊 {s.crits} 下（{s.crit_rate:.0%}）")
    return "，".join(bits)


def taken_text(s: DamageSummary, name: NameFn, top: int = 4) -> str:
    rows = [f"{name(p) if p is not None else '小兵／野怪／防禦塔'} {amount:.0f}" for p, amount in s.taken_by[:top] if amount >= 1]
    return "、".join(rows) or "無"


def hits_text(rows: Sequence[Damage], name: NameFn, taken: bool) -> str:
    """One line per hit: time, other side, spell, amount."""
    out = []
    for d in rows:
        other = d.source if taken else d.target
        who = name(other) if other is not None else ("小兵／野怪／防禦塔" if taken else "非英雄單位")
        out.append(f"{d.clock} {'←' if taken else '→'} {who} {spells.label(d.spell_hash) if d.spell_hash else '?'} {d.amount:.0f}")
    return out


def per_target(rows: Sequence[Damage]) -> Tuple[Tuple[Optional[int], float, int], ...]:
    acc: Dict[Optional[int], list] = defaultdict(lambda: [0.0, 0])
    for d in rows:
        acc[d.target][0] += d.amount
        acc[d.target][1] += 1
    return tuple(sorted(((k, v[0], v[1]) for k, v in acc.items()), key=lambda r: -r[1]))
