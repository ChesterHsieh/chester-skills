#!/usr/bin/env python3
"""LEAPS 候选合约筛选器 —— 把 IBKR connector 抓回来的报价换算成决策数字。

IBKR MCP connector 不回传 greeks，只给 bid/ask/IV/OI。这支脚本用 Black-Scholes
自己算 delta，并把「时间价值占比 / 年化槓桿租金 / 实质槓桿 / 半价差成本」摊开，
最后依 A3-1 delta 框架给出判定，并在给定预算下算出每档能买几口。

2026-09 修订（证据：SNOW 20261120 350C 的 −$7,010 拆解，见 references/playbook.md「案例」段）：
  A 财报 IV 封锁窗 + IV 百分位硬门槛
  B 重估 vs 确认判读（前次财报跳空 > 20% → 收紧 delta 下限）
  C 履约价交叉点 + 风险中性机率 N(d2) + 历史频率
  D 等美元 delta 曝险比较表（控制变因，凸显 vega 曝险差异）
  E 论述类型（中位数 / 尾部）与 delta 的匹配门槛
  G 时值占比升级为主风控指标（100% → 硬否决）

用法:
    python3 screener.py quotes.json
    cat quotes.json | python3 screener.py -

quotes.json 结构见 SKILL.md。所有价格单位为「每股」（非每口）。
"""
import json
import math
import sys
from datetime import date, timedelta

# ── A3-1 delta 框架（来源：leaps-trader 检讨报告） ────────────────────────
BUCKETS = [
    (0.85, 9.99, "深度 ITM（超框架）", "槓桿已低，接近直接持股，检查是否值得付价差"),
    (0.70, 0.85, "替代持股", "核心仓：时间价值薄、不怕盘整，可当长腿做 PMCC"),
    (0.40, 0.70, "高信念加速", "论点强时用，槓桿高但对盘整敏感"),
    (0.25, 0.40, "偏投机", "已接近彩券区，需并入投机总额"),
    (0.00, 0.25, "彩券", "L股 600C 的教训——一律归投机仓并限额"),
]
# 预算模式：budget 就是这次要花掉的权利金总额，全额用于买选择权。
# 不反推帐户比例——资金总量由使用者自己在 skill 外面管。
DTE_MIN_MONTHS, DTE_MAX_MONTHS = 12, 24   # A3-4 到期日窗口（月）
MAX_HALF_SPREAD_PCT = 0.03                # A3-3 价差红线：半价差 / 中价
MIN_OI = 200
EARNINGS_BLACKOUT_TD = 5                  # A 距财报 ≤5 个交易日 → 硬否决
EARNINGS_WARN_TD = 10                     # A 6–10 个交易日 → 需明示确认
POST_EVENT_WINDOW_TD = 2                  # F 催化剂后决策窗口
IV_PCT_HARD = 80.0                        # A IV 百分位 > 80 → 硬否决
IV_PCT_WARN = 50.0
EXT_PCT_WARN = 0.60                       # G 时值占比 > 60% → 警告
RERATE_GAP = 0.20                         # B 前次财报单日 > 20% → 视为重估
MIN_DELTA_MEDIAN = 0.70                   # E 中位数论述只能用高 delta
MIN_DELTA_TAIL = 0.25                     # E 尾部论述仍不碰彩券区
MIN_DELTA_TAIL_RERATED = 0.50             # B 确认季再收紧
TRADING_DAYS_PER_YEAR = 252

SAMPLE_BIAS_WARNING = (
    "※ 历史频率是「这两年的样本」，不是机率。若这段期间标的本身走了大多头，\n"
    "   用它当尾部下注的基准机率就是对多头样本过度拟合——两个数字要一起看，不要只挑好听的。"
)


# ── 数学 ──────────────────────────────────────────────────────────────────
def norm_cdf(x):
    return 0.5 * (1.0 + math.erf(x / math.sqrt(2.0)))


