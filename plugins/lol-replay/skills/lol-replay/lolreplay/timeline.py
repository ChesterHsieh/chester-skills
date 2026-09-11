"""Time-stamped events recovered from the packet envelope: deaths, level-ups, shop visits,
per-minute activity and death clusters ("fights").

When the patch ships packet content specs (`specs/<patch>/packet_<type>.json`), deaths also carry
the killer and the death position, and shop events carry the item id and price."""
from __future__ import annotations

import math
from collections import Counter
from dataclasses import dataclass
from typing import Callable, Dict, Iterable, Optional, Tuple

from .blocks import Block
from .calibrate import PacketProfile
from .metadata import Game
from .payload.events import decode_cast, decode_kill, decode_purchase
from .payload.spec import PayloadSpec

FIGHT_WINDOW_S = 12.0
FIRST_LEVEL = 2
START_WINDOW_S = 10.0
PRESS_MERGE_S = 0.2        # the same spell re-sent within this window is one key press


@dataclass(frozen=True)
class Event:
    time: float
    player: int
    kind: str
    value: int = 0
    killer: Optional[int] = None          # player index of the killer (None: unknown or not a champion)
    killer_netid: Optional[int] = None    # raw net id of the killer object
    pos: Optional[Tuple[float, float]] = None
    item: Optional[int] = None
    price: Optional[float] = None
    gold_after: Optional[float] = None
    respawn: Optional[float] = None       # death timer in seconds (kill packet)

    @property
    def clock(self) -> str:
        return fmt_clock(self.time)


@dataclass(frozen=True)
class Cast:
    time: float
    player: int
    spell_hash: Optional[int]
    seq: Optional[int] = None             # client cast counter (duplicate packets share it)
    start: Optional[Tuple[float, float]] = None
    end: Optional[Tuple[float, float]] = None

    @property
    def clock(self) -> str:
        return fmt_clock(self.time)


@dataclass(frozen=True)
class Fight:
    start: float
    end: float
    deaths: Tuple[Event, ...]

    def deaths_of_team(self, game: Game, team: int) -> Tuple[Event, ...]:
        return tuple(e for e in self.deaths if game.players[e.player].team == team)

    @property
    def clock(self) -> str:
        return fmt_clock(self.start)


@dataclass(frozen=True)
class Timeline:
    deaths: Tuple[Event, ...]
    level_ups: Tuple[Event, ...]
    shop: Tuple[Event, ...]
    activity: Tuple[Tuple[int, ...], ...]
    fights: Tuple[Fight, ...]
    profile: PacketProfile
    detail: Tuple[str, ...] = ()          # which extras are decoded: "killer", "death_pos", "items", "casts"
    casts: Tuple[Cast, ...] = ()

    def casts_of(self, player: int, start: float = 0.0, end: float = float("inf")) -> Tuple[Cast, ...]:
        return tuple(c for c in self.casts if c.player == player and start <= c.time <= end)

    def deaths_of(self, player: int) -> Tuple[Event, ...]:
        return tuple(e for e in self.deaths if e.player == player)

    def kills_of(self, player: int) -> Tuple[Event, ...]:
        return tuple(e for e in self.deaths if e.killer == player)

    def level_ups_of(self, player: int) -> Tuple[Event, ...]:
        return tuple(e for e in self.level_ups if e.player == player)

    def shop_of(self, player: int) -> Tuple[Event, ...]:
        return tuple(e for e in self.shop if e.player == player)

    def purchases_of(self, player: int) -> Tuple[Event, ...]:
        return tuple(e for e in self.shop if e.player == player and e.item)

    def level_time(self, player: int, level: int) -> Optional[float]:
        for e in self.level_ups_of(player):
            if e.value == level:
                return e.time
        return None

    def starting_level(self, player: int) -> int:
        """Level reached within the first seconds (accelerated modes grant instant level-ups)."""
        return 1 + sum(1 for e in self.level_ups_of(player) if e.time < START_WINDOW_S)

    @property
    def accelerated(self) -> bool:
        players = range(len(self.activity))
        return bool(players) and all(self.starting_level(p) >= 2 for p in players)


def fmt_clock(seconds: float) -> str:
    s = int(round(seconds))
    return f"{s // 60}:{s % 60:02d}"


Decorator = Callable[[Block], Dict[str, object]]


def _events(blocks, net_ids, ptype: Optional[int], kind: str, decorate: Optional[Decorator] = None) -> Tuple[Event, ...]:
    if ptype is None:
        return ()
    index = {nid: i for i, nid in enumerate(net_ids)}
    out = []
    for b in blocks:
        if b.type != ptype or b.net_id not in index:
            continue
        extra = decorate(b) if decorate else {}
        out.append(Event(b.time, index[b.net_id], kind, **extra))
    return tuple(sorted(out, key=lambda e: e.time))


def _number_levels(level_events: Tuple[Event, ...]) -> Tuple[Event, ...]:
    seen: Counter = Counter()
    out = []
    for e in level_events:
        seen[e.player] += 1
        out.append(Event(e.time, e.player, e.kind, FIRST_LEVEL - 1 + seen[e.player]))
    return tuple(out)


