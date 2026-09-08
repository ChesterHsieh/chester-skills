"""One-call loader: file -> game -> metrics (-> payload timeline when available)."""
from __future__ import annotations

import sys
from dataclasses import dataclass
from typing import Dict, Optional, Tuple

from .analysis import Metrics, compute_metrics
from .positions import Track, build_tracks
from .payload.spec import PayloadSpec, load_spec
from .blocks import BlockParseError, game_blocks
from .calibrate import PacketProfile, champion_net_ids, count_matrix, resolve_profile
from .container import RoflFile, RoflFormatError, read_rofl
from .metadata import Game, parse_game
from .timeline import Timeline, build_timeline


@dataclass(frozen=True)
class Session:
    rofl: RoflFile
    game: Game
    metrics: Tuple[Metrics, ...]
    timeline: Optional[Timeline]
    net_ids: Tuple[int, ...]
    profile: Optional[PacketProfile]
    warnings: Tuple[str, ...]
    tracks: Optional[Dict[int, Track]] = None
    spec: Optional[PayloadSpec] = None


def load_session(path: str, payload: bool = True, persist_profile: bool = True) -> Session:
    rofl = read_rofl(path)
    game = parse_game(rofl.metadata(), rofl.game_version)
    metrics = compute_metrics(game)
    if not payload or rofl.format_version != 2:
        note = () if payload else ("payload skipped (--no-payload)",)
        if payload and rofl.format_version != 2:
            note = ("ROFL1 file: payload is Blowfish encrypted, metadata only",)
        return Session(rofl, game, metrics, None, (), None, note)
    try:
        blocks = game_blocks(rofl)
    except (BlockParseError, RoflFormatError) as exc:
        return Session(rofl, game, metrics, None, (), None, (f"payload unreadable, metadata only: {exc}",))
    net_ids = champion_net_ids(blocks)
    warnings = []
    if len(net_ids) != len(game.players):
        warnings.append(f"found {len(net_ids)} champion net ids for {len(game.players)} players")
    matrix = count_matrix(blocks, net_ids)
    profile = resolve_profile(game, matrix, persist=persist_profile)
    if not profile.verified:
        warnings.append("packet profile unverified: death packet not identified, timeline may be empty")
    spec = load_spec(game.patch)
    timeline = build_timeline(game, blocks, net_ids, profile, spec)
    tracks = build_tracks(spec, blocks, net_ids) if spec else None
    if spec is None:
        warnings.append(f"no payload spec for patch {game.patch}: positions unavailable")
    return Session(rofl, game, metrics, timeline, net_ids, profile, tuple(warnings), tracks, spec)


def warn(session: Session) -> None:
    for w in session.warnings:
        print(f"[warn] {w}", file=sys.stderr)
