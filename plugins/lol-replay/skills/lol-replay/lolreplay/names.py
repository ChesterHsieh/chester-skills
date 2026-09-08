"""Resolve a free-form reference ("凱特零", "女警", "adc", "紅方打野", "windeve") to a player."""
from __future__ import annotations

import difflib
import re
from dataclasses import dataclass
from typing import Optional, Tuple

from .ddragon import all_champions
from .metadata import Game, Player

ROLE_ALIASES = {
    "TOP": ("top", "上路", "上單", "上"),
    "JUNGLE": ("jungle", "jg", "jng", "jungler", "打野", "野"),
    "MIDDLE": ("mid", "middle", "中路", "中單", "中"),
    "BOTTOM": ("bot", "bottom", "adc", "ad", "下路", "射手", "下"),
    "UTILITY": ("support", "sup", "supp", "輔助", "輔", "辅助", "辅"),
}
TEAM_ALIASES = {100: ("藍方", "藍隊", "藍色", "blue", "藍"), 200: ("紅方", "紅隊", "紅色", "red", "紅")}
NICKNAMES = {
    "女警": "Caitlyn", "凱特": "Caitlyn", "蠻王": "Tryndamere", "螳螂": "Khazix", "武器": "Jax", "皇子": "JarvanIV",
    "盲僧": "LeeSin", "瞎子": "LeeSin", "猴子": "MonkeyKing", "稻草人": "Fiddlesticks", "狗頭": "Nasus",
    "鱷魚": "Renekton", "石頭人": "Malphite", "機器人": "Blitzcrank", "蜘蛛": "Elise", "小炮": "Tristana",
    "小砲": "Tristana", "大嘴": "KogMaw", "老鼠": "Twitch", "男槍": "Graves", "女槍": "MissFortune",
    "寶石": "Taric", "牛頭": "Alistar", "錘石": "Thresh", "諾手": "Darius", "劍姬": "Fiora", "蛇女": "Cassiopeia",
    "火男": "Brand", "冰鳥": "Anivia", "冰女": "Lissandra", "寒冰": "Ashe", "小魚人": "Fizz", "龍女": "Shyvana",
    "豬妹": "Sejuani", "死歌": "Karthus", "卡牌": "TwistedFate", "卡特": "Katarina", "亞索": "Yasuo",
    "男刀": "Talon", "大樹": "Maokai", "蒙多": "DrMundo", "瑞文": "Riven", "銳雯": "Riven", "提莫": "Teemo",
    "阿狸": "Ahri", "金克絲": "Jinx", "薇恩": "Vayne", "vn": "Vayne", "ez": "Ezreal", "伊澤": "Ezreal",
    "露露": "Lulu", "光輝": "Lux", "布隆": "Braum", "霞": "Xayah", "洛": "Rakan", "娜美": "Nami", "沙皇": "Azir",
    "發條": "Orianna", "辛德拉": "Syndra", "劍魔": "Aatrox", "諾克": "Darius", "龍龜": "Rammus", "阿木木": "Amumu",
    "寡婦": "Evelynn", "妖姬": "Leblanc", "小法": "Veigar", "大頭": "Heimerdinger", "炸彈人": "Ziggs",
    "輪子媽": "Sivir", "德萊文": "Draven", "盧錫安": "Lucian", "飛機": "Corki", "卡莎": "Kaisa", "kaisa": "Kaisa",
    "cait": "Caitlyn", "tf": "TwistedFate", "j4": "JarvanIV", "mf": "MissFortune", "yi": "MasterYi",
    "劫": "Zed", "汎": "Vayne", "燼": "Jhin", "慎": "Shen", "關": "Gwen", "慧": "Hwei", "梅爾": "Mel",
}
MIN_FUZZY_RATIO = 0.5


@dataclass(frozen=True)
class Resolution:
    player: Optional[Player]
    candidates: Tuple[Player, ...]
    how: str

    @property
    def ok(self) -> bool:
        return self.player is not None


def normalize(text: str) -> str:
    return re.sub(r"[\s_\-'’.　]+", "", text or "").lower()


def resolve_player(game: Game, query: str) -> Resolution:
    q = normalize(query)
    if not q:
        return Resolution(None, game.players, "empty query")
    for step in (_by_index, _by_summoner_name, _by_champion, _by_team_and_role, _by_fuzzy_champion):
        result = step(game, q)
        if result is not None:
            return result
    return Resolution(None, game.players, "no match")


def _by_index(game: Game, q: str) -> Optional[Resolution]:
    if q.isdigit() and 0 <= int(q) < len(game.players):
        return Resolution(game.players[int(q)], (), "index")
    return None


def _by_summoner_name(game: Game, q: str) -> Optional[Resolution]:
    hits = tuple(p for p in game.players if normalize(p.name) == q or (q and q in normalize(p.name) and len(q) >= 3))
    if len(hits) == 1:
        return Resolution(hits[0], (), "summoner name")
    return None


def _champion_keys_for(q: str) -> Tuple[str, ...]:
    champs = all_champions()
    keys = []
    if q in NICKNAMES:
        keys.append(NICKNAMES[q])
    for key, info in champs.items():
        names = {normalize(key), normalize(info.get("en", "")), normalize(info.get("zh", ""))}
        if q in names:
            keys.append(key)
    if not keys:
        for key, info in champs.items():
            zh = normalize(info.get("zh", ""))
            en = normalize(info.get("en", ""))
            if (len(q) >= 2 and q in zh) or (len(q) >= 3 and q in en):
                keys.append(key)
    return tuple(dict.fromkeys(keys))


def _by_champion(game: Game, q: str) -> Optional[Resolution]:
    keys = set(_champion_keys_for(q))
    if not keys:
        return None
    hits = tuple(p for p in game.players if p.champion in keys)
    if len(hits) == 1:
        return Resolution(hits[0], (), "champion")
    if len(hits) > 1:
        return Resolution(None, hits, "ambiguous champion")
    return None


def _by_team_and_role(game: Game, q: str) -> Optional[Resolution]:
    team = next((t for t, aliases in TEAM_ALIASES.items() if any(q.startswith(a) for a in aliases)), None)
    rest = q
    if team is not None:
        rest = next(q[len(a):] for a in TEAM_ALIASES[team] if q.startswith(a))
    role = next((r for r, aliases in ROLE_ALIASES.items() if rest in aliases), None)
    if role is None:
        return None
    hits = tuple(p for p in game.players if p.position == role and (team is None or p.team == team))
    if len(hits) == 1:
        return Resolution(hits[0], (), "role")
    if hits:
        return Resolution(None, hits, "ambiguous role (both teams)")
    return None


def _by_fuzzy_champion(game: Game, q: str) -> Optional[Resolution]:
    champs = all_champions()
    pool = {}
    for p in game.players:
        info = champs.get(p.champion, {})
        for label in (normalize(p.champion), normalize(info.get("zh", "")), normalize(info.get("en", "")), normalize(p.name)):
            if label:
                pool.setdefault(label, p)
    best = difflib.get_close_matches(q, list(pool), n=2, cutoff=MIN_FUZZY_RATIO)
    if not best:
        return None
    players = tuple({pool[b].index: pool[b] for b in best}.values())
    if len(players) == 1:
        return Resolution(players[0], (), f"fuzzy ({best[0]})")
    return Resolution(None, players, "fuzzy ambiguous")