def activity_per_minute(blocks, net_ids, buckets: int) -> Tuple[Tuple[int, ...], ...]:
    index = {nid: i for i, nid in enumerate(net_ids)}
    grid = [[0] * buckets for _ in net_ids]
    for b in blocks:
        slot = index.get(b.net_id)
        if slot is not None:
            m = int(b.time // 60)
            if 0 <= m < buckets:
                grid[slot][m] += 1
    return tuple(tuple(row) for row in grid)


def cluster_fights(deaths: Tuple[Event, ...], window: float = FIGHT_WINDOW_S) -> Tuple[Fight, ...]:
    fights = []
    current: list = []
    for e in deaths:
        if current and e.time - current[-1].time > window:
            fights.append(Fight(current[0].time, current[-1].time, tuple(current)))
            current = []
        current.append(e)
    if current:
        fights.append(Fight(current[0].time, current[-1].time, tuple(current)))
    return tuple(fights)


def _kill_decorator(spec: PayloadSpec, net_ids: Tuple[int, ...]) -> Optional[Decorator]:
    kill_spec = spec.packet_spec("kill")
    if kill_spec is None:
        return None
    index = {nid: i for i, nid in enumerate(net_ids)}

    def decorate(b: Block) -> Dict[str, object]:
        info = decode_kill(kill_spec, b.content)
        if info is None:
            return {}
        return {"killer": index.get(info.killer_netid) if info.killer_netid is not None else None,
                "killer_netid": info.killer_netid, "pos": info.pos, "respawn": info.respawn}
    return decorate


def _shop_decorator(spec: PayloadSpec) -> Optional[Decorator]:
    shop_spec = spec.packet_spec("shop")
    if shop_spec is None:
        return None

    def decorate(b: Block) -> Dict[str, object]:
        info = decode_purchase(shop_spec, b.content)
        if info is None:
            return {}
        return {"item": info.item, "value": info.item or 0, "price": info.price, "gold_after": info.gold_after}
    return decorate


def merge_key_presses(casts: Iterable[Cast], merge_s: float = PRESS_MERGE_S) -> Tuple[Cast, ...]:
    """Drop repeats of the same spell by the same player within `merge_s`. Some spells send one
    packet per phase (Tryndamere E, Irelia W charge/release), which would double their counts."""
    last: Dict[Tuple[int, Optional[int]], float] = {}
    out = []
    for c in sorted(casts, key=lambda c: c.time):
        key = (c.player, c.spell_hash)
        prev = last.get(key)
        last[key] = c.time
        if prev is None or c.time - prev > merge_s:
            out.append(c)
    return tuple(out)


def build_casts(blocks: Tuple[Block, ...], net_ids: Tuple[int, ...], spec: PayloadSpec) -> Tuple[Cast, ...]:
    cast_type = spec.packet_type("cast")
    cast_spec = spec.packet_spec("cast")
    if cast_type is None or cast_spec is None:
        return ()
    index = {nid: i for i, nid in enumerate(net_ids)}
    out = []
    seen = set()
    for b in blocks:
        if b.type != cast_type or b.net_id not in index:
            continue
        info = decode_cast(cast_spec, b.content)
        if info is None:
            continue
        player = index.get(info.caster_netid, index[b.net_id]) if info.caster_netid is not None else index[b.net_id]
        if info.seq is not None:
            if (player, info.seq) in seen:
                continue
            seen.add((player, info.seq))
        out.append(Cast(b.time, player, info.spell_hash, info.seq, info.start, info.end))
    return merge_key_presses(out)


def build_timeline(game: Game, blocks: Tuple[Block, ...], net_ids: Tuple[int, ...],
                   profile: PacketProfile, spec: Optional[PayloadSpec] = None) -> Timeline:
    detail = []
    death_type = profile.death_type
    kill_decorate = None
    if spec is not None and spec.packet_type("kill") in profile.death_types:
        death_type = spec.packet_type("kill")
        kill_decorate = _kill_decorator(spec, net_ids)
        detail += ["killer", "death_pos"]
    shop_decorate = None
    if spec is not None and spec.packet_type("shop") == profile.shop_type:
        shop_decorate = _shop_decorator(spec)
        detail.append("items")
    casts = build_casts(blocks, net_ids, spec) if spec is not None else ()
    if casts:
        detail.append("casts")
    deaths = _events(blocks, net_ids, death_type, "death", kill_decorate)
    levels = _number_levels(_events(blocks, net_ids, profile.level_type, "level"))
    shop = _events(blocks, net_ids, profile.shop_type, "shop", shop_decorate)
    buckets = max(1, math.ceil(game.minutes))
    return Timeline(deaths=deaths, level_ups=levels, shop=shop,
                    activity=activity_per_minute(blocks, net_ids, buckets),
                    fights=cluster_fights(deaths), profile=profile, detail=tuple(detail), casts=casts)
