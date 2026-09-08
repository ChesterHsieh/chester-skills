#!/usr/bin/env python3
"""Build lolreplay/data/champion_spells.json from Data Dragon: per champion the passive and Q/W/E/R
spell script ids (the names the client hashes in cast packets) with zh_TW / en_US display names."""
import json
import sys
import urllib.request
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "lolreplay" / "data" / "champion_spells.json"
CDN = "https://ddragon.leagueoflegends.com/cdn"


def fetch(url: str) -> dict:
    with urllib.request.urlopen(url, timeout=30) as resp:
        return json.loads(resp.read().decode("utf-8"))


def main(version: str) -> int:
    keys = list(fetch(f"{CDN}/{version}/data/en_US/champion.json")["data"])
    out = {}
    for i, key in enumerate(keys):
        zh = fetch(f"{CDN}/{version}/data/zh_TW/champion/{key}.json")["data"][key]
        en = fetch(f"{CDN}/{version}/data/en_US/champion/{key}.json")["data"][key]
        slots = {}
        for slot, zs, es in zip("QWER", zh["spells"], en["spells"]):
            slots[slot] = {"id": es["id"], "zh": zs["name"], "en": es["name"]}
        slots["P"] = {"id": key + "Passive", "zh": zh["passive"]["name"], "en": en["passive"]["name"]}
        out[key] = slots
        print(f"{i + 1}/{len(keys)} {key}", file=sys.stderr)
    OUT.write_text(json.dumps({"version": version, "champions": out}, ensure_ascii=False, indent=0), encoding="utf-8")
    print(f"wrote {OUT} ({len(out)} champions)")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1] if len(sys.argv) > 1 else "16.17.1"))
