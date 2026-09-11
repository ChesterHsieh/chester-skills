#!/usr/bin/env python3
"""Read a League of Legends .rofl replay and print analysis (markdown by default, --json for data).

  rofl_cli.py summary   FILE                 whole-lobby scoreboard + fight timeline
  rofl_cli.py player    FILE WHO             deep-dive on one player (champion zh/en, role, name, 'red adc'...)
  rofl_cli.py timeline  FILE                 deaths / level timings / fights for everyone
  rofl_cli.py packets   FILE [--type T] [--player WHO] [--limit N] [--keyframe K]   raw packet envelope dump
  rofl_cli.py calibrate FILE                 re-detect packet types for this patch and save the profile
  rofl_cli.py meta      FILE [--keys]        raw metadata JSON
  rofl_cli.py session   FILE --focus WHO [--question TEXT]   parse once, write context.md + game.json to the cache, print context
  rofl_cli.py where     WHO TIME [--file FILE]               position / region / speed / nearest champions at TIME (mm:ss)
  rofl_cli.py near      WHO TIME [--radius R]                champions within R units at TIME
  rofl_cli.py track     WHO FROM TO [--step S]               positions every S seconds
  rofl_cli.py fight     TIME [--window W]                    everyone's position at TIME plus deaths within W seconds
  rofl_cli.py items     WHO                                  purchase order with item names, prices and gold left
  rofl_cli.py kills     [--who WHO]                          every death with killer and place (or only WHO's deaths/kills)
  rofl_cli.py casts     WHO [FROM TO]                        spell casts (Q/W/E/R, summoners, items, recall) with position, aim point and the enemy nearest it
  rofl_cli.py combos    WHO [FROM TO] [--gap S] [--all]      combos: cast bursts with sequence, timing, inferred target, flashes and result
Options: --json  --no-payload (metadata only, fast)
Follow-up commands use the current session (last `session` call) unless --file is given.
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from lolreplay import report  # noqa: E402
from lolreplay.analysis import flags_for  # noqa: E402
from lolreplay.blocks import game_blocks, keyframe_blocks  # noqa: E402
from lolreplay.calibrate import champion_net_ids, count_matrix, detect_profile, save_profile  # noqa: E402
from lolreplay.container import RoflFormatError  # noqa: E402
from lolreplay.names import resolve_player  # noqa: E402
from lolreplay.pipeline import load_session, warn  # noqa: E402
from lolreplay import workspace as wsmod  # noqa: E402
from lolreplay.positions import region_name  # noqa: E402
from lolreplay.timeline import fmt_clock  # noqa: E402
from lolreplay import combos, ddragon, spells  # noqa: E402


def cmd_summary(args) -> int:
    s = load_session(args.file, payload=not args.no_payload)
    warn(s)
    if args.json:
        flags = {str(m.index): flags_for(s.game, s.metrics, m, s.timeline) for m in s.metrics}
        print(report.to_json(s.game, s.metrics, s.timeline, flags))
    else:
        print(report.render_summary(s.game, s.metrics, s.timeline))
    return 0


def _pick(session, who: str):
    res = resolve_player(session.game, who)
    if res.ok:
        return res.player
    options = ", ".join(f"[{p.index}] {p.champion} {p.name} ({p.team} {p.position})" for p in res.candidates)
    print(f"無法唯一對應「{who}」（{res.how}）。候選：{options}", file=sys.stderr)
    return None


def cmd_player(args) -> int:
    s = load_session(args.file, payload=not args.no_payload)
    warn(s)
    player = _pick(s, args.who)
    if player is None:
        return 2
    m = s.metrics[player.index]
    flags = flags_for(s.game, s.metrics, m, s.timeline)
    if args.json:
        print(report.to_json(s.game, (m,), s.timeline, {str(m.index): flags}))
    else:
        print(report.render_player(s.game, s.metrics, m, s.timeline, flags))
    return 0


def cmd_timeline(args) -> int:
    s = load_session(args.file, payload=True)
    warn(s)
    if s.timeline is None:
        print("此檔案沒有可讀的 payload。", file=sys.stderr)
        return 1
    if args.json:
        print(report.to_json(s.game, s.metrics, s.timeline))
    else:
        print(report.render_timeline(s.game, s.metrics, s.timeline))
    return 0


def cmd_packets(args) -> int:
    s = load_session(args.file, payload=True, persist_profile=False)
    blocks = keyframe_blocks(s.rofl, args.keyframe) if args.keyframe is not None else game_blocks(s.rofl)
    net_filter = None
    if args.player:
        player = _pick(s, args.player)
        if player is None:
            return 2
        net_filter = s.net_ids[player.index] if player.index < len(s.net_ids) else None
    names = {nid: s.game.players[i].champion for i, nid in enumerate(s.net_ids)}
    shown = 0
    for b in blocks:
        if args.type is not None and b.type != args.type:
            continue
        if net_filter is not None and b.net_id != net_filter:
            continue
        who = names.get(b.net_id, f"{b.net_id:#x}")
        print(f"{b.time:9.3f}s type={b.type:4d} net={who:12s} ch={b.channel} len={len(b.content):4d} {b.content.hex()}")
        shown += 1
        if shown >= args.limit:
            break
    print(f"# shown {shown} of {len(blocks)} blocks", file=sys.stderr)
    return 0


def cmd_calibrate(args) -> int:
    s = load_session(args.file, payload=True, persist_profile=False)
    blocks = game_blocks(s.rofl)
    net_ids = champion_net_ids(blocks)
    matrix = count_matrix(blocks, net_ids)
    profile = detect_profile(s.game, matrix)
    path = save_profile(profile)
    print(json.dumps({"profile": profile.__dict__, "net_ids": [hex(n) for n in net_ids], "saved": str(path)},
                     ensure_ascii=False, indent=2))
    return 0


def cmd_meta(args) -> int:
    s = load_session(args.file, payload=False)
    meta = s.rofl.metadata()
    if args.keys:
        stats = json.loads(meta.get("statsJson", "[]"))
        print("\n".join(sorted(stats[0].keys())) if stats else "(no stats)")
    else:
        meta = dict(meta, statsJson=json.loads(meta.get("statsJson", "[]")))
        print(json.dumps(meta, ensure_ascii=False, indent=2))
    return 0


def _ws(args):
    return wsmod.load_current(getattr(args, "file", None))


def _who(ws, text):
    res = resolve_player(ws.game, text)
    if res.ok:
        return res.player
    options = ", ".join(f"[{p.index}] {p.champion} {p.name}" for p in res.candidates)
    raise SystemExit(f"無法唯一對應「{text}」（{res.how}）。候選：{options}")


def cmd_session(args) -> int:
    try:
        ws = wsmod.create_workspace(args.file, args.focus, args.question)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 2
    warn(ws.session)
    print((ws.directory / "context.md").read_text(encoding="utf-8"))
    print(f"\n[session] context: {ws.directory / 'context.md'}  data: {ws.directory / 'game.json'}", file=sys.stderr)
    return 0


def _label(ws, player):
    return f"{ddragon.champion_name(player.champion)}({player.champion})"


def cmd_where(args) -> int:
    ws = _ws(args); p = _who(ws, args.who); t = wsmod.parse_time(args.time)
    pos = wsmod.position_of(ws, p.index, t)
    if pos is None:
        print("沒有位置資料"); return 1
    track = ws.tracks[p.index]
    print(f"{fmt_clock(t)} {_label(ws, p)} 在 ({pos[0]:.0f}, {pos[1]:.0f}) {region_name(pos)}，移速 {track.speed_at(t) or 0:.0f}")
    for other, d, opos in wsmod.nearby(ws, p.index, t, radius=3000):
        side = "隊友" if other.team == p.team else "敵人"
        print(f"  {side} {_label(ws, other)} 距離 {d:.0f} 在 {region_name(opos)}")
    return 0


def cmd_near(args) -> int:
    ws = _ws(args); p = _who(ws, args.who); t = wsmod.parse_time(args.time)
    rows = wsmod.nearby(ws, p.index, t, radius=args.radius)
    print(f"{fmt_clock(t)} {_label(ws, p)} 半徑 {args.radius:.0f} 內：")
    for other, d, opos in rows:
        print(f"  {'隊友' if other.team == p.team else '敵人'} {_label(ws, other)} {d:.0f} ({opos[0]:.0f}, {opos[1]:.0f})")
    if not rows:
        print("  無")
    return 0


def cmd_track(args) -> int:
    ws = _ws(args); p = _who(ws, args.who)
    t0, t1 = wsmod.parse_time(args.start), wsmod.parse_time(args.end); t = t0
    while t <= t1:
        pos = wsmod.position_of(ws, p.index, t)
        print(f"{fmt_clock(t)} " + (f"({pos[0]:.0f}, {pos[1]:.0f}) {region_name(pos)}" if pos else "?"))
        t += args.step
    return 0


def cmd_fight(args) -> int:
    ws = _ws(args); t = wsmod.parse_time(args.time)
    print(f"{fmt_clock(t)} 全員位置：")
    for player in ws.game.players:
        pos = wsmod.position_of(ws, player.index, t)
        print(f"  {'藍' if player.team == 100 else '紅'} {_label(ws, player):26s} " + (f"({pos[0]:.0f}, {pos[1]:.0f}) {region_name(pos)}" if pos else "?"))
    tl = ws.session.timeline
    if tl:
        deaths = [e for e in tl.deaths if abs(e.time - t) <= args.window]
        print(f"±{args.window:.0f} 秒內死亡：" + (", ".join(f"{e.clock} {_label(ws, ws.game.players[e.player])}" for e in deaths) or "無"))
    return 0


def cmd_items(args) -> int:
    ws = _ws(args); p = _who(ws, args.who); tl = ws.session.timeline
    if tl is None or "items" not in tl.detail:
        print("這個版本沒有購買內容規格，只有購物時間：" + ", ".join(e.clock for e in (tl.shop_of(p.index) if tl else ())))
        return 1
    rows = tl.purchases_of(p.index)
    print(f"{_label(ws, p)} 購買順序（{len(rows)} 件）：")
    for e in rows:
        left = f"，買後剩 {e.gold_after:.0f}" if e.gold_after is not None else ""
        print(f"  {e.clock} {ddragon.item_name(e.item)}({e.item}) {e.price:.0f} 金{left}")
    return 0


def cmd_kills(args) -> int:
    ws = _ws(args); tl = ws.session.timeline
    if tl is None:
        print("沒有時間軸資料"); return 1
    who = _who(ws, args.who) if args.who else None
    for e in tl.deaths:
        if who is not None and e.player != who.index and e.killer != who.index:
            continue
        victim = _label(ws, ws.game.players[e.player])
        killer = (_label(ws, ws.game.players[e.killer]) if e.killer is not None
                  else "非英雄單位（塔／小兵／野怪）" if e.killer_netid is not None else "?")
        where = f" 於 ({e.pos[0]:.0f}, {e.pos[1]:.0f}) {region_name(e.pos)}" if e.pos else ""
        print(f"  {e.clock} {victim} 被 {killer} 擊殺{where}")
    return 0


def cmd_casts(args) -> int:
    ws = _ws(args); p = _who(ws, args.who); tl = ws.session.timeline
    if tl is None or "casts" not in tl.detail:
        print("這個版本沒有施法封包規格"); return 1
    t0 = wsmod.parse_time(args.start) if args.start else 0.0
    t1 = wsmod.parse_time(args.end) if args.end else float("inf")
    rows = tl.casts_of(p.index, t0, t1)
    enemies = tuple(o.index for o in ws.game.players if o.team != p.team)
    pos = combos.living(lambda i, t: wsmod.position_of(ws, i, t), tl.deaths)
    print(f"{_label(ws, p)} 施法 {len(rows)} 次（技能／召喚師技能／道具／回城；普攻不在施法封包裡；「瞄準」＝指向點 450 內最近的敵方英雄，由位置推定）：")
    for c in rows:
        where = f" 於 ({c.start[0]:.0f}, {c.start[1]:.0f}) {region_name(c.start)}" if c.start else ""
        aim = combos.aim_point(c)
        aim_txt = f"，指向 ({aim[0]:.0f}, {aim[1]:.0f})" if aim else ""
        hit = combos.aimed_enemy(c, enemies, pos)
        hit_txt = f"，瞄準 {_label(ws, ws.game.players[hit[0]])}（距指向點 {hit[1]:.0f}）" if hit else ""
        print(f"  {c.clock} {spells.label(c.spell_hash)}{where}{aim_txt}{hit_txt}")
    return 0


def cmd_combos(args) -> int:
    ws = _ws(args); p = _who(ws, args.who); tl = ws.session.timeline
    if tl is None or "casts" not in tl.detail:
        print("這個版本沒有施法封包規格，無法分析連招"); return 1
    t0 = wsmod.parse_time(args.start) if args.start else 0.0
    t1 = wsmod.parse_time(args.end) if args.end else float("inf")
    every = [c for c in wsmod.combos_of(ws, p.index, args.gap) if t0 <= c.start <= t1]
    shown = every if args.all else [c for c in every if c.is_fight]
    name = lambda i: _label(ws, ws.game.players[i])  # noqa: E731
    print(f"{_label(ws, p)} 連招（施法間隔 ≤{args.gap:g} 秒算一套；目標由位置推定；普攻不在施法封包裡，技能間的停頓可能是在普攻）：")
    print("  " + combos.summary_line(combos.summarize(every)))
    for c in shown:
        print("  " + combos.describe(c, name))
    if not shown:
        print("  （這段時間沒有交戰連招；加 --all 看清線／打野連段）")
    flashes = [f for f in wsmod.flashes_of(ws, p.index) if t0 <= f.time <= t1]
    if flashes:
        print("  " + "｜".join(combos.flash_text(f, name) for f in flashes))
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    sub = p.add_subparsers(dest="cmd", required=True)

    def common(sp):
        sp.add_argument("file")
        sp.add_argument("--json", action="store_true")
        sp.add_argument("--no-payload", action="store_true")

    common(sub.add_parser("summary")); sub.choices["summary"].set_defaults(fn=cmd_summary)
    sp = sub.add_parser("player"); common(sp); sp.add_argument("who"); sp.set_defaults(fn=cmd_player)
    common(sub.add_parser("timeline")); sub.choices["timeline"].set_defaults(fn=cmd_timeline)
    sp = sub.add_parser("packets"); sp.add_argument("file"); sp.add_argument("--type", type=int)
    sp.add_argument("--player"); sp.add_argument("--limit", type=int, default=50); sp.add_argument("--keyframe", type=int)
    sp.set_defaults(fn=cmd_packets)
    sp = sub.add_parser("calibrate"); sp.add_argument("file"); sp.set_defaults(fn=cmd_calibrate)
    sp = sub.add_parser("meta"); common(sp); sp.add_argument("--keys", action="store_true"); sp.set_defaults(fn=cmd_meta)
    sp = sub.add_parser("session"); sp.add_argument("file"); sp.add_argument("--focus"); sp.add_argument("--question"); sp.set_defaults(fn=cmd_session)
    for name, fn in (("where", cmd_where), ("near", cmd_near)):
        sp = sub.add_parser(name); sp.add_argument("who"); sp.add_argument("time"); sp.add_argument("--file"); sp.add_argument("--radius", type=float, default=1200.0); sp.set_defaults(fn=fn)
    sp = sub.add_parser("track"); sp.add_argument("who"); sp.add_argument("start"); sp.add_argument("end"); sp.add_argument("--step", type=float, default=10.0); sp.add_argument("--file"); sp.set_defaults(fn=cmd_track)
    sp = sub.add_parser("fight"); sp.add_argument("time"); sp.add_argument("--window", type=float, default=15.0); sp.add_argument("--file"); sp.set_defaults(fn=cmd_fight)
    sp = sub.add_parser("items"); sp.add_argument("who"); sp.add_argument("--file"); sp.set_defaults(fn=cmd_items)
    sp = sub.add_parser("kills"); sp.add_argument("--who"); sp.add_argument("--file"); sp.set_defaults(fn=cmd_kills)
    sp = sub.add_parser("casts"); sp.add_argument("who"); sp.add_argument("start", nargs="?"); sp.add_argument("end", nargs="?")
    sp.add_argument("--file"); sp.set_defaults(fn=cmd_casts)
    sp = sub.add_parser("combos"); sp.add_argument("who"); sp.add_argument("start", nargs="?"); sp.add_argument("end", nargs="?")
    sp.add_argument("--gap", type=float, default=combos.COMBO_GAP_S); sp.add_argument("--all", action="store_true")
    sp.add_argument("--file"); sp.set_defaults(fn=cmd_combos)
    return p


def main(argv=None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.fn(args)
    except RoflFormatError as exc:
        print(f"讀取失敗：{exc}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
