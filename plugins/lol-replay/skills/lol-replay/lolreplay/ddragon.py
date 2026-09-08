"""Bundled Data Dragon lookup tables (zh_TW + en_US names) for items, champions, runes, spells."""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

DATA_DIR = Path(__file__).parent / "data"
STYLE_NAMES = {8000: ("精密", "Precision"), 8100: ("主宰", "Domination"), 8200: ("巫術", "Sorcery"),
               8300: ("啟發", "Inspiration"), 8400: ("堅決", "Resolve")}
TRINKET_IDS = frozenset({3340, 3363, 3364})
LEGENDARY_MIN_GOLD = 2200


@lru_cache(maxsize=None)
def _table(name: str, key: str) -> Dict[str, dict]:
    path = DATA_DIR / f"{name}.json"
    if not path.is_file():
        return {}
    with path.open(encoding="utf-8") as fh:
        return json.load(fh).get(key, {})


def data_version() -> str:
    path = DATA_DIR / "items.json"
    if not path.is_file():
        return "n/a"
    with path.open(encoding="utf-8") as fh:
        return json.load(fh).get("version", "n/a")


def item_info(item_id: int) -> Optional[dict]:
    return _table("items", "items").get(str(item_id))


def item_name(item_id: int, lang: str = "zh") -> str:
    if not item_id:
        return "-"
    info = item_info(item_id)
    if not info:
        return f"#{item_id}"
    return info.get(lang) or info.get("en") or f"#{item_id}"


def is_legendary(item_id: int) -> bool:
    info = item_info(item_id)
    if not info:
        return False
    return (not info.get("into")) and info.get("gold", 0) >= LEGENDARY_MIN_GOLD and "Boots" not in info.get("tags", [])


def is_boots(item_id: int) -> bool:
    info = item_info(item_id)
    return bool(info) and "Boots" in info.get("tags", [])


def champion_info(key: str) -> Optional[dict]:
    return _table("champions", "champions").get(key)


def champion_name(key: str, lang: str = "zh") -> str:
    info = champion_info(key)
    if not info:
        return key
    return info.get(lang) or key


def all_champions() -> Dict[str, dict]:
    return _table("champions", "champions")


def spell_name(spell_id: int, lang: str = "zh") -> str:
    info = _table("summoner_spells", "spells").get(str(spell_id))
    if not info:
        return f"#{spell_id}"
    return info.get(lang) or info.get("en") or f"#{spell_id}"


def rune_name(rune_id: int, lang: str = "zh") -> str:
    if rune_id in STYLE_NAMES:
        return STYLE_NAMES[rune_id][0 if lang == "zh" else 1]
    info = _table("runes", "runes").get(str(rune_id))
    if not info:
        return f"#{rune_id}"
    return info.get(lang) or info.get("en") or f"#{rune_id}"
