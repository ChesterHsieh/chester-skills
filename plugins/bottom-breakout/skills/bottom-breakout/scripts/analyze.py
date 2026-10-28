#!/usr/bin/env python3
"""底翻突破判讀：讀 IBKR get_price_history 的日線 JSON，輸出底部確認檢查表。

規則來自 Murphy《Technical Analysis of the Financial Markets》（1999），
頁碼對照見 references/rules.md。只用 Python 標準函式庫。

用法：
    python3 analyze.py bars.json                 # 文字報告
    python3 analyze.py bars.json --json          # 機器可讀
    python3 analyze.py bars.json --event         # 今天是財報等事件日（量能門檻提高）
"""
from __future__ import annotations

import argparse
import json
import sys
from dataclasses import asdict, dataclass, field


@dataclass
class Params:
    peak_lookback: int = 120        # 在最近 N 根 K 棒裡找波段高點
    base_lookback: int = 250        # 高點之前 N 根找這段漲勢的起點
    filter_pct: float = 0.01        # 突破的價格過濾 1%（p.122）；停損也放在支撐下 1%
    break_pct: float = 0.03         # 有效跌破 3%（p.64）；第二隻腳比第一隻低不超過 3% 仍算破底翻
    min_rally_pct: float = 0.03     # 兩隻腳之間的反彈至少 3%，才算 W 而不是雜訊
    vol_window: int = 20            # 相對量（RVOL）基準天數
    vol_min_bars: int = 10          # 基準天數不足時至少要幾根才計算
    vol_strong: float = 1.2         # ≥ 1.2 倍 = 帶量
    vol_weak: float = 0.8           # < 0.8 倍 = 無量；中間是中性
    event_vol: float = 1.5          # 財報等事件日：帶量門檻提高
    pullback_max_retr: float = 0.66 # 回吐整段漲幅 ≤ 2/3 = 上升趨勢中的修正（p.86）
    reversal_lookback: int = 10     # 底部反轉日：最低價要低於前 N 根
    reversal_scan: int = 15         # 只檢查最近 N 根有沒有反轉日
    updown_window: int = 10         # 上漲日／下跌日量比較的天數
    tight_stop_pct: float = 0.02    # 突破後的緊停損：突破點下方 2%（p.401）
    rr_min: float = 3.0             # 報酬風險比門檻（p.398）
    max_hurdles: int = 3            # 列出幾道「下降中的反彈高點」


# ---------------------------------------------------------------- 讀資料

def load(path: str) -> tuple[str, list[dict]]:
    with open(path, encoding="utf-8") as f:
        raw = json.load(f)
    symbol = raw.get("symbol", "") if isinstance(raw, dict) else ""
    if isinstance(raw, dict) and "bars" in raw:
        bars = raw["bars"]
    elif isinstance(raw, dict) and "close" in raw:
        n = len(raw["close"])
        bars = [
            {
                "date": str(raw["time"][i])[:10],
                "open": float(raw["open"][i]),
                "high": float(raw["high"][i]),
                "low": float(raw["low"][i]),
                "close": float(raw["close"][i]),
                "volume": float(raw["volume"][i]),
            }
            for i in range(n)
        ]
    else:
        raise ValueError("看不懂的格式：要嘛是 IBKR get_price_history 的輸出，要嘛是 {bars:[...]}")
    if len(bars) < 30:
        raise ValueError(f"只有 {len(bars)} 根 K 棒，至少要 30 根（建議抓一年日線）")
    return symbol, bars


# ---------------------------------------------------------------- 工具

def rvol(bars: list[dict], i: int, p: Params) -> float | None:
    prev = [b["volume"] for b in bars[max(0, i - p.vol_window):i]]
    if len(prev) < p.vol_min_bars:
        return None
    avg = sum(prev) / len(prev)
    return bars[i]["volume"] / avg if avg else None


def is_pivot_low(bars, j):
    return 0 < j < len(bars) - 1 and bars[j]["low"] < bars[j - 1]["low"] and bars[j]["low"] < bars[j + 1]["low"]


def is_pivot_high(bars, j):
    return 0 < j < len(bars) - 1 and bars[j]["high"] > bars[j - 1]["high"] and bars[j]["high"] > bars[j + 1]["high"]


def argmax_high(bars, a, b):
    return max(range(a, b + 1), key=lambda k: bars[k]["high"])


def argmin_low(bars, a, b):
    return min(range(a, b + 1), key=lambda k: bars[k]["low"])


