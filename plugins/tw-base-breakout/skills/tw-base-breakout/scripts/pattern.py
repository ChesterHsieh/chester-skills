"""「穩定支撐區（平台整理）→ 均線糾結 → 突破」型態判讀。純函式，不碰網路。

輸入是依日期排序的 Bar 序列（見 fetch.py），輸出一個 dict，給 scan.py 排版。
所有門檻集中在 PARAMS，校正依據見 references/pattern.md。
"""
from dataclasses import dataclass, asdict


@dataclass(frozen=True)
class Params:
    min_base_bars: int = 10        # 平台至少幾根 K（<20 會標「平台尚短」）
    mature_base_bars: int = 20
    max_band: float = 0.16         # 平台振幅上限（收盤高低差 / 最低收盤）
    min_band: float = 0.06         # 平台振幅下限（低波動股也至少給這麼寬）
    band_tr_mult: float = 5.0      # 振幅門檻 = 近 60 日真實波幅中位數 × 這個倍數，再夾在上下限之間
    anchor_lookback: int = 90      # 往回幾根找前波低點（平台必須在它之後）
    max_breakout_age: int = 15     # 平台結束後最多回看幾根（找已突破的型態）
    min_crash: float = 0.10        # 平台前的急跌幅度（只當參考資訊，不計分）
    crash_lookback: int = 40       # 從急跌低點往前找高點的範圍
    ma_tight: float = 0.03         # MA5/20/60 糾結：(max-min)/收盤
    ma_ok: float = 0.06
    ma120_slope_bars: int = 20
    vol_contract: float = 0.75     # 平台均量 / 平台前 20 日均量
    breakout_vol: float = 1.5      # 突破日量 / 平台均量
    min_touches: int = 3           # 支撐區至少被測試幾次
    max_floor_drift: float = 0.03  # 後半段底線最多比前半段低多少（越墊越低就不是支撐）
    near_floor: float = 0.04       # 收盤距底線幾 % 內算「底線附近」
    near_ceiling: float = 0.03
    fail_below_floor: float = 0.03  # 收盤跌破底線幾 % 判型態失敗
    max_extension: float = 0.15    # 突破後離平台上緣超過這個 % 標追高


PARAMS = Params()


def sma(values, n, i):
    if i + 1 < n:
        return None
    return sum(values[i - n + 1:i + 1]) / n


def percentile(values, q):
    s = sorted(values)
    k = (len(s) - 1) * q
    lo, hi = int(k), min(int(k) + 1, len(s) - 1)
    return s[lo] + (s[hi] - s[lo]) * (k - lo)


def _band(closes):
    return (max(closes) - min(closes)) / min(closes)


def band_limit(bars, p=PARAMS):
    """依個股波動度決定平台振幅門檻：台積電這種低波動股 16% 太寬，會把半年都算成平台。"""
    tail = bars[-60:]
    trs = [(max(b.high, a.close) - min(b.low, a.close)) / a.close for a, b in zip(tail, tail[1:])]
    return min(p.max_band, max(p.min_band, percentile(trs, 0.5) * p.band_tr_mult))


def find_anchor(bars, p=PARAMS):
    """前波低點（錨點）：最近 anchor_lookback 根內「之後再也沒被跌破」的低點裡，跌幅最大的那個。

    只取最低價會抓到更早的舊低點（中美晶 6 月的 137.5 比 7/30 的 150.5 還低）；
    只取最近的會抓到平台內的小回檔。都沒達 min_crash 就退回區間最低點。
    """
    n = len(bars)
    lo, hi = max(0, n - p.anchor_lookback), n - p.min_base_bars
    if hi <= lo:
        return None
    lowest = min(range(lo, hi), key=lambda i: bars[i].low)
    candidates = []
    floor_after = float("inf")
    for i in range(n - 1, lo - 1, -1):
        if i < hi and bars[i].low < floor_after:
            crash = _crash_at(bars, i, p)
            if crash and crash["drop"] >= p.min_crash:
                candidates.append((crash["drop"], i))
        floor_after = min(floor_after, bars[i].low)
    return max(candidates)[1] if candidates else lowest


def find_base(bars, p=PARAMS, min_start=0, max_band=None):
    """找平台：起點在 min_start 之後、收盤振幅 ≤ max_band 的最長一段。終點可以在最近
    max_breakout_age 根內，這樣才抓得到「已經突破」的平台。回傳 (start, end) 或 None。"""
    max_band = p.max_band if max_band is None else max_band
    closes = [b.close for b in bars]
    n = len(closes)
    best = None
    for end in range(n - 1, max(n - 2 - p.max_breakout_age, min_start + p.min_base_bars - 2), -1):
        start = end
        while start - 1 >= min_start and _band(closes[start - 1:end + 1]) <= max_band:
            start -= 1
        length = end - start + 1
        if length >= p.min_base_bars and (best is None or length > best[1] - best[0] + 1):
            best = (start, end)
    return best


