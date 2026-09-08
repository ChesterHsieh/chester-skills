"""Champion position tracks built from WaypointGroup packets, with interpolation at any game time."""
from __future__ import annotations

import bisect
import math
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .blocks import Block
from .payload.blob import BlobError, decode_waypoint_packet
from .payload.spec import PayloadSpec
from .payload.waypoints import WaypointRecord, parse_records

MAP_SIZE = 14800.0


@dataclass(frozen=True)
class PathSegment:
    time: float
    speed: float
    points: Tuple[Tuple[float, float], ...]

    def position_at(self, t: float) -> Tuple[float, float]:
        """Walk the path at `speed` for (t - time) seconds."""
        if len(self.points) == 1 or self.speed <= 0:
            return self.points[0]
        remaining = max(0.0, t - self.time)
        for (x1, y1), (x2, y2) in zip(self.points, self.points[1:]):
            dist = math.hypot(x2 - x1, y2 - y1)
            if dist == 0:
                continue
            dt = dist / self.speed
            if remaining <= dt:
                f = remaining / dt
                return (x1 + (x2 - x1) * f, y1 + (y2 - y1) * f)
            remaining -= dt
        return self.points[-1]


@dataclass(frozen=True)
class Track:
    segments: Tuple[PathSegment, ...]

    @property
    def times(self) -> Tuple[float, ...]:
        return tuple(s.time for s in self.segments)

    def at(self, t: float) -> Optional[Tuple[float, float]]:
        if not self.segments:
            return None
        i = bisect.bisect_right(self.times, t) - 1
        if i < 0:
            return self.segments[0].points[0]
        return self.segments[i].position_at(t)

    def speed_at(self, t: float) -> Optional[float]:
        i = bisect.bisect_right(self.times, t) - 1
        return self.segments[i].speed if i >= 0 else None


def build_tracks(spec: PayloadSpec, blocks: Tuple[Block, ...], net_ids: Tuple[int, ...]) -> Dict[int, Track]:
    """Map player index -> Track from all waypoint packets in the stream."""
    index = {nid: i for i, nid in enumerate(net_ids)}
    segs: Dict[int, list] = {i: [] for i in index.values()}
    for b in blocks:
        if b.type != spec.waypoint_type:
            continue
        try:
            blob = decode_waypoint_packet(spec, b.content)
            records, _ = parse_records(spec, blob)
        except (BlobError, IndexError, ValueError):
            continue
        for rec in records:
            slot = index.get(rec.net_id)
            if slot is not None and rec.points:
                segs[slot].append(PathSegment(b.time, rec.speed, rec.points))
    return {i: Track(tuple(sorted(s, key=lambda seg: seg.time))) for i, s in segs.items()}


def distance(a: Tuple[float, float], b: Tuple[float, float]) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def region_name(pos: Tuple[float, float]) -> str:
    """Coarse Summoner's Rift region label (blue base is bottom-left, red base top-right)."""
    x, y = pos
    if x < 2200 and y < 2200:
        return "藍方基地"
    if x > 12600 and y > 12600:
        return "紅方基地"
    diag = (x - y) / MAP_SIZE
    if abs(diag) < 0.12:
        return "中路" if 4000 < x < 10800 else ("藍方中路塔前" if x <= 4000 else "紅方中路塔前")
    if y > x:
        return "上路" if y > 9000 else "藍方上半野區" if x < 6500 else "紅方上半野區"
    return "下路" if x > 9000 else "藍方下半野區" if y < 6500 else "紅方下半野區"
