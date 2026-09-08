"""Identify packet types by matching per-champion packet counts to metadata stats.

Packet type ids are renumbered by Riot every patch and the contents are obfuscated,
but the *envelope* is not: every block carries the net id of the object it concerns.
Counting blocks per champion and comparing the 10-vector with known end-of-game stats
(NUM_DEATHS, LEVEL-1, ITEMS_PURCHASED) identifies the death / level-up / shop packets
for any patch, and simultaneously proves which net id belongs to which player.
"""
from __future__ import annotations

import json
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Dict, Optional, Tuple

from .blocks import Block, NETID_OBJECT_BASE
from .metadata import Game

PROFILE_DIR = Path(__file__).parent / "profiles"
PLAYER_COUNT = 10
SHOP_TOLERANCE_PER_PLAYER = 4


@dataclass(frozen=True)
class PacketProfile:
    patch: str
    death_types: Tuple[int, ...]
    level_type: Optional[int]
    shop_type: Optional[int]
    verified: bool
    notes: str = ""

    @property
    def death_type(self) -> Optional[int]:
        return self.death_types[0] if self.death_types else None


def champion_net_ids(blocks: Tuple[Block, ...]) -> Tuple[int, ...]:
    """The 10 champion net ids, ordered like the metadata player list (ascending)."""
    counts = Counter(b.net_id for b in blocks if b.net_id >= NETID_OBJECT_BASE)
    top = [nid for nid, _ in counts.most_common(PLAYER_COUNT)]
    if len(top) < PLAYER_COUNT:
        return tuple(sorted(top))
    base = min(top)
    consecutive = tuple(range(base, base + PLAYER_COUNT))
    if set(consecutive) == set(top):
        return consecutive
    return tuple(sorted(top))


def count_matrix(blocks: Tuple[Block, ...], net_ids: Tuple[int, ...]) -> Dict[int, Tuple[int, ...]]:
    index = {nid: i for i, nid in enumerate(net_ids)}
    counts: Dict[int, list] = defaultdict(lambda: [0] * len(net_ids))
    for b in blocks:
        slot = index.get(b.net_id)
        if slot is not None:
            counts[b.type][slot] += 1
    return {t: tuple(v) for t, v in counts.items()}


def stat_vector(game: Game, key: str, offset: int = 0) -> Tuple[int, ...]:
    return tuple(max(0, p.int(key) + offset) for p in game.players)


def _exact_matches(matrix: Dict[int, Tuple[int, ...]], target: Tuple[int, ...]) -> Tuple[int, ...]:
    if sum(target) == 0:
        return ()
    return tuple(sorted(t for t, vec in matrix.items() if vec == target))


def _closest_match(matrix, target, tol_per_player: int) -> Optional[int]:
    if sum(target) == 0:
        return None
    best = None
    for t, vec in matrix.items():
        if all(abs(a - b) <= tol_per_player for a, b in zip(vec, target)):
            dist = sum(abs(a - b) for a, b in zip(vec, target))
            if best is None or dist < best[0]:
                best = (dist, t)
    return best[1] if best else None


def detect_profile(game: Game, matrix: Dict[int, Tuple[int, ...]]) -> PacketProfile:
    deaths = _exact_matches(matrix, stat_vector(game, "NUM_DEATHS"))
    # the shortest-content variant is irrelevant here; keep ascending ids, the first is used
    levels = _exact_matches(matrix, stat_vector(game, "LEVEL", -1))
    shop = _closest_match(matrix, stat_vector(game, "ITEMS_PURCHASED"), SHOP_TOLERANCE_PER_PLAYER)
    return PacketProfile(
        patch=game.patch, death_types=deaths, level_type=(levels[0] if levels else None),
        shop_type=shop, verified=bool(deaths),
        notes="auto-detected from per-champion packet counts vs NUM_DEATHS / LEVEL / ITEMS_PURCHASED")


def load_profile(patch: str, profile_dir: Path = PROFILE_DIR) -> Optional[PacketProfile]:
    path = profile_dir / f"{patch}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PacketProfile(patch=data["patch"], death_types=tuple(data.get("death_types", ())),
                             level_type=data.get("level_type"), shop_type=data.get("shop_type"),
                             verified=bool(data.get("verified")), notes=data.get("notes", ""))
    except (KeyError, ValueError, TypeError):
        return None


def save_profile(profile: PacketProfile, profile_dir: Path = PROFILE_DIR) -> Path:
    profile_dir.mkdir(parents=True, exist_ok=True)
    path = profile_dir / f"{profile.patch}.json"
    path.write_text(json.dumps(asdict(profile), ensure_ascii=False, indent=2), encoding="utf-8")
    return path


def profile_matches(profile: PacketProfile, game: Game, matrix: Dict[int, Tuple[int, ...]]) -> bool:
    if not profile.death_types:
        return False
    target = stat_vector(game, "NUM_DEATHS")
    return sum(target) > 0 and matrix.get(profile.death_type) == target


def resolve_profile(game: Game, matrix: Dict[int, Tuple[int, ...]], profile_dir: Path = PROFILE_DIR,
                    persist: bool = True) -> PacketProfile:
    """Use the stored profile for this patch when it still matches this game, else re-detect."""
    stored = load_profile(game.patch, profile_dir)
    if stored is not None and profile_matches(stored, game, matrix):
        return stored
    detected = detect_profile(game, matrix)
    if persist and detected.verified:
        save_profile(detected, profile_dir)
    return detected