def _crash_at(bars, lo_idx, p):
    """急跌幅度：從錨點低點往前 crash_lookback 根找最高價。"""
    hi_from = max(0, lo_idx - p.crash_lookback)
    if hi_from == lo_idx:
        return None
    hi_idx = max(range(hi_from, lo_idx), key=lambda i: bars[i].high)
    hi, lo = bars[hi_idx].high, bars[lo_idx].low
    return {"high": hi, "high_day": bars[hi_idx].day.isoformat(),
            "low": lo, "low_day": bars[lo_idx].day.isoformat(),
            "drop": (hi - lo) / hi, "low_idx": lo_idx}


def _ma_snapshot(closes, i):
    return {k: sma(closes, n, i) for k, n in (("ma5", 5), ("ma20", 20), ("ma60", 60), ("ma120", 120))}


def _ma_spread(ma, close):
    vals = [ma["ma5"], ma["ma20"], ma["ma60"]]
    if None in vals:
        return None
    return (max(vals) - min(vals)) / close


def _status(bars, start, end, floor, ceiling, p):
    last = bars[-1]
    after = len(bars) - 1 - end
    if last.close < floor * (1 - p.fail_below_floor):
        return "failed", "收盤跌破平台底線，型態失敗"
    if after == 0:
        if last.close <= floor * (1 + p.near_floor):
            return "base_floor", "整理中，貼近支撐區底線（進場區）"
        if last.close >= ceiling * (1 - p.near_ceiling):
            return "base_ceiling", "整理中，逼近平台上緣（等突破確認）"
        return "base_mid", "整理中，位於平台中段"
    ext = last.close / ceiling - 1
    if ext > p.max_extension:
        return "extended", f"已突破 {after} 天、離上緣 +{ext:.0%}，追高風險大"
    if last.close > ceiling:
        return "breakout", f"已突破平台 {after} 天（離上緣 +{ext:.1%}）"
    return "pullback", f"突破後回測平台內（{after} 天前離開平台）"


def analyze(bars, p=PARAMS):
    if len(bars) < 130:
        return {"match": "insufficient", "reason": f"日線只有 {len(bars)} 根，至少要 130 根才算得出 MA120"}
    closes = [b.close for b in bars]
    vols = [b.volume for b in bars]
    anchor = find_anchor(bars, p)
    limit = band_limit(bars, p)
    base = find_base(bars, p, min_start=anchor + 1, max_band=limit) if anchor is not None else None
    if base is None:
        return {"match": "no", "reason": f"前波低點之後找不到收盤振幅 ≤{limit:.1%}、長度 ≥{p.min_base_bars} 日的平台"}
    start, end = base
    seg = bars[start:end + 1]
    body_lows = [min(b.open, b.close) for b in seg]
    floor = percentile(body_lows, 0.10)
    ceiling = max(b.close for b in seg)
    touches = sum(1 for b in seg if b.low <= floor * 1.02)
    half = len(body_lows) // 2
    floor_drift = percentile(body_lows[half:], 0.10) / percentile(body_lows[:half], 0.10) - 1

    crash = _crash_at(bars, anchor, p)
    ma_end = _ma_snapshot(closes, end)
    ma_now = _ma_snapshot(closes, len(bars) - 1)
    spread_end = _ma_spread(ma_end, closes[end])
    spreads_tail = [s for s in (_ma_spread(_ma_snapshot(closes, i), closes[i])
                                for i in range(max(start, end - 9), end + 1)) if s is not None]
    spread_min = min(spreads_tail) if spreads_tail else None
    ma120_prev = sma(closes, 120, len(bars) - 1 - p.ma120_slope_bars)
    ma120_slope = (ma_now["ma120"] / ma120_prev - 1) if ma120_prev else None

    pre_vol = vols[max(0, start - 20):start]
    base_vol = sum(vols[start:end + 1]) / (end - start + 1)
    vol_ratio = base_vol / (sum(pre_vol) / len(pre_vol)) if pre_vol else None
    post = list(range(end + 1, len(bars)))
    bo_idx = next((i for i in post if closes[i] > ceiling), None)
    bo_vol_ratio = vols[bo_idx] / base_vol if bo_idx is not None and base_vol else None

    status, status_text = _status(bars, start, end, floor, ceiling, p)
    checks = _checks(p, {
        "crash": crash, "base_len": end - start + 1, "floor": floor, "touches": touches,
        "floor_drift": floor_drift, "spread_min": spread_min, "ma120_slope": ma120_slope,
        "above_ma120": bool(ma_now["ma120"]) and closes[-1] >= ma_now["ma120"],
        "vol_ratio": vol_ratio, "bo_vol_ratio": bo_vol_ratio})
    match = _verdict(checks, status)

    return {
        "match": match,
        "entry_zone": status == "base_floor" and match != "no",
        "status": status, "status_text": status_text,
        "last": {"day": bars[-1].day.isoformat(), "close": closes[-1], "volume": vols[-1]},
        "base": {"start": seg[0].day.isoformat(), "end": seg[-1].day.isoformat(),
                 "bars": end - start + 1, "floor": round(floor, 2), "ceiling": ceiling,
                 "band": round(_band([b.close for b in seg]), 4), "band_limit": round(limit, 4),
                 "floor_touches": touches, "floor_drift": round(floor_drift, 4),
                 "avg_volume": round(base_vol, 1)},
        "crash": {k: v for k, v in (crash or {}).items() if k != "low_idx"} or None,
        "ma_now": {k: (round(v, 2) if v else None) for k, v in ma_now.items()},
        "ma_spread_at_base_end": spread_end, "ma_spread_min_last10": spread_min,
        "ma120_slope_20d": ma120_slope,
        "volume_contraction": vol_ratio,
        "breakout": ({"day": bars[bo_idx].day.isoformat(), "close": closes[bo_idx],
                      "vol_ratio": bo_vol_ratio} if bo_idx is not None else None),
        "levels": {"stop_ref": round(floor * (1 - p.fail_below_floor), 2),
                   "dist_to_floor": closes[-1] / floor - 1,
                   "dist_to_ceiling": closes[-1] / ceiling - 1},
        "checks": checks,
        "params": asdict(p),
    }