def vol_label(r: float | None, p: Params, event: bool) -> str:
    if r is None:
        return "資料不足"
    strong = p.event_vol if event else p.vol_strong
    if r >= strong:
        return "帶量"
    if r < p.vol_weak:
        return "無量"
    return "中性"


# ---------------------------------------------------------------- 主判讀

@dataclass
class Point:
    date: str
    price: float
    index: int


@dataclass
class Report:
    symbol: str
    last: Point
    mode: str = ""
    mode_note: str = ""
    peak: Point | None = None
    base: Point | None = None
    decline_pct: float | None = None
    retr_of_upleg: float | None = None
    stage: int = 0
    stage_text: str = ""
    pattern: str = ""
    one: Point | None = None
    x: Point | None = None
    x_provisional: Point | None = None
    three: Point | None = None
    undercut_pct: float | None = None
    hurdles: list = field(default_factory=list)
    breakout_raw: Point | None = None
    breakout_filtered: Point | None = None
    two_day: bool = False
    above_x_now: bool | None = None
    volume: dict = field(default_factory=dict)
    reversal_days: list = field(default_factory=list)
    gaps: list = field(default_factory=list)
    levels: dict = field(default_factory=dict)
    rr: list = field(default_factory=list)
    warnings: list = field(default_factory=list)


def pt(bars, i, key) -> Point:
    return Point(bars[i]["date"], bars[i][key], i)


