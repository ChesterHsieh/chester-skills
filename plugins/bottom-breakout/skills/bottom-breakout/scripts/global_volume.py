#!/usr/bin/env python3
"""跨市場全球量能：把 ADR 和原股的日成交量換成同一個單位，算全球相對量（RVOL）。

為什麼需要：書裡的量能規則假設一檔股票只在一個市場交易。SK Hynix 這類 ADR，
買賣壓力分散在原股、ADR 正常盤、盤前盤後與夜盤；只看其中一個，會把「量移到別的時段」
誤判成「沒有量」。原則見 references/global-volume.md。

用法：
    python3 global_volume.py --adr adr.json --home krx.json --k 0.1
    python3 global_volume.py --adr adr.json --home krx.json --k 0.1 --json

--k  ：1 股 ADR 等於幾股原股，用官方換股比例（SKHY：10 ADR = 1 股 → 0.1）
--fx ：備案。查不到官方比例時，用最近 10 天「ADR 收盤 × 匯率 ÷ 原股收盤」的中位數反推 k
只用 Python 標準函式庫。
"""
from __future__ import annotations

import argparse
import json
import statistics
import sys
from dataclasses import dataclass


@dataclass
class GParams:
    window: int = 20       # 相對量基準天數
    min_bars: int = 10     # 基準不足時至少幾天
    strong: float = 1.2    # 帶量
    weak: float = 0.8      # 無量
    event: float = 1.5     # 事件日帶量門檻
    show: int = 10         # 輸出最近幾天
    updown: int = 10       # 上漲日／下跌日比較天數


def load(path):
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    if "bars" in raw:
        return {b["date"][:10]: b for b in raw["bars"]}
    return {
        str(raw["time"][i])[:10]: {"close": float(raw["close"][i]), "volume": float(raw["volume"][i])}
        for i in range(len(raw["close"]))
    }


def estimate_k(adr, home, fx, last=10):
    """備案：沒有官方換股比例時，用最近 `last` 個重疊日反推。

    只能用最近幾天：fx 是「現在」的匯率，套到幾個月前的收盤會把匯率變動算成 k 的變動。
    也要先確認 fx 本身可信——2026-10 用一個預測網站的匯率反推出 0.146，
    官方比例其實是 0.1（差 46%），量能權重整個歪掉。
    """
    common = sorted(set(adr) & set(home))[-last:]
    ks = [adr[d]["close"] * fx / home[d]["close"] for d in common if home[d]["close"]]
    if len(ks) < 5:
        raise ValueError("ADR 和原股重疊的交易日少於 5 天，沒辦法估 k；請直接給 --k")
    return statistics.median(ks), len(ks)


def compute(adr, home, k, p: GParams | None = None, event_last=False):
    p = p or GParams()
    dates = sorted(set(adr) | set(home))
    g = {d: home.get(d, {}).get("volume", 0.0) + k * adr.get(d, {}).get("volume", 0.0) for d in dates}

    def rv(series, d):
        prev = [series[x]["volume"] if isinstance(series[x], dict) else series[x]
                for x in dates if x < d and x in series][-p.window:]
        if len(prev) < p.min_bars or d not in series:
            return None
        avg = sum(prev) / len(prev)
        cur = series[d]["volume"] if isinstance(series[d], dict) else series[d]
        return cur / avg if avg else None

    def direction(d):
        for src in (adr, home):
            ds = [x for x in dates if x in src and x <= d]
            if len(ds) >= 2 and ds[-1] == d:
                return "漲" if src[d]["close"] > src[ds[-2]]["close"] else "跌"
        return "?"

    rows = []
    for d in dates:
        r = rv(g, d)
        strong = p.event if (event_last and d == dates[-1]) else p.strong
        label = "資料不足" if r is None else ("帶量" if r >= strong else "無量" if r < p.weak else "中性")
        rows.append({"date": d, "dir": direction(d), "home_rvol": rv(home, d), "adr_rvol": rv(adr, d),
                     "global_rvol": r, "label": label})
    recent = [r for r in rows[-p.updown:] if r["global_rvol"] is not None]
    up = [r["global_rvol"] for r in recent if r["dir"] == "漲"]
    dn = [r["global_rvol"] for r in recent if r["dir"] == "跌"]
    common = [d for d in dates if d in adr and d in home][-p.window:]
    adr_sh = sum(k * adr[d]["volume"] for d in common)
    tot = adr_sh + sum(home[d]["volume"] for d in common)
    return {
        "k": k,
        "adr_share": adr_sh / tot if tot else None,
        "rows": rows[-p.show:],
        "up_avg": sum(up) / len(up) if up else None,
        "down_avg": sum(dn) / len(dn) if dn else None,
    }


def render(res, p: GParams):
    f = lambda x: "—" if x is None else f"{x:.2f}"
    out = [f"k = {res['k']:.4f} 股原股／ADR；最近 {p.window} 個共同交易日，ADR 佔全球成交（換算成原股股數）約 "
           f"{res['adr_share']:.0%}", "",
           "| 日期 | 方向 | 原股 RVOL | ADR RVOL | 全球 RVOL | 判讀 |", "|---|---|---|---|---|---|"]
    for r in res["rows"]:
        out.append(f"| {r['date']} | {r['dir']} | {f(r['home_rvol'])} | {f(r['adr_rvol'])} | "
                   f"**{f(r['global_rvol'])}** | {r['label']} |")
    out.append("")
    out.append(f"最近 {p.updown} 天：上漲日平均全球 RVOL {f(res['up_avg'])}，下跌日 {f(res['down_avg'])}"
               "（上漲日要大於下跌日，p.162）")
    out.append(f"門檻：≥{p.strong} 帶量、<{p.weak} 無量、中間中性（事件日 ≥{p.event}）")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--adr", required=True)
    ap.add_argument("--home", required=True)
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--k", type=float)
    g.add_argument("--fx", type=float, help="1 美元 = 多少原股幣別")
    ap.add_argument("--event", action="store_true", help="最新一天是財報等事件日")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    adr, home = load(a.adr), load(a.home)
    k = a.k
    if k is None:
        k, n = estimate_k(adr, home, a.fx)
        print(f"（用 {n} 個重疊交易日估出 k，誤差主要來自兩地收盤時間不同）", file=sys.stderr)
    p = GParams()
    res = compute(adr, home, k, p, a.event)
    print(json.dumps(res, ensure_ascii=False, indent=1) if a.json else render(res, p))


if __name__ == "__main__":
    sys.exit(main())
