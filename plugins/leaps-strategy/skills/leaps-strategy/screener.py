#!/usr/bin/env python3
"""LEAPS 候选合约筛选器 —— 把 IBKR connector 抓回来的报价换算成决策数字。

IBKR MCP connector 不回传 greeks，只给 bid/ask/IV/OI。这支脚本用 Black-Scholes
自己算 delta，并把「时间价值占比 / 年化槓桿租金 / 实质槓桿 / 半价差成本」摊开，
最后依 A3-1 delta 框架给出判定，并在给定预算下算出每档能买几口。

预算（budget）是这次打算花在选择权上的权利金总额，可弹性调整；
脚本不做帐户比例检查，只回答「这笔预算在这档上买得到什么」。

用法:
    python3 screener.py quotes.json
    cat quotes.json | python3 screener.py -

quotes.json 结构见 SKILL.md。所有价格单位为「每股」（非每口）。
"""
import json
import math
import sys
from datetime import date

# ── A3-1 delta 框架（来源：leaps-trader 检讨报告） ────────────────────────
BUCKETS = [
    (0.70, 0.85, "替代持股", "核心仓：时间价值薄、不怕盘整，可当长腿做 PMCC"),
    (0.40, 0.70, "高信念加速", "论点强时用，槓桿高但对盘整敏感"),
    (0.25, 0.40, "偏投机", "已接近彩券区，需并入投机总额"),
    (0.00, 0.25, "彩券", "L股 600C 的教训——一律归投机仓并限额"),
]
# 预算模式：budget 就是这次要花掉的权利金总额，全额用于买选择权。
# 不反推帐户比例——资金总量由使用者自己在 skill 外面管。
# A3-4 到期日窗口（月）
DTE_MIN_MONTHS, DTE_MAX_MONTHS = 12, 24
# A3-3 价差红线：半价差 / 中价
MAX_HALF_SPREAD_PCT = 0.03
MIN_OI = 200


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


def years_to(expiry, today):
    y, m, d = int(expiry[:4]), int(expiry[4:6]), int(expiry[6:8])
    return (date(y, m, d) - today).days / 365.0


