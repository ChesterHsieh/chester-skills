"""Spell names for the hashes carried by cast packets.

The client identifies a spell script by the classic League "spell hash" (an ELF hash of the
lower-cased script name, 28 bits). Cast packets carry that hash; this module maps it back to a
champion ability (Q/W/E/R/passive), a summoner spell, a basic attack or a recall by hashing every
name we know from Data Dragon plus the standard attack/recall script names."""
from __future__ import annotations

import json
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Dict, Optional

DATA_DIR = Path(__file__).parent / "data"
ATTACK_SUFFIXES = ("BasicAttack", "BasicAttack2", "BasicAttack3", "BasicAttack4", "BasicAttack5",
                   "BasicAttack6", "BasicAttack7", "BasicAttack8", "CritAttack", "CritAttack2", "CritAttack3")
# second/third casts and recasts share the ability's script id with a suffix (ZedW2, IreliaE2, QuinnRFinale, NocturneParanoia2)
RECAST_SUFFIXES = {"2": "第二段", "3": "第三段", "Recast": "再施放", "Finale": "第二段", "End": "結束", "Cast": "", "Missile": "", "Toggle": "切換", "Wrapper": ""}
RECALL_NAMES = {"Recall": "回城", "SuperRecall": "強化回城", "RecallImproved": "回城", "OdinRecall": "回城", "TeamRecall": "回城"}
GENERIC_NAMES = {
    "TrinketTotemLvl1": "飾品眼", "TrinketTotemLvl2": "飾品眼", "TrinketSweeperLvl1": "掃描透鏡", "TrinketOrbLvl1": "先知飾品", "TrinketOrbLvl3": "先知飾品",
    "ItemCrystalFlask": "重生藥水", "ItemDarkCrystalFlask": "腐敗藥水", "ItemPotion": "生命藥水", "RefillablePotion": "重生藥水", "ItemBiscuit": "餅乾", "ItemControlWard": "控制守衛", "ItemSightWard": "偵查守衛",
    "SummonerSmite": "懲戒", "SummonerSmiteAvatarOffensive": "懲戒", "SummonerSmiteAvatarUtility": "懲戒", "SummonerSmiteAvatarDefensive": "懲戒",
    "S5_SummonerSmitePlayerGanker": "懲戒", "S5_SummonerSmiteDuel": "懲戒", "SummonerSmitePlayerGanker": "懲戒", "SummonerSmiteDuel": "懲戒",
    "SummonerTeleportUpgrade": "強化傳送", "S12_SummonerTeleportUpgrade": "強化傳送", "SummonerFlashPerksHextechFlashtraptionV2": "海克斯閃現",
}


@dataclass(frozen=True)
class SpellRef:
    script: str            # script name, e.g. "CaitlynQ"
    slot: str              # Q/W/E/R/P, "S" summoner, "A" attack, "B" recall, "I" item/trinket/potion, "?" unknown
    zh: str
    champion: Optional[str] = None

    @property
    def label(self) -> str:
        if self.slot in "QWER":
            return f"{self.slot}({self.zh})"
        if self.slot == "P":
            return f"被動({self.zh})"
        return self.zh


def elf_hash(name: str) -> int:
    """League spell hash: ELF hash over the lower-cased script name."""
    h = 0
    for ch in name.lower():
        h = ((h << 4) + ord(ch)) & 0xFFFFFFFF
        high = h & 0xF0000000
        if high:
            h ^= high >> 24
        h &= ~high & 0xFFFFFFFF
    return h


@lru_cache(maxsize=None)
def _champion_spells() -> Dict[str, dict]:
    path = DATA_DIR / "champion_spells.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("champions", {})


@lru_cache(maxsize=None)
def _summoner_spells() -> Dict[str, dict]:
    path = DATA_DIR / "summoner_spells.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("spells", {})


@lru_cache(maxsize=None)
def _items() -> Dict[str, dict]:
    path = DATA_DIR / "items.json"
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8")).get("items", {})


@lru_cache(maxsize=None)
def hash_table() -> Dict[int, SpellRef]:
    table: Dict[int, SpellRef] = {}
    for champ, slots in _champion_spells().items():
        for slot, info in slots.items():
            table[elf_hash(info["id"])] = SpellRef(info["id"], slot, info["zh"], champ)
            for suffix, note in RECAST_SUFFIXES.items():
                script = info["id"] + suffix
                table.setdefault(elf_hash(script), SpellRef(script, slot, f"{info['zh']}{note}", champ))
                if slot in "QWER":
                    script = champ + slot + suffix
                    table.setdefault(elf_hash(script), SpellRef(script, slot, f"{info['zh']}{note}", champ))
        for suffix in ATTACK_SUFFIXES:
            script = champ + suffix
            table[elf_hash(script)] = SpellRef(script, "A", "普攻" if "Crit" not in suffix else "暴擊普攻", champ)
    for info in _summoner_spells().values():
        table[elf_hash(info["en"])] = SpellRef(info["en"], "S", info["zh"])
    for script, zh in GENERIC_NAMES.items():
        table.setdefault(elf_hash(script), SpellRef(script, "S" if script.startswith("Summoner") else "I", zh))
    for item_id, info in _items().items():
        script = f"{item_id}Active"
        table.setdefault(elf_hash(script), SpellRef(script, "I", f"道具主動：{info.get('zh') or info.get('en') or item_id}"))
    for script, zh in RECALL_NAMES.items():
        table[elf_hash(script)] = SpellRef(script, "B", zh)
    return table


def lookup(spell_hash: Optional[int]) -> Optional[SpellRef]:
    if spell_hash is None:
        return None
    return hash_table().get(spell_hash & 0xFFFFFFFF)


def label(spell_hash: Optional[int]) -> str:
    ref = lookup(spell_hash)
    if ref is not None:
        return ref.label
    return f"未知技能#{spell_hash:08x}" if spell_hash is not None else "?"
