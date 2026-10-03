#!/usr/bin/env python3
"""台股「穩定支撐區 → 均線糾結 → 突破」型態粗掃。

用法:
    python3 scan.py 2330                          # 單檔，完整明細
    python3 scan.py 2330 5483 3673 2409 8299      # 多檔，摘要表（依符合度排序）
    python3 scan.py --file codes.txt              # 代碼清單檔（空白／逗號／換行分隔，# 後為註解）
    python3 scan.py --universe ssf                # 全部個股期貨標的（約 300 檔）
    python3 scan.py 2330 2317 --futures-only      # 只留有個股期貨的
    python3 scan.py ... --only entry              # 只列貼近底線（進場區）的
    python3 scan.py 5483 --asof 2026-09-16        # 回測：假裝今天是那天
"""
import argparse
import json
import re
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
from fetch import fetch_series  # noqa: E402
from futures import describe, load_contracts  # noqa: E402
from market import fetch_many  # noqa: E402
from pattern import analyze, score  # noqa: E402

VERDICT = {"yes": "✅ 符合", "partial": "🟡 部分", "no": "❌ 不符", "insufficient": "⚪ 資料不足", "error": "⚠️ 錯誤"}
KIND_MARK = {"required": "＊", "core": "  ", "bonus": "＋", "info": "ℹ "}
BULK_THRESHOLD = 8   # 超過這個檔數改用全市場逐日抓，避免逐檔請求被證交所擋
DETAIL_MAX = 3       # 這個檔數以內預設印完整明細


def _pct(x, sign=False):
    return "n/a" if x is None else (f"{x:+.1%}" if sign else f"{x:.1%}")


def parse_codes(tokens):
    out = []
    for t in tokens:
        for c in re.split(r"[\s,，、]+", re.sub(r"#.*", "", t)):
            if c and c not in out:
                out.append(c)
    return out


def load_series(codes, asof, log):
    if len(codes) <= BULK_THRESHOLD:
        out, errors = {}, {}
        for c in codes:
            try:
                out[c] = fetch_series(c, months=9, today=asof)
            except Exception as e:
                errors[c] = str(e)
        return out, errors
    log(f"共 {len(codes)} 檔，改用全市場逐日行情（第一次會花幾分鐘建快取，之後每天只補當天）")
    got = fetch_many(codes, today=asof, progress=log)
    return got, {c: "上市、上櫃日行情都找不到這個代碼" for c in codes if c not in got}


def render_detail(r):
    lines = [f"━━ {r['code']} {r.get('name', '')}（{r.get('market', '')}）  {VERDICT[r['match']]}"
             + ("  🎯 進場區" if r.get("entry_zone") else "")]
    if "base" not in r:
        lines.append(f"   {r['reason']}")
        return "\n".join(lines)
    b, lv, last, m = r["base"], r["levels"], r["last"], r["ma_now"]
    lines += [
        f"   {last['day']} 收 {last['close']}  →  {r['status_text']}",
        f"   支撐區 {b['start']} ~ {b['end']}（{b['bars']} 日，振幅 {_pct(b['band'])}）",
        f"   底線 {b['floor']}（距今 {_pct(lv['dist_to_floor'], True)}）｜上緣 {b['ceiling']}"
        f"（距今 {_pct(lv['dist_to_ceiling'], True)}）｜失效參考 {lv['stop_ref']}",
        f"   MA5 {m['ma5']} / MA20 {m['ma20']} / MA60 {m['ma60']} / MA120 {m['ma120']}",
    ]
    if "futures" in r:
        lines.append(f"   個股期貨：{r['futures']}")
    for c in r["checks"]:
        lines.append(f"   {'✓' if c['ok'] else '✗'}{KIND_MARK[c['kind']]}{c['label']}：{c['detail']}")
    return "\n".join(lines)