def evaluate(q, cfg, today):
    spot = cfg["spot"]
    strike = float(q["strike"])
    bid, ask = float(q["bid"]), float(q["ask"])
    mid = (bid + ask) / 2.0
    iv = float(q["iv"])
    t = years_to(str(q["expiry"]), today)
    delta, theo, vega = bs_call(spot, strike, t, iv, cfg["rate"], cfg.get("div_yield", 0.0))

    intrinsic = max(spot - strike, 0.0)
    extrinsic = mid - intrinsic
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
        if lo <= delta < hi or (hi == 0.85 and delta >= 0.85):
            bucket, note = name, desc
            break
    if delta >= 0.85:
        bucket, note = "深度 ITM（超框架）", "槓桿已低，接近直接持股，检查是否值得付价差"

    budget = cfg["budget"]
    n_ct = int(budget // premium) if premium else 0
    spend = n_ct * premium
    leftover = budget - spend
    budget_use = spend / budget if budget else 0.0

    fails = []
    months = t * 12
    if not (DTE_MIN_MONTHS <= months <= DTE_MAX_MONTHS):
        fails.append(f"到期窗 {months:.1f} 月不在 {DTE_MIN_MONTHS}-{DTE_MAX_MONTHS} 月")
    if half_spread_pct > MAX_HALF_SPREAD_PCT:
        fails.append(f"半价差 {half_spread_pct:.1%} > {MAX_HALF_SPREAD_PCT:.0%}")
    if oi < MIN_OI:
        fails.append(f"OI {oi} < {MIN_OI}")
    if n_ct < 1:
        fails.append(f"单口权利金 ${premium:,.0f} > 预算 ${budget:,.0f}")
    if delta < 0.25:
        fails.append("delta<0.25：彩券区，不是槓桿工具")

    return {
        "label": f'{cfg["symbol"]} {q["expiry"]} {strike:g}C',
        "months": months, "mid": mid, "delta": delta, "iv": iv,
        "intrinsic": intrinsic, "extrinsic": extrinsic,
        "ext_pct": extrinsic / mid if mid else 0,
        "rent": rent, "leverage": leverage, "vega_1pt": vega * 100,
        "half_spread": half_spread, "half_spread_pct": half_spread_pct,
        "oi": oi, "bucket": bucket, "note": note,
        "premium": premium, "n_contracts": n_ct, "spend": spend,
        "leftover": leftover, "budget_use": budget_use,
        "eff_shares": delta * n_ct * 100,
        "notional": delta * spot * n_ct * 100,
        "theo": theo, "rich": mid - theo,
        "fails": fails,
    }


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "-"
    cfg = json.load(sys.stdin if src == "-" else open(src))
    for key in ("symbol", "spot", "budget", "quotes"):
        if key not in cfg:
            sys.exit(f"缺少必填栏位 `{key}`。budget = 这次要花在选择权上的权利金总额。")
    if not cfg["quotes"]:
        sys.exit("quotes 是空的——先用 get_price_snapshot 逐档抓 bid/ask/iv/oi。")
    cfg.setdefault("rate", 0.04)
    cfg.setdefault("div_yield", 0.0)
    today = date.fromisoformat(cfg["asof"]) if "asof" in cfg else date.today()

    rows = [evaluate(q, cfg, today) for q in cfg["quotes"]]
    rows.sort(key=lambda r: (bool(r["fails"]), -r["delta"]))

    ivp = cfg.get("iv_percentile")
    print(f'\n=== {cfg["symbol"]} LEAPS 候选（spot {cfg["spot"]}, asof {today}） ===')
    if ivp is not None:
        gate = "通过（买方友善）" if ivp <= 50 else "否决：IV 偏贵，改用 debit spread 或等 IV 回落"
        print(f'A2-2 价格关 · IV 百分位 {ivp:.0f} → {gate}')
    if cfg.get("earnings_date"):
        print(f'A4-4 财报日 {cfg["earnings_date"]} —— 财报前 N 天不开新买方部位')
    print(f'预算 ${cfg["budget"]:,.0f}（全额用于买选择权）\n')

    hdr = f'{"合约":<26}{"月":>5}{"中价":>8}{"delta":>7}{"IV":>7}{"时值%":>7}{"年租金":>8}{"槓桿":>6}{"半价差":>8}{"OI":>7}{"口数":>6}{"动用":>8}  {"分类"}'
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        flag = "  " if not r["fails"] else "✗ "
        print(f'{flag}{r["label"]:<24}{r["months"]:>5.1f}{r["mid"]:>8.2f}{r["delta"]:>7.2f}'
              f'{r["iv"]*100:>6.1f}%{r["ext_pct"]*100:>6.1f}%{r["rent"]*100:>7.1f}%'
              f'{r["leverage"]:>5.1f}x{r["half_spread_pct"]*100:>7.1f}%{r["oi"]:>7}{r["n_contracts"]:>6}{r["budget_use"]*100:>7.0f}%  {r["bucket"]}')

    print("\n--- 逐档细节 ---")
    for r in rows:
        print(f'\n▸ {r["label"]}  [{r["bucket"]}]')
        print(f'   {r["note"]}')
        print(f'   每口权利金 ${r["premium"]:,.0f}；每口等效持股 ${r["delta"]*cfg["spot"]*100:,.0f}')
        if r["n_contracts"] >= 1:
            print(f'   预算 ${cfg["budget"]:,.0f} → 买 {r["n_contracts"]} 口，花 ${r["spend"]:,.0f}'
                  f'（动用 {r["budget_use"]:.0%}），剩 ${r["leftover"]:,.0f}')
            print(f'   → 总等效持股 {r["eff_shares"]:,.0f} 股 / ${r["notional"]:,.0f}'
                  f'（同样的钱直接买正股只有 {r["spend"]/cfg["spot"]:,.0f} 股）')
        else:
            print(f'   预算 ${cfg["budget"]:,.0f} 买不到一口')
        print(f'   内在 ${r["intrinsic"]:.2f} / 时间价值 ${r["extrinsic"]:.2f}'
              f'（年化槓桿租金 {r["rent"]*100:.1f}%，对照融资利率 {cfg["rate"]*100:.1f}%）')
        print(f'   IV 每动 1 点 ≈ ${r["vega_1pt"]:,.0f}/口'
              f'（{r["n_contracts"]} 口 = ${r["vega_1pt"]*r["n_contracts"]:,.0f}）；'
              f'一买一卖价差成本 ≈ ${r["half_spread"]*200:,.0f}/口')
        print(f'   三关：{"全过 ✓" if not r["fails"] else "否决 → " + "；".join(r["fails"])}')
    print("\n※ 本表是筛选与风险计算工具，不是投资建议；下单与否由你决定。")


if __name__ == "__main__":
    main()