def _check(key, label, ok, detail, kind):
    """kind: required（不過就不符合）／core（決定符合與否）／bonus（加分）／info（只顯示）。"""
    return {"key": key, "label": label, "ok": ok, "detail": detail, "kind": kind,
            "required": kind == "required"}


def _fmt(x, f="{:.1%}"):
    return "n/a" if x is None else f.format(x)


def _checks(p, m):
    """m 是 analyze 算好的量測值。核心是「穩定的支撐區」，急跌只是背景資訊。"""
    crash, base_len = m["crash"], m["base_len"]
    out = [
        _check("base", "平台整理", base_len >= p.min_base_bars, f"{base_len} 日", "required"),
        _check("higher_low", "底線高於前波低點", crash is not None and m["floor"] > crash["low"],
               f"底線 {m['floor']:.2f} vs 前波低點 {crash['low'] if crash else 'n/a'}", "required"),
        _check("mature", f"平台 ≥{p.mature_base_bars} 日", base_len >= p.mature_base_bars,
               f"{base_len} 日" + ("" if base_len >= p.mature_base_bars else "（平台尚短，支撐還沒被驗證夠久）"), "core"),
        _check("touches", f"支撐被測試 ≥{p.min_touches} 次", m["touches"] >= p.min_touches,
               f"{m['touches']} 次觸及底線 +2% 內", "core"),
        _check("support_flat", "支撐區平穩（沒有越墊越低）", m["floor_drift"] >= -p.max_floor_drift,
               f"後半段底線 vs 前半段 {_fmt(m['floor_drift'], '{:+.1%}')}（容許 −{p.max_floor_drift:.0%}）", "core"),
        _check("ma_tangle", "MA5/20/60 糾結", m["spread_min"] is not None and m["spread_min"] <= p.ma_ok,
               f"最近 10 日最小開口 {_fmt(m['spread_min'])}（≤{p.ma_tight:.0%} 強、≤{p.ma_ok:.0%} 可）", "core"),
        _check("ma120_up", "MA120 上彎", m["ma120_slope"] is not None and m["ma120_slope"] > 0,
               f"20 日斜率 {_fmt(m['ma120_slope'], '{:+.1%}')}，收盤{'在' if m['above_ma120'] else '低於'} MA120（不要求站上）", "core"),
        _check("vol_dry", "整理期量縮", m["vol_ratio"] is not None and m["vol_ratio"] <= p.vol_contract,
               f"平台均量 / 平台前均量 = {_fmt(m['vol_ratio'], '{:.2f}')}（≤{p.vol_contract}）", "bonus"),
        _check("crash", "平台前有急跌洗盤", crash is not None and crash["drop"] >= p.min_crash,
               f"跌幅 {_fmt(crash['drop'] if crash else None)}（參考，不計分）", "info"),
    ]
    if m["bo_vol_ratio"] is not None:
        out.append(_check("breakout_vol", "突破帶量", m["bo_vol_ratio"] >= p.breakout_vol,
                          f"突破日量 = 平台均量 × {m['bo_vol_ratio']:.1f}（≥{p.breakout_vol}）", "bonus"))
    return out


def _verdict(checks, status):
    """必要全過才談；核心全過 → 符合，核心錯 1 項 → 部分符合。加分項只影響排序。
    曾放寬到錯 2 項，全市場掃出 60+ 檔「部分＋進場區」，多是 MA120 下彎的弱勢股，粗掃雜訊太大。"""
    if status == "failed" or not all(c["ok"] for c in checks if c["kind"] == "required"):
        return "no"
    core_miss = sum(1 for c in checks if c["kind"] == "core" and not c["ok"])
    if core_miss == 0:
        return "yes"
    if core_miss <= 1:
        return "partial"
    return "no"


def score(r):
    """排序用：符合度 → 是否在進場區 → 加分項數 → 離底線越近越前面。"""
    if "checks" not in r:
        return (9, 0, 0, 0)
    rank = {"yes": 0, "partial": 1, "no": 2}[r["match"]]
    bonus = sum(1 for c in r["checks"] if c["kind"] == "bonus" and c["ok"])
    return (rank, 0 if r["entry_zone"] else 1, -bonus, abs(r["levels"]["dist_to_floor"]))
