#!/usr/bin/env python3
"""Build lolreplay/data/champion_spell_scripts.json from CommunityDragon's extracted champion bins:
every spell script a champion owns (missiles, recasts, passive procs, attack variants), with the
ability slot it belongs to, plus the shared (non-champion) spell scripts such as dragon souls. Damage packets name the damage source by the hash of these scripts
(TalonWMissileOne, TalonRMisTwo, BriarP...), which Data Dragon does not list.

    python3 tools/fetch_spell_scripts.py [CDRAGON_VERSION]     (default: latest)
"""
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

DATA = Path(__file__).resolve().parent.parent / "lolreplay" / "data"
OUT = DATA / "champion_spell_scripts.json"
CDRAGON = "https://raw.communitydragon.org/{ver}/game/data/characters/{c}/{c}.bin.json"
BIN_ENTRIES = "https://raw.communitydragon.org/data/hashes/lol/hashes.binentries.txt"
SLOT_RE = re.compile(r"(Q|W|E|R|Passive)Ability$")


def fetch(url, raw=False):
    req = urllib.request.Request(url, headers={"User-Agent": "lol-replay/fetch_spell_scripts"})
    with urllib.request.urlopen(req, timeout=300) as resp:
        body = resp.read().decode("utf-8", errors="replace")
    return body if raw else json.loads(body)


def shared_scripts():
    """Spell scripts not owned by a champion (Shared/Spells/*: dragon souls, burning, elder...)."""
    names = set()
    for line in fetch(BIN_ENTRIES, raw=True).splitlines():
        path = line.split(" ", 1)[-1]
        parts = path.split("/")
        if len(parts) == 3 and parts[0] == "Shared" and parts[1] == "Spells":
            names.add(parts[2])
    return sorted(names)


def slot_of(path, abilities, dd_ids):
    """Q/W/E/R/P for a spell path, from its ability object (child list or the ability folder name)."""
    for ability, (root, children) in abilities.items():
        if path in children or path == root:
            root_name = root.rsplit("/", 1)[-1]
            for slot, sid in dd_ids.items():
                if root_name.lower() == sid.lower():
                    return slot
            m = SLOT_RE.search(ability.rsplit("/", 1)[-1])
            if m:
                return "P" if m.group(1) == "Passive" else m.group(1)
    parts = path.split("/")
    if len(parts) >= 2:
        m = SLOT_RE.search(parts[-2])
        if m:
            return "P" if m.group(1) == "Passive" else m.group(1)
    name = parts[-1]
    if "BasicAttack" in name or "CritAttack" in name:
        return "A"
    return "?"


def champion_scripts(key, ver, dd_ids):
    data = fetch(CDRAGON.format(ver=ver, c=key.lower()))
    abilities = {k: (v.get("mRootSpell", ""), set(v.get("mChildSpells", [])))
                 for k, v in data.items() if isinstance(v, dict) and v.get("__type") == "AbilityObject"}
    out = {}
    for k, v in data.items():
        if isinstance(v, dict) and v.get("__type") == "SpellObject":
            name = v.get("mScriptName") or k.rsplit("/", 1)[-1]
            out[name] = slot_of(k, abilities, dd_ids)
    return out


def main(ver="latest"):
    spells = json.loads((DATA / "champion_spells.json").read_text(encoding="utf-8"))["champions"]
    out = {}
    for i, (key, slots) in enumerate(sorted(spells.items())):
        dd_ids = {s: info["id"] for s, info in slots.items()}
        try:
            out[key] = champion_scripts(key, ver, dd_ids)
        except (urllib.error.URLError, ValueError) as exc:
            print(f"{key}: {exc}", file=sys.stderr)
            continue
        print(f"{i + 1}/{len(spells)} {key} {len(out[key])}", file=sys.stderr)
    shared = shared_scripts()
    OUT.write_text(json.dumps({"source": f"communitydragon/{ver}", "champions": out, "shared": shared}, ensure_ascii=False,
                              separators=(",", ":"), sort_keys=True), encoding="utf-8")
    print(f"wrote {OUT} ({len(out)} champions, {sum(len(v) for v in out.values())} scripts, {len(shared)} shared)")
    return 0


if __name__ == "__main__":
    sys.exit(main(*sys.argv[1:]))
