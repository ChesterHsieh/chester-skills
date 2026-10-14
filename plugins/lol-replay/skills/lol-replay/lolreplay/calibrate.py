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
from typing import Dict, List, Optional, Tuple

from .blocks import Block, NETID_OBJECT_BASE
from .metadata import Game

PROFILE_DIR = Path(__file__).parent / "profiles"
PLAYER_COUNT = 10
SHOP_TOLERANCE_PER_PLAYER = 4
SHOP_TOLERANCE_RATIO = 0.2  # long games drift further (sells / undo), so the tolerance scales with the count
LEVEL_RESEND_SLACK = 2      # extra level packets per player allowed before collapsing resends
LEVEL_CODE_PROBE = (-1, -2, 0, 1, 2)


@dataclass(frozen=True)
class PacketProfile:
    patch: str
    death_types: Tuple[int, ...]
    level_type: Optional[int]
    shop_type: Optional[int]
    verified: bool
    notes: str = ""
    # content byte (index, negative = from the end) that encodes the new level; a packet whose
    # code equals the player's previous one is a resend (16.20 sometimes sends a level twice)
    level_code_byte: Optional[int] = None

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


def _closest_match(matrix, target, tol_per_player: int, tol_ratio: float = 0.0) -> Optional[int]:
    if sum(target) == 0:
        return None
    best = None
    for t, vec in matrix.items():
        if all(abs(a - b) <= max(tol_per_player, round(b * tol_ratio)) for a, b in zip(vec, target)):
            dist = sum(abs(a - b) for a, b in zip(vec, target))
            if best is None or dist < best[0]:
                best = (dist, t)
    return best[1] if best else None


def _code_sequences(blocks: Tuple[Block, ...], ptype: int, net_ids: Tuple[int, ...],
                    index: int) -> Optional[List[List[int]]]:
    """Per player, the code byte of each `ptype` packet with consecutive repeats collapsed."""
    slots = {nid: i for i, nid in enumerate(net_ids)}
    seqs: List[List[int]] = [[] for _ in net_ids]
    for b in blocks:
        slot = slots.get(b.net_id)
        if b.type != ptype or slot is None:
            continue
        if not -len(b.content) <= index < len(b.content):
            return None
        code = b.content[index]
        if not seqs[slot] or seqs[slot][-1] != code:
            seqs[slot].append(code)
    return seqs


def level_code_byte(blocks: Tuple[Block, ...], ptype: int, net_ids: Tuple[int, ...]) -> Optional[int]:
    """The content byte that encodes the level: every player walks the same code sequence
    (L2, L3, ...) once collapsed, and no code repeats within it."""
    for index in LEVEL_CODE_PROBE:
        seqs = _code_sequences(blocks, ptype, net_ids, index)
        if not seqs or not any(seqs):
            continue
        longest = max(seqs, key=len)
        if len(set(longest)) == len(longest) > 1 and all(s == longest[:len(s)] for s in seqs):
            return index
    return None


def level_counts(blocks: Tuple[Block, ...], ptype: int, net_ids: Tuple[int, ...], index: int) -> Tuple[int, ...]:
    return tuple(len(s) for s in _code_sequences(blocks, ptype, net_ids, index) or [[] for _ in net_ids])


def _level_with_resends(matrix, target, blocks, net_ids) -> Tuple[Optional[int], Optional[int]]:
    """Level packet whose raw counts exceed LEVEL-1 only by resends of the same level."""
    if sum(target) == 0:
        return None, None
    for t in sorted(matrix):
        vec = matrix[t]
        if vec == target or not all(b <= a <= b + LEVEL_RESEND_SLACK for a, b in zip(vec, target)):
            continue
        index = level_code_byte(blocks, t, net_ids)
        if index is not None and level_counts(blocks, t, net_ids, index) == target:
            return t, index
    return None, None


def detect_profile(game: Game, matrix: Dict[int, Tuple[int, ...]],
                   blocks: Optional[Tuple[Block, ...]] = None,
                   net_ids: Optional[Tuple[int, ...]] = None) -> PacketProfile:
    deaths = _exact_matches(matrix, stat_vector(game, "NUM_DEATHS"))
    # the shortest-content variant is irrelevant here; keep ascending ids, the first is used
    level_target = stat_vector(game, "LEVEL", -1)
    levels = _exact_matches(matrix, level_target)
    level_type, code_byte = (levels[0] if levels else None), None
    if level_type is None and blocks is not None and net_ids is not None:
        level_type, code_byte = _level_with_resends(matrix, level_target, blocks, net_ids)
    shop = _closest_match(matrix, stat_vector(game, "ITEMS_PURCHASED"), SHOP_TOLERANCE_PER_PLAYER,
                          SHOP_TOLERANCE_RATIO)
    return PacketProfile(
        patch=game.patch, death_types=deaths, level_type=level_type,
        shop_type=shop, verified=bool(deaths),
        notes="auto-detected from per-champion packet counts vs NUM_DEATHS / LEVEL / ITEMS_PURCHASED",
        level_code_byte=code_byte)


def load_profile(patch: str, profile_dir: Path = PROFILE_DIR) -> Optional[PacketProfile]:
    path = profile_dir / f"{patch}.json"
    if not path.is_file():
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return PacketProfile(patch=data["patch"], death_types=tuple(data.get("death_types", ())),
                             level_type=data.get("level_type"), shop_type=data.get("shop_type"),
                             verified=bool(data.get("verified")), notes=data.get("notes", ""),
                             level_code_byte=data.get("level_code_byte"))
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


def _complete(profile: PacketProfile) -> bool:
    """A stored profile without level or shop type was written before the detector could find them."""
    return profile.level_type is not None and profile.shop_type is not None


def resolve_profile(game: Game, matrix: Dict[int, Tuple[int, ...]], profile_dir: Path = PROFILE_DIR,
                    persist: bool = True, blocks: Optional[Tuple[Block, ...]] = None,
                    net_ids: Optional[Tuple[int, ...]] = None) -> PacketProfile:
    """Use the stored profile for this patch when it still matches this game, else re-detect."""
    stored = load_profile(game.patch, profile_dir)
    if stored is not None and profile_matches(stored, game, matrix) and _complete(stored):
        return stored
    detected = detect_profile(game, matrix, blocks, net_ids)
    if persist and detected.verified:
        save_profile(detected, profile_dir)
    return detected