def analyze(bars: list[dict], p: Params | None = None, symbol: str = "", event: bool = False) -> Report:
    p = p or Params()
    n = len(bars)
    last_i = n - 1
    rep = Report(symbol=symbol, last=pt(bars, last_i, "close"))

    # 1. 波段高點、之後的最低點、這段漲勢的起點
    w0 = max(0, n - p.peak_lookback)
    peak_i = argmax_high(bars, w0, last_i)
    rep.peak = pt(bars, peak_i, "high")
    if peak_i == last_i:
        rep.mode = "new_high"
        rep.stage_text = "今天就是波段新高，沒有底部結構可判讀"
        return rep
    low_i = argmin_low(bars, peak_i + 1, last_i)
    L = bars[low_i]["low"]
    rep.decline_pct = (rep.peak.price - L) / rep.peak.price
    b0 = max(0, peak_i - p.base_lookback)
    if peak_i > b0:
        base_i = argmin_low(bars, b0, peak_i - 1)
        rep.base = pt(bars, base_i, "low")
        if rep.peak.price > rep.base.price:
            rep.retr_of_upleg = (rep.peak.price - L) / (rep.peak.price - rep.base.price)
    if rep.retr_of_upleg is not None and rep.retr_of_upleg <= p.pullback_max_retr:
        rep.mode = "pullback"
        rep.mode_note = (f"回吐這段漲幅 {rep.retr_of_upleg:.0%}，在 2/3 以內：上升趨勢中的修正（p.85–86）。"
                         "33–50% 是常見買進參考區（p.402）")
    else:
        rep.mode = "bottoming"
        rep.mode_note = ("回吐超過 2/3 或資料不足以看到漲勢起點：當成下跌後打底判讀。"
                         "底部需要明顯放大的買盤（p.110）")

    # 2. 下降過程中的反彈高點（第一關、第二關…）
    # 3. ①②③：先看是不是破底翻（低點 L 是第二隻腳），否則 L 就是 ①
    one_i = two_i = three_i = None
    for j in range(low_i - 2, peak_i, -1):
        if not is_pivot_low(bars, j):
            continue
        lj = bars[j]["low"]
        if lj <= L * (1 + p.break_pct):
            xi = argmax_high(bars, j + 1, low_i - 1)
            if bars[xi]["high"] >= max(lj, L) * (1 + p.min_rally_pct):
                one_i, two_i, three_i = j, xi, low_i
                rep.pattern = "破底翻（第二隻腳略低於第一隻）" if L < lj else "雙重底（第二隻腳不破第一隻）"
                rep.undercut_pct = (lj - L) / lj
            break
    pullback_start = None
    if one_i is None:
        one_i = low_i
        rep.pattern = "單一低點，看反彈結構"
        for k in range(low_i + 1, n):
            if k > low_i + 1 and bars[k]["close"] < bars[k - 1]["close"]:
                pullback_start = k
                two_i = argmax_high(bars, low_i + 1, k)
                break
        if two_i is None and low_i < last_i:
            rep.x_provisional = pt(bars, argmax_high(bars, low_i + 1, last_i), "high")

    rep.one = pt(bars, one_i, "low")
    # 反彈高點清單：①之前、高點之後的 pivot high，從近到遠取越來越高的
    hurdles = []
    for j in range(one_i - 1, peak_i, -1):
        if is_pivot_high(bars, j) and (not hurdles or bars[j]["high"] > hurdles[-1].price):
            hurdles.append(pt(bars, j, "high"))
            if len(hurdles) >= p.max_hurdles:
                break
    rep.hurdles = hurdles

    if two_i is not None:
        X = bars[two_i]["high"]
        rep.x = pt(bars, two_i, "high")
        start = three_i if three_i is not None else pullback_start
        raw = next((m for m in range(start, n) if bars[m]["close"] > X), None)
        if three_i is None:
            three_i = argmin_low(bars, pullback_start, raw if raw is not None else last_i)
        rep.three = pt(bars, three_i, "low")
        if raw is not None:
            rep.breakout_raw = pt(bars, raw, "close")
            filt = next((m for m in range(raw, n) if bars[m]["close"] > X * (1 + p.filter_pct)), None)
            if filt is not None:
                rep.breakout_filtered = pt(bars, filt, "close")
            rep.two_day = any(bars[m]["close"] > X and bars[m + 1]["close"] > X for m in range(raw, n - 1))
        rep.above_x_now = bars[last_i]["close"] > X

    # 4. 階段
    if rep.x is None:
        if one_i == last_i:
            rep.stage, rep.stage_text = 0, "今天還在創新低，底部未成形"
        else:
            rep.stage, rep.stage_text = 1, "① 已出現，② 形成中（反彈還沒回測）"
    elif rep.breakout_raw is None:
        rep.stage, rep.stage_text = 3, "③ 回測守住，等收盤突破 X"
    else:
        rep.stage = 4
        ok = rep.breakout_filtered is not None and rep.two_day
        rep.stage_text = "④ 已突破 X（價格過濾＋兩日規則都過）" if ok else "④ 收盤站上 X，但過濾條件還沒全過"
        if not rep.above_x_now:
            rep.warnings.append("突破後又收回 X 之下：突破失敗的前兆（p.122 的反向）")

    # 5. 量
    def vol_point(i, name):
        r = rvol(bars, i, p)
        d = "漲" if i > 0 and bars[i]["close"] > bars[i - 1]["close"] else "跌"
        return {"name": name, "date": bars[i]["date"], "index": i, "volume": bars[i]["volume"], "rvol": r,
                "dir": d, "label": vol_label(r, p, event and i == last_i)}

    keys = [vol_point(one_i, "①")]
    if rep.three and rep.three.index != one_i:
        keys.append(vol_point(rep.three.index, "③"))
    if rep.breakout_raw:
        keys.append(vol_point(rep.breakout_raw.index, "④ 突破日"))
    if last_i not in [k_["index"] for k_ in keys]:
        keys.append(vol_point(last_i, "最新"))
    seg = range(max(1, n - p.updown_window), n)
    up = [bars[i]["volume"] for i in seg if bars[i]["close"] > bars[i - 1]["close"]]
    dn = [bars[i]["volume"] for i in seg if bars[i]["close"] < bars[i - 1]["close"]]
    obv = 0.0
    for i in range(one_i + 1, n):
        if bars[i]["close"] > bars[i - 1]["close"]:
            obv += bars[i]["volume"]
        elif bars[i]["close"] < bars[i - 1]["close"]:
            obv -= bars[i]["volume"]
    base_vol = sum(b["volume"] for b in bars[max(0, one_i - p.vol_window):one_i]) / max(1, min(p.vol_window, one_i))
    rep.volume = {
        "key_days": keys,
        "up_avg": sum(up) / len(up) if up else None,
        "down_avg": sum(dn) / len(dn) if dn else None,
        "up_down_ratio": (sum(up) / len(up)) / (sum(dn) / len(dn)) if up and dn else None,
        "obv_since_one_in_days": obv / base_vol if base_vol else None,
        "event": event,
    }

    # 6. 底部反轉日（p.91–92）
    for i in range(max(p.reversal_lookback, n - p.reversal_scan), n):
        prior_low = min(b["low"] for b in bars[i - p.reversal_lookback:i])
        if bars[i]["low"] < prior_low and bars[i]["close"] > bars[i - 1]["close"]:
            rep.reversal_days.append({
                "date": bars[i]["date"],
                "outside_day": bars[i]["high"] > bars[i - 1]["high"] and bars[i]["low"] < bars[i - 1]["low"],
                "range_pct": (bars[i]["high"] - bars[i]["low"]) / bars[i - 1]["close"],
                "rvol": rvol(bars, i, p),
            })

    # 7. ① 之後的向上缺口（p.94）
    for i in range(one_i + 1, n):
        if bars[i]["low"] > bars[i - 1]["high"]:
            lo, hi = bars[i - 1]["high"], bars[i]["low"]
            after = bars[i + 1:]
            if any(b["close"] < lo for b in after):
                status = "收盤跌回缺口下（轉弱訊號）"
            elif any(b["low"] <= lo for b in after):
                status = "盤中回補但收盤守住"
            else:
                status = "未回補"
            rep.gaps.append({"date": bars[i]["date"], "from": lo, "to": hi, "status": status,
                             "rvol": rvol(bars, i, p)})

    # 8. 價位
    pk = rep.peak.price
    lv = {f"retr_{int(r * 100)}": L + (pk - L) * r for r in (0.33, 0.38, 0.5, 0.62, 0.66)}
    lv["peak"] = pk
    bottom = min(rep.one.price, rep.three.price) if rep.three else rep.one.price
    if rep.x:
        lv["measured"] = rep.x.price + (rep.x.price - bottom)
    rep.levels = lv

    # 9. 報酬風險比（p.398、p.401）
    last_close = bars[last_i]["close"]
    entries = [("現價", last_close)]
    if rep.x and last_close > rep.x.price * (1 + p.filter_pct / 2):
        entries.append(("拉回到 X", rep.x.price))
    stops = [("結構停損（最近低點下 1%）", bottom * (1 - p.filter_pct))]
    if rep.x and rep.breakout_raw:
        stops.append(("緊停損（X 下 2%）", rep.x.price * (1 - p.tight_stop_pct)))
    targets = [("33% 反彈", lv["retr_33"]), ("50% 反彈", lv["retr_50"]), ("66% 反彈", lv["retr_66"]), ("前高", pk)]
    if "measured" in lv:
        targets.append(("型態測量目標", lv["measured"]))
    for h in rep.hurdles:
        targets.append((f"反彈高點 {h.date}", h.price))
    targets.sort(key=lambda t_: t_[1])
    for en, e in entries:
        for sn, s in stops:
            if s >= e:
                continue
            for tn, t in targets:
                if t <= e:
                    continue
                ratio = (t - e) / (e - s)
                rep.rr.append({"entry": en, "entry_px": e, "stop": sn, "stop_px": s, "target": tn,
                               "target_px": t, "ratio": ratio, "ok": ratio >= p.rr_min})

    if rep.mode == "bottoming" and rep.stage >= 4:
        bo = next((k_ for k_ in rep.volume["key_days"] if k_["name"] == "④ 突破日"), None)
        if bo and bo["label"] != "帶量":
            rep.warnings.append(f"突破日相對量 {bo['rvol']:.2f}（{bo['label']}）：底部突破需要量（p.110），"
                                "用兩日規則或等後續補量；跨市場掛牌的股票改看全球量（global_volume.py）")
    return rep