def render_table(results, with_futures):
    head = "| 代碼 | 名稱 | 判定 | 位置 | 收盤 | 底線 | 距底線 | 上緣 | 核心未過 |" + (" 個股期貨 |" if with_futures else "")
    sep = "|" + "---|" * (9 + with_futures)
    rows = [head, sep]
    for r in results:
        if "base" not in r:
            cells = [r["code"], r.get("name", ""), VERDICT[r["match"]], r.get("reason", ""), "", "", "", "", ""]
        else:
            miss = "、".join(c["label"] for c in r["checks"] if c["kind"] == "core" and not c["ok"]) or "—"
            pos = ("🎯 " if r["entry_zone"] else "") + r["status_text"]
            cells = [r["code"], r["name"], VERDICT[r["match"]], pos, f"{r['last']['close']:g}",
                     f"{r['base']['floor']:g}", _pct(r["levels"]["dist_to_floor"], True),
                     f"{r['base']['ceiling']:g}", miss]
        if with_futures:
            cells.append(r.get("futures", ""))
        rows.append("| " + " | ".join(cells) + " |")
    return "\n".join(rows)


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("codes", nargs="*", help="股票代碼，例如 2330 5483 或 2330,5483")
    ap.add_argument("--file", type=Path, help="代碼清單檔")
    ap.add_argument("--universe", choices=["ssf"], help="ssf = 全部個股期貨標的")
    ap.add_argument("--futures-only", action="store_true", help="只保留有個股期貨的股票")
    ap.add_argument("--no-futures", action="store_true", help="不查個股期貨資訊")
    ap.add_argument("--only", choices=["entry", "match"], help="entry=只列進場區；match=只列符合／部分符合")
    ap.add_argument("--detail", action="store_true", help="多檔時也印完整明細")
    ap.add_argument("--asof", type=date.fromisoformat, default=date.today(), help="回測日期 YYYY-MM-DD")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args()
    log = lambda s: print(f"[scan] {s}", file=sys.stderr)  # noqa: E731

    codes = parse_codes(a.codes + ([a.file.read_text()] if a.file else []))
    need_futures = not a.no_futures or a.futures_only or a.universe == "ssf"
    contracts = {}
    if need_futures:
        try:
            contracts = load_contracts()
        except Exception as e:
            if a.futures_only or a.universe:
                sys.exit(f"抓期交所個股期貨清單失敗：{e}")
            log(f"抓期交所個股期貨清單失敗，略過期貨欄位：{e}")
    if a.universe == "ssf":
        codes = codes + [c for c in sorted(contracts) if c not in codes]
    if a.futures_only:
        dropped = [c for c in codes if c not in contracts]
        codes = [c for c in codes if c in contracts]
        if dropped:
            log(f"沒有個股期貨、已排除：{' '.join(dropped)}")
    if not codes:
        sys.exit("沒有可掃的代碼")

    series, errors = load_series(codes, a.asof, log)
    results = []
    for c in codes:
        if c in errors:
            results.append({"code": c, "match": "error", "reason": errors[c]})
            continue
        s = series[c]
        r = analyze([b for b in s.bars if b.day <= a.asof])
        r = {"code": c, "name": s.name, "market": s.market, **r}
        if contracts:
            r["futures"] = describe(contracts.get(c, []), r["last"]["close"]) if "last" in r else ""
        results.append(r)

    results.sort(key=score)
    n_yes = sum(r["match"] == "yes" for r in results)  # 篩選前計數，摘要才不會被 --only 吃掉
    n_entry = sum(bool(r.get("entry_zone")) for r in results)
    n_both = sum(r["match"] == "yes" and bool(r.get("entry_zone")) for r in results)
    if a.only == "entry":
        results = [r for r in results if r.get("entry_zone")]
    elif a.only == "match":
        results = [r for r in results if r["match"] in ("yes", "partial")]

    if a.json:
        print(json.dumps(results, ensure_ascii=False, indent=2, default=str))
        return
    print(f"掃描 {len(codes)} 檔（{a.asof}）：符合 {n_yes}、進場區 {n_entry}（其中符合 {n_both}），"
          f"列出 {len(results)} 檔\n")
    if a.detail or len(results) <= DETAIL_MAX:
        print("\n\n".join(render_detail(r) for r in results))
    else:
        print(render_table(results, bool(contracts)))


if __name__ == "__main__":
    main()