def bs_call(spot, strike, t, iv, r, q):
    """回传 (delta, 理论价, vega/1pt IV)。t 以年计。"""
    if t <= 0 or iv <= 0:
        intrinsic = max(spot - strike, 0.0)
        return (1.0 if spot > strike else 0.0), intrinsic, 0.0
    sd = iv * math.sqrt(t)
    d1 = (math.log(spot / strike) + (r - q + 0.5 * iv * iv) * t) / sd
    d2 = d1 - sd
    disc_q, disc_r = math.exp(-q * t), math.exp(-r * t)
    delta = disc_q * norm_cdf(d1)
    price = spot * disc_q * norm_cdf(d1) - strike * disc_r * norm_cdf(d2)
    pdf = math.exp(-0.5 * d1 * d1) / math.sqrt(2 * math.pi)
    vega = spot * disc_q * pdf * math.sqrt(t) / 100.0
    return delta, price, vega


def prob_above(spot, target, t, iv, r, q):
    """风险中性机率 P(S_T ≥ target) = N(d2)。这是市场定价的机率，不是真实机率。"""
    if t <= 0 or iv <= 0 or spot <= 0 or target <= 0:
        return 1.0 if spot >= target else 0.0
    sd = iv * math.sqrt(t)
    d2 = (math.log(spot / target) + (r - q - 0.5 * iv * iv) * t) / sd
    return norm_cdf(d2)


# ── 日期 ──────────────────────────────────────────────────────────────────
def parse_date(s):
    s = str(s).strip()
    if len(s) == 8 and s.isdigit():
        return date(int(s[:4]), int(s[4:6]), int(s[6:8]))
    return date.fromisoformat(s[:10])


def years_to(expiry, today):
    return (parse_date(expiry) - today).days / 365.0


def trading_days_between(a, b):
    """a→b 的交易日数（只扣週末、未扣假日，宁可保守）。b 在 a 之前回负数。"""
    sign = 1 if b >= a else -1
    lo, hi = (a, b) if b >= a else (b, a)
    n, d = 0, lo
    while d < hi:
        d += timedelta(days=1)
        if d.weekday() < 5:
            n += 1
    return sign * n


# ── 输入正规化 ────────────────────────────────────────────────────────────
def normalize_iv_percentile(raw):
    """connector 的 implied-volatility-percentile 有时是分数(0.92)、有时是百分比(4.8)。
    ≤1 一律当分数。回传 (百分比, 解读说明)。"""
    v = float(raw)
    if v <= 1.0:
        return v * 100.0, f"输入 {v:g} 当分数读 → 第 {v * 100:.0f} 百分位"
    return v, f"输入 {v:g} 当百分位读"


def load_history(cfg):
    """回传 (dates, closes)。支援 [{'date':..,'close':..}] 或纯 close 阵列。"""
    hist = cfg.get("price_history")
    if not hist and cfg.get("price_history_file"):
        with open(cfg["price_history_file"]) as f:
            hist = json.load(f)
    if isinstance(hist, dict):
        hist = hist.get("bars") or hist.get("data") or []
    if not hist:
        return [], []
    dates, closes = [], []
    for pt in hist:
        if isinstance(pt, dict):
            c = pt.get("close", pt.get("c"))
            if c is None:
                continue
            closes.append(float(c))
            dates.append(str(pt.get("date", pt.get("t", ""))))
        else:
            closes.append(float(pt))
            dates.append("")
    return dates, closes


def biggest_1d_moves(dates, closes, k=3):
    moves = []
    for i in range(1, len(closes)):
        if closes[i - 1] > 0:
            moves.append((closes[i] / closes[i - 1] - 1.0, dates[i]))
    moves.sort(key=lambda m: -abs(m[0]))
    return moves[:k]


