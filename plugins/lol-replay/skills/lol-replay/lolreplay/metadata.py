"""Metadata JSON (tail of the file): game length and per-player end-of-game stats."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping, Optional, Tuple

TEAM_BLUE = 100
TEAM_RED = 200
POSITIONS = ("TOP", "JUNGLE", "MIDDLE", "BOTTOM", "UTILITY")


@dataclass(frozen=True)
class Player:
    index: int
    champion: str
    name: str
    tag: str
    team: int
    position: str
    win: bool
    stats: Mapping[str, str]

    def int(self, key: str, default: int = 0) -> int:
        value = self.stats.get(key)
        if value in (None, ""):
            return default
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return default

    def float(self, key: str, default: float = 0.0) -> float:
        value = self.stats.get(key)
        try:
            return float(value)
        except (TypeError, ValueError):
            return default

    @property
    def display(self) -> str:
        return f"{self.champion} ({self.name})" if self.name else self.champion


@dataclass(frozen=True)
class Game:
    version: str
    length_ms: int
    last_chunk_id: int
    last_keyframe_id: int
    players: Tuple[Player, ...]

    @property
    def minutes(self) -> float:
        return self.length_ms / 60000.0

    @property
    def duration_label(self) -> str:
        total = self.length_ms // 1000
        return f"{total // 60}:{total % 60:02d}"

    def team(self, team_id: int) -> Tuple[Player, ...]:
        return tuple(p for p in self.players if p.team == team_id)

    @property
    def winning_team(self) -> Optional[int]:
        for p in self.players:
            if p.win:
                return p.team
        return None

    def player(self, index: int) -> Player:
        return self.players[index]

    def opponent(self, player: Player) -> Optional[Player]:
        for other in self.players:
            if other.team != player.team and other.position == player.position:
                return other
        return None

    @property
    def patch(self) -> str:
        parts = self.version.split(".")
        return ".".join(parts[:2]) if len(parts) >= 2 else self.version


def parse_game(meta: dict, version: str = "") -> Game:
    import json
    stats_json = meta.get("statsJson", "[]")
    try:
        stats = json.loads(stats_json) if isinstance(stats_json, str) else list(stats_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"statsJson is not valid JSON: {exc}") from exc
    players = tuple(_player_from_stats(i, s) for i, s in enumerate(stats))
    return Game(
        version=version or str(meta.get("gameVersion", "")),
        length_ms=int(meta.get("gameLength", 0)),
        last_chunk_id=int(meta.get("lastGameChunkId", 0)),
        last_keyframe_id=int(meta.get("lastKeyFrameId", 0)),
        players=players,
    )


def _player_from_stats(index: int, s: dict) -> Player:
    win_value = str(s.get("WIN", "")).strip().lower()
    return Player(
        index=index,
        champion=str(s.get("SKIN", "")),
        name=str(s.get("RIOT_ID_GAME_NAME") or s.get("NAME") or ""),
        tag=str(s.get("RIOT_ID_TAG_LINE", "")),
        team=int(s.get("TEAM", 0) or 0),
        position=str(s.get("TEAM_POSITION") or s.get("INDIVIDUAL_POSITION") or ""),
        win=(win_value == "win"),
        stats={str(k): str(v) for k, v in s.items()},
    )