# ---------------------------------------------------------------- 輸出

def fmt(x: float | None) -> str:
    if x is None:
        return "—"
    if abs(x) >= 10000:
        return f"{x:,.0f}"
    return f"{x:,.2f}"


def render(rep: Report, p: Params) -> str:
    out = []
    out.append(f"# {rep.symbol or '標的'}  最新收盤 {fmt(rep.last.price)}（{rep.last.date}）")
    if rep.mode == "new_high":
        out.append(rep.stage_text)
        return "\n".join(out)
    out.append(f"模式：{'上升趨勢中的回檔' if rep.mode == 'pullback' else '下跌後打底'} — {rep.mode_note}")
    base = f"，起點 {fmt(rep.base.price)}（{rep.base.date}）" if rep.base else ""
    out.append(f"波段高點 {fmt(rep.peak.price)}（{rep.peak.date}）{base}，跌幅 {rep.decline_pct:.1%}")
    out.append("")
    out.append(f"## 階段：{rep.stage_text}")
    out.append(f"型態：{rep.pattern}" + (f"，第二隻腳低於第一隻 {rep.undercut_pct:.1%}"
                                       if rep.undercut_pct and rep.undercut_pct > 0 else ""))
    out.append(f"① {fmt(rep.one.price)}（{rep.one.date}）")
    if rep.x:
        out.append(f"② X = {fmt(rep.x.price)}（{rep.x.date}），過濾價 {fmt(rep.x.price * (1 + p.filter_pct))}")
    elif rep.x_provisional:
        out.append(f"② 形成中：① 之後最高 {fmt(rep.x_provisional.price)}（{rep.x_provisional.date}），還沒出現回測")
    if rep.three and (rep.three.index != rep.one.index):
        out.append(f"③ {fmt(rep.three.price)}（{rep.three.date}）")
    if rep.breakout_raw:
        out.append(f"④ 收盤站上 X：{rep.breakout_raw.date}；過 1% 過濾："
                   f"{rep.breakout_filtered.date if rep.breakout_filtered else '還沒'}；兩日規則：{'過' if rep.two_day else '還沒'}")
    if rep.hurdles:
        hs = "、".join(f"{fmt(h.price)}（{h.date}）" for h in rep.hurdles)
        out.append(f"下降過程的反彈高點（由近到遠，第一關在最前）：{hs}")
    out.append("")
    v = rep.volume
    out.append("## 量" + ("（事件日：帶量門檻提高到 %.1f 倍）" % p.event_vol if v.get("event") else ""))
    for k_ in v["key_days"]:
        out.append(f"- {k_['name']} {k_['date']} {k_['dir']}：{fmt(k_['volume'])}，相對量 {fmt(k_['rvol'])}（{k_['label']}）")
    if v["up_down_ratio"] is not None:
        out.append(f"- 最近 {p.updown_window} 天上漲日均量 / 下跌日均量 = {v['up_down_ratio']:.2f}"
                   f"（>1 代表買盤佔上風，p.162）")
    if v["obv_since_one_in_days"] is not None:
        out.append(f"- ① 以來 OBV 淨增 {v['obv_since_one_in_days']:+.1f} 天均量（方向要跟價格一致，p.165）")
    if rep.reversal_days:
        out.append("")
        out.append("## 底部反轉日（盤中創新低、收盤比前一天高，p.91）")
        for r in rep.reversal_days:
            out.append(f"- {r['date']}：震幅 {r['range_pct']:.1%}，相對量 {fmt(r['rvol'])}"
                       + ("，外側日" if r["outside_day"] else ""))
    if rep.gaps:
        out.append("")
        out.append("## ① 之後的向上缺口（p.94）")
        for g in rep.gaps:
            out.append(f"- {g['date']}：{fmt(g['from'])} → {fmt(g['to'])}，{g['status']}，相對量 {fmt(g['rvol'])}")
    out.append("")
    lv = rep.levels
    out.append("## 價位")
    out.append(f"- 下跌段的反彈位置：33% {fmt(lv['retr_33'])}／50% {fmt(lv['retr_50'])}／"
               f"62% {fmt(lv['retr_62'])}／66% {fmt(lv['retr_66'])}；前高 {fmt(lv['peak'])}")
    if "measured" in lv:
        out.append(f"- 型態測量目標（X 加上型態高度）：{fmt(lv['measured'])}")
    out.append("")
    out.append(f"## 報酬風險比（門檻 {p.rr_min:.0f}:1，p.398）")
    seen = set()
    for r in rep.rr:
        key = (r["entry"], r["stop"])
        if key not in seen:
            seen.add(key)
            out.append(f"- 進場 {r['entry']} {fmt(r['entry_px'])}，{r['stop']} {fmt(r['stop_px'])}：")
        out.append(f"    → {r['target']} {fmt(r['target_px'])}：{r['ratio']:.1f}:1 {'✓' if r['ok'] else ''}")
    if rep.warnings:
        out.append("")
        out.append("## 注意")
        for w in rep.warnings:
            out.append(f"- {w}")
    return "\n".join(out)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="IBKR get_price_history 輸出的 JSON（日線，建議一年）")
    ap.add_argument("--symbol", default="")
    ap.add_argument("--event", action="store_true", help="最新一根是財報等事件日")
    ap.add_argument("--json", action="store_true")
    a = ap.parse_args(argv)
    p = Params()
    sym, bars = load(a.file)
    rep = analyze(bars, p, a.symbol or sym, a.event)
    if a.json:
        print(json.dumps(asdict(rep), ensure_ascii=False, indent=1))
    else:
        print(render(rep, p))


if __name__ == "__main__":
    sys.exit(main())