def move_on_dates(dates, closes, targets):
    """回传 [(日期, 当日报酬)]，只取 price_history 里对得上的日子。"""
    idx = {d[:10]: i for i, d in enumerate(dates) if d}
    out = []
    for t in targets:
        i = idx.get(str(t)[:10])
        if i and closes[i - 1] > 0:
            out.append((str(t)[:10], closes[i] / closes[i - 1] - 1.0))
    return out


def rolling_freq(closes, horizon, threshold_ret):
    """滚动 horizon 个交易日报酬 ≥ threshold_ret 的经验频率。回传 (频率, 样本数)。"""
    if horizon < 1 or len(closes) <= horizon:
        return None, 0
    hits = tot = 0
    for i in range(len(closes) - horizon):
        if closes[i] <= 0:
            continue
        tot += 1
        if closes[i + horizon] / closes[i] - 1.0 >= threshold_ret:
            hits += 1
    return (hits / tot, tot) if tot else (None, 0)


# ── 关卡（A / B / E / F） ────────────────────────────────────────────────
def build_gates(cfg, today, dates, closes):
    """开仓前的整体关卡。回传 hard / warn / info 三串讯息与 delta 下限。"""
    hard, warn, info = [], [], []

    # E. 论述类型 ↔ delta 匹配
    thesis = str(cfg.get("thesis_type", "")).strip().lower()
    if thesis not in ("median", "tail"):
        thesis = "median"
        warn.append('未指定 thesis_type → 预设「中位数论述」（本 skill 的预设路径）。'
                    '论点若是「市场会因此重新定价 X%」这种尾部判断，请明写 thesis_type="tail" '
                    '并附 tail_trigger／tail_window。')
    min_delta = MIN_DELTA_MEDIAN if thesis == "median" else MIN_DELTA_TAIL
    if thesis == "tail":
        if not str(cfg.get("tail_trigger", "")).strip() or not str(cfg.get("tail_window", "")).strip():
            hard.append("thesis_type=tail 但没写 tail_trigger／tail_window："
                        "尾部下注必须写出触发机制与时间窗，否则退回中位数路径（delta ≥ 0.70）")
            min_delta = MIN_DELTA_MEDIAN

    # B. 重估 vs 确认
    regime = None
    prior = cfg.get("prior_earnings_move")
    prior_date = cfg.get("prior_earnings_date", "")
    if prior is None and closes and cfg.get("earnings_history"):
        this_er = str(cfg.get("earnings_date", ""))[:10]
        past = sorted((d, m) for d, m in move_on_dates(dates, closes, cfg["earnings_history"])
                      if parse_date(d) < today and d != this_er)
        if past:
            prior_date, prior = past[-1]   # 今天之前最近的一次财报
    if closes:
        top = biggest_1d_moves(dates, closes)
        if top:
            info.append("历史最大单日跳空：" + "、".join(
                f'{m * 100:+.1f}%{("（" + d[:10] + "）") if d else ""}' for m, d in top))
    if prior is not None:
        prior = float(prior)
        if abs(prior) > RERATE_GAP:
            regime = "confirmation"
            min_delta = max(min_delta, MIN_DELTA_TAIL_RERATED)
            warn.append(f'前次财报单日 {prior * 100:+.1f}%{("（" + str(prior_date)[:10] + "）") if prior_date else ""}'
                        f' > {RERATE_GAP:.0%} → 前次是「重估」事件（市场首次认知），'
                        f'本次大机率只是「确认」：右尾期望值应下调，delta 下限收紧至 {min_delta:.2f}')
        else:
            info.append(f"前次财报单日 {prior * 100:+.1f}% ≤ {RERATE_GAP:.0%} → 未出现重估跳空")

    # A. 财报 IV 封锁窗
    if cfg.get("earnings_date"):
        edt = parse_date(cfg["earnings_date"])
        td = trading_days_between(today, edt)
        if 0 <= td <= EARNINGS_BLACKOUT_TD:
            hard.append(f"距财报 {td} 个交易日（≤{EARNINGS_BLACKOUT_TD}）→ 财报 IV 溢价窗，不开新买方部位。"
                        f"实测：T-21／T-14／T-7 进场结果几乎相同，惩罚集中在最后 2–3 个交易日")
        elif td <= EARNINGS_WARN_TD and td > 0:
            msg = (f"距财报 {td} 个交易日（{EARNINGS_BLACKOUT_TD + 1}–{EARNINGS_WARN_TD} 区间）→ "
                   f"IV 已在爬升段，要开仓须由你明示确认（earnings_confirmed=true）")
            (info if cfg.get("earnings_confirmed") else warn).append(
                msg + ("；已确认" if cfg.get("earnings_confirmed") else ""))
        elif td < 0:
            info.append(f"财报 {edt} 已过 {-td} 个交易日")
            if -td <= POST_EVENT_WINDOW_TD:
                warn.append(f"F 催化剂后窗口：事件已发生 {-td} 个交易日。若部位的进场理由是这个事件，"
                            f"IV 崩溃与不确定性解除同时发生，{POST_EVENT_WINDOW_TD} 个交易日内必须做决定")
        else:
            info.append(f"距财报 {td} 个交易日 → 在封锁窗外")
    else:
        warn.append("没给 earnings_date —— 财报窗关卡无法执行，先查财报日再跑")

    # A. IV 百分位硬门槛
    ivp = cfg.get("iv_percentile")
    if ivp is not None:
        pct, how = normalize_iv_percentile(ivp)
        rng = iv_range_text(cfg)
        if pct > IV_PCT_HARD:
            hard.append(f"IV 百分位 {pct:.0f}（{how}）> {IV_PCT_HARD:.0f} → 你买的主要是波动率溢价，不是方向{rng}")
        elif pct > IV_PCT_WARN:
            warn.append(f"IV 百分位 {pct:.0f} 介于 {IV_PCT_WARN:.0f}–{IV_PCT_HARD:.0f}："
                        f"买方在逆 vega 风，考虑改用 debit spread（A6-4）{rng}")
        else:
            info.append(f"IV 百分位 {pct:.0f} ≤ {IV_PCT_WARN:.0f} → 价格关通过（买方友善）{rng}")
    else:
        warn.append("没给 iv_percentile —— 价格关缺一个数字，先抓 implied_volatility_percentile 再跑")

    return {"hard": hard, "warn": warn, "info": info,
            "min_delta": min_delta, "thesis": thesis, "regime": regime}


def iv_range_text(cfg):
    """否决讯息要让使用者看到自己付的是什么价：当前 IV + 52 週区间。"""
    cur = cfg.get("underlying_iv")
    rng = cfg.get("iv_52w") or {}
    lo, hi = rng.get("low"), rng.get("high")
    bits = []
    if cur is not None:
        bits.append(f"当前 IV {float(cur) * 100:.1f}%")
    if lo is not None and hi is not None:
        bits.append(f"52 週区间 {float(lo) * 100:.1f}%–{float(hi) * 100:.1f}%")
    return "；" + "、".join(bits) if bits else "；（未提供 52 週 IV 区间与当前 IV，看不到你付的价在哪——建议补上）"


# ── 单档评估 ──────────────────────────────────────────────────────────────
def evaluate(q, cfg, today, gates):
    spot = cfg["spot"]
    strike = float(q["strike"])
    bid, ask = float(q["bid"]), float(q["ask"])
    mid = (bid + ask) / 2.0
    iv = float(q["iv"])
    t = years_to(str(q["expiry"]), today)
    delta, theo, vega = bs_call(spot, strike, t, iv, cfg["rate"], cfg.get("div_yield", 0.0))

    intrinsic = max(spot - strike, 0.0)
    extrinsic = mid - intrinsic
    ext_pct = extrinsic / mid if mid else 0.0
    # 槓桿「租金」：为了取得等效持股，多付的时间价值年化后占等效股价的比例
    rent = (extrinsic / t) / spot if t > 0 else float("inf")
    notional = delta * spot * 100.0          # 每口等效持股金额
    premium = mid * 100.0                    # 每口权利金
    leverage = notional / premium if premium else 0.0
    half_spread = (ask - bid) / 2.0
    half_spread_pct = half_spread / mid if mid else 1.0
    oi = int(q.get("oi", 0) or 0)

    bucket, note = "未分类", ""
    for lo, hi, name, desc in BUCKETS:
        if lo <= delta < hi:
            bucket, note = name, desc
            break

    budget = cfg["budget"]
    n_ct = int(budget // premium) if premium else 0
    spend = n_ct * premium
    leftover = budget - spend

    fails = []          # 合约层级的否决
    warns = []
    blocked = bool(gates["hard"])   # 整体关卡（A/B/E）已挡下，与选哪档无关
    months = t * 12
    if not (DTE_MIN_MONTHS <= months <= DTE_MAX_MONTHS):
        fails.append(f"到期窗 {months:.1f} 月不在 {DTE_MIN_MONTHS}-{DTE_MAX_MONTHS} 月")
    if half_spread_pct > MAX_HALF_SPREAD_PCT:
        fails.append(f"半价差 {half_spread_pct:.1%} > {MAX_HALF_SPREAD_PCT:.0%}")
    if oi < MIN_OI:
        fails.append(f"OI {oi} < {MIN_OI}")
    if n_ct < 1:
        fails.append(f"单口权利金 ${premium:,.0f} > 预算 ${budget:,.0f}")
    # G. 时值占比是主风控指标，不是参考数字
    if ext_pct >= 0.9995:
        fails.append(f"时值占比 100%：零内含价值，${mid:,.2f}/股 全额暴露在 vega／theta")
        if gates["thesis"] == "tail":
            warns.append("G 关（时值 100% 硬否决）会挡掉所有价外 call，包含尾部路径想用的那些——"
                         "尾部下注要买价外，就改用 debit spread（A6-4）把 vega 曝险切掉，别买裸 call")
    elif ext_pct > EXT_PCT_WARN:
        warns.append(f"时值占比 {ext_pct:.0%} > {EXT_PCT_WARN:.0%}：多数资金买的是时间与波动率，不是资产")
    # E. 论述类型 ↔ delta
    path = "中位数论述" if gates["thesis"] == "median" else "尾部论述"
    if gates["regime"] == "confirmation":
        path += "＋确认季收紧"
    if delta < gates["min_delta"]:
        fails.append(f"delta {delta:.2f} < {gates['min_delta']:.2f}（{path}的下限）")

    breakeven = strike + mid
    return {
        "label": f'{cfg["symbol"]} {q["expiry"]} {strike:g}C',
        "expiry": str(q["expiry"]), "strike": strike,
        "months": months, "t": t, "mid": mid, "delta": delta, "iv": iv,
        "intrinsic": intrinsic, "extrinsic": extrinsic, "ext_pct": ext_pct,
        "rent": rent, "leverage": leverage, "vega_1pt": vega * 100,
        "half_spread": half_spread, "half_spread_pct": half_spread_pct,
        "oi": oi, "bucket": bucket, "note": note,
        "premium": premium, "n_contracts": n_ct, "spend": spend,
        "leftover": leftover, "budget_use": spend / budget if budget else 0.0,
        "eff_shares": delta * n_ct * 100,
        "notional": delta * spot * n_ct * 100,
        "theo": theo, "rich": mid - theo,
        "breakeven": breakeven, "be_move": breakeven / spot - 1.0,
        "fails": fails, "warns": warns, "blocked": blocked,
        "rejected": blocked or bool(fails),
    }


# ── C. 交叉点 ─────────────────────────────────────────────────────────────
def pnl_at(row, s):
    """到期损益（以整数口数、同一笔预算计）。"""
    return row["n_contracts"] * 100 * max(s - row["strike"], 0.0) - row["spend"]


def expiry_crossovers(a, b):
    """两档同到期候选的到期损益交叉点。分段解析，回传由小到大的价格清单。"""
    lo, hi = (a, b) if a["strike"] <= b["strike"] else (b, a)
    if lo["strike"] == hi["strike"]:
        return []
    pts = []
    n_lo = lo["n_contracts"] * 100
    if n_lo > 0:  # 区间 (K_lo, K_hi]：只有低履约价有内含价值
        s = lo["strike"] + (lo["spend"] - hi["spend"]) / n_lo
        if lo["strike"] < s <= hi["strike"]:
            pts.append(s)
    slope = 100 * (lo["n_contracts"] - hi["n_contracts"])   # 区间 (K_hi, ∞)
    if slope != 0:
        const = (-100 * lo["n_contracts"] * lo["strike"] + 100 * hi["n_contracts"] * hi["strike"]
                 - lo["spend"] + hi["spend"])
        s = -const / slope
        if s > hi["strike"]:
            pts.append(s)
    return sorted(pts)


def threshold_line(cfg, row_for_iv, price, horizon_td, closes):
    """同一个价格门槛的两个数字：市场定价的 N(d2) 与历史经验频率。"""
    spot = cfg["spot"]
    rn = prob_above(spot, price, row_for_iv["t"], row_for_iv["iv"], cfg["rate"], cfg.get("div_yield", 0.0))
    freq, n = rolling_freq(closes, horizon_td, price / spot - 1.0) if closes else (None, 0)
    hist = f"{freq * 100:.1f}%（{n} 个滚动窗）" if freq is not None else "—（没给 price_history）"
    return f"市场定价 {rn * 100:.1f}% ／ 历史频率 {hist}"


# ── 报表 ──────────────────────────────────────────────────────────────────
def print_gates(gates):
    path = "中位数论述 → 只能走高 delta（≥0.70）" if gates["thesis"] == "median" else \
           "尾部论述 → 才轮得到低 delta，且须写出触发机制与时间窗"
    print(f'【第 0 关 · 论述类型】{path}；本次 delta 下限 {gates["min_delta"]:.2f}')
    if gates["regime"] == "confirmation":
        print("            体制判读：确认季（前次为重估）")
    print("【第 1 关 · 事件与波动率窗口】")
    for m in gates["hard"]:
        print(f"  ✗ 硬否决  {m}")
    for m in gates["warn"]:
        print(f"  ! 警告    {m}")
    for m in gates["info"]:
        print(f"  · 讯息    {m}")
    if gates["hard"]:
        print("\n  ⇒ 这些是开仓前的整体否决，与选哪个履约价无关。下面的表只是让你看清楚代价，不是备选清单。")


def print_table(rows):
    hdr = (f'{"合约":<26}{"月":>5}{"中价":>8}{"delta":>7}{"IV":>7}{"时值%":>7}'
           f'{"年租金":>8}{"槓桿":>6}{"半价差":>8}{"OI":>7}{"口数":>6}{"动用":>8}  {"分类"}')
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        flag = "  " if not r["rejected"] else "✗ "
        print(f'{flag}{r["label"]:<24}{r["months"]:>5.1f}{r["mid"]:>8.2f}{r["delta"]:>7.2f}'
              f'{r["iv"] * 100:>6.1f}%{r["ext_pct"] * 100:>6.1f}%{r["rent"] * 100:>7.1f}%'
              f'{r["leverage"]:>5.1f}x{r["half_spread_pct"] * 100:>7.1f}%{r["oi"]:>7}'
              f'{r["n_contracts"]:>6}{r["budget_use"] * 100:>7.0f}%  {r["bucket"]}')


def print_details(rows, cfg, today, closes):
    print("\n--- 逐档细节 ---")
    for r in rows:
        print(f'\n▸ {r["label"]}  [{r["bucket"]}]')
        print(f'   {r["note"]}')
        print(f'   每口权利金 ${r["premium"]:,.0f}；每口等效持股 ${r["delta"] * cfg["spot"] * 100:,.0f}')
        if r["n_contracts"] >= 1:
            print(f'   预算 ${cfg["budget"]:,.0f} → 买 {r["n_contracts"]} 口，花 ${r["spend"]:,.0f}'
                  f'（动用 {r["budget_use"]:.0%}），剩 ${r["leftover"]:,.0f}')
            print(f'   → 总等效持股 {r["eff_shares"]:,.0f} 股 / ${r["notional"]:,.0f}'
                  f'（同样的钱直接买正股只有 {r["spend"] / cfg["spot"]:,.0f} 股）')
        else:
            print(f'   预算 ${cfg["budget"]:,.0f} 买不到一口')
        # G. 这笔钱里多少是资产、多少是利息
        print(f'   这笔钱：资产（内含价值）${r["intrinsic"]:.2f}/股 ／ 利息（时间价值）${r["extrinsic"]:.2f}/股'
              f' → 时值占比 {r["ext_pct"]:.0%}')
        print(f'   年化槓桿租金 {r["rent"] * 100:.1f}%（对照融资利率 {cfg["rate"] * 100:.1f}%）；'
              f'IV 每动 1 点 ≈ ${r["vega_1pt"]:,.0f}/口'
              f'（{r["n_contracts"]} 口 = ${r["vega_1pt"] * r["n_contracts"]:,.0f}）')
        print(f'   一买一卖价差成本 ≈ ${r["half_spread"] * 200:,.0f}/口')
        horizon = trading_days_between(today, parse_date(r["expiry"]))
        print(f'   到期损益两平 ${r["breakeven"]:.2f}（需 {r["be_move"] * 100:+.1f}%）：'
              f'{threshold_line(cfg, r, r["breakeven"], horizon, closes)}')
        for w in r["warns"]:
            print(f'   ! {w}')
        if r["blocked"]:
            print("   判定：否决（已被上面的整体关卡挡下，与选哪个履约价无关）")
        if r["fails"]:
            print(f'   合约层级：否决 → {"；".join(r["fails"])}')
        elif not r["blocked"]:
            print("   判定：三关全过 ✓")


def print_crossovers(rows, cfg, today, closes):
    """C. 低履约价在什么价位才输给高履约价——使用者最需要的那个数字。"""
    pairs = [(a, b) for i, a in enumerate(rows) for b in rows[i + 1:]
             if a["expiry"] == b["expiry"] and a["strike"] != b["strike"]
             and a["n_contracts"] >= 1 and b["n_contracts"] >= 1]
    pairs.sort(key=lambda p: (p[0]["expiry"], min(p[0]["strike"], p[1]["strike"])))
    print("\n--- C. 履约价交叉点（同预算、同到期、整数口数的到期损益）---")
    if not pairs:
        print("   （需要同一到期日、且各自至少买得到一口的两档以上候选才能比）")
        return
    for a, b in pairs:
        lo, hi = (a, b) if a["strike"] <= b["strike"] else (b, a)
        pts = expiry_crossovers(lo, hi)
        head = (f'{lo["strike"]:g}C（{lo["n_contracts"]} 口 ${lo["spend"]:,.0f}，Δ{lo["delta"]:.2f}）'
                f' vs {hi["strike"]:g}C（{hi["n_contracts"]} 口 ${hi["spend"]:,.0f}，Δ{hi["delta"]:.2f}）')
        print(f'\n   {head}')
        if not pts:
            print("      两条到期损益线不相交（口数相同或结构重叠）")
            continue
        horizon = trading_days_between(today, parse_date(hi["expiry"]))
        for s in pts:
            move = s / cfg["spot"] - 1.0
            probe = s * 1.0005 + 0.01
            winner = f'{hi["strike"]:g}C' if pnl_at(hi, probe) > pnl_at(lo, probe) else f'{lo["strike"]:g}C'
            print(f'      交叉点 ${s:,.2f}（距 spot {move * 100:+.1f}%）→ 高于此价 {winner} 才开始赢')
            print(f'         {threshold_line(cfg, hi, s, horizon, closes)}')
    print("\n" + SAMPLE_BIAS_WARNING)


def print_equal_delta(rows, cfg):
    """D. 控制变因：固定美元 delta 曝险，看资金佔用与时值总额如何分家。"""
    live = [r for r in rows if r["delta"] > 0 and r["premium"] > 0]
    if len(live) < 2:
        return
    target = cfg.get("target_dollar_delta")
    if not target:
        target = max((r["notional"] for r in live if r["n_contracts"] >= 1), default=0.0)
        if not target:
            target = max(r["delta"] * cfg["spot"] * 100 * (cfg["budget"] / r["premium"]) for r in live)
    print(f'\n--- D. 等美元 delta 曝险比较（控制变因：固定 ${target:,.0f} 的美元 delta）---')
    hdr = f'{"履约价":<12}{"需要口数":>10}{"投入资金":>12}{"资产(内含)":>13}{"时值总额":>12}{"IV 1pt 曝险":>13}'
    print(hdr)
    print("-" * len(hdr))
    for r in sorted(live, key=lambda x: x["strike"]):
        per_ct = r["delta"] * cfg["spot"] * 100
        n = target / per_ct if per_ct else 0.0
        print(f'{r["strike"]:<12g}{n:>10.2f}{n * r["premium"]:>12,.0f}'
              f'{n * r["intrinsic"] * 100:>13,.0f}{n * r["extrinsic"] * 100:>12,.0f}'
              f'{n * r["vega_1pt"]:>13,.0f}')
    print("\n   → 选深度 ITM 不是「降低槓桿」，是「用同样的槓桿买下更少的波动率曝险」。")
    print("     Delta 和 Vega 是两个独立旋钮，价外把它们焊死在一起。")


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "-"
    cfg = json.load(sys.stdin if src == "-" else open(src))
    for key in ("symbol", "spot", "budget", "quotes"):
        if key not in cfg:
            sys.exit(f"缺少必填栏位 `{key}`。budget = 这次要花在选择权上的权利金总额。")
    if not cfg["quotes"]:
        sys.exit("quotes 是空的——先用 get_price_snapshot 逐档抓 bid/ask/iv/oi。")
    for q in cfg["quotes"]:
        for key in ("expiry", "strike", "bid", "ask", "iv"):
            if q.get(key) is None:
                sys.exit(f"quote {q} 缺 `{key}`。iv 取 implied-vol.annual_iv，不要用 option_midpoint_iv。")
    cfg.setdefault("rate", 0.04)
    cfg.setdefault("div_yield", 0.0)
    today = date.fromisoformat(cfg["asof"]) if "asof" in cfg else date.today()

    dates, closes = load_history(cfg)
    gates = build_gates(cfg, today, dates, closes)
    rows = [evaluate(q, cfg, today, gates) for q in cfg["quotes"]]
    rows.sort(key=lambda r: (r["rejected"], -r["delta"]))

    print(f'\n=== {cfg["symbol"]} LEAPS 候选（spot {cfg["spot"]}, asof {today}） ===')
    print_gates(gates)
    print(f'\n预算 ${cfg["budget"]:,.0f}（全额用于买选择权）\n')
    print_table(rows)
    print_details(rows, cfg, today, closes)
    print_crossovers(rows, cfg, today, closes)
    print_equal_delta(rows, cfg)
    print("\n※ 本表是筛选与风险计算工具，不是投资建议；下单与否由你决定。")


if __name__ == "__main__":
    main()
