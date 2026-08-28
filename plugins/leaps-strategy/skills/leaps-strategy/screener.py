#!/usr/bin/env python3
"""LEAPS 候选合约筛选器 —— 把 IBKR connector 抓回来的报价换算成决策数字。

IBKR MCP connector 不回传 greeks，只给 bid/ask/IV/OI。这支脚本用 Black-Scholes
自己算 delta，并把「时间价值占比 / 年化槓桿租金 / 实质槓桿 / 半价差成本」摊开，
最后依 A3-1 delta 框架 + A5-1 部位规模给出三关判定。

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
# A5-1 部位规模上限
MAX_SINGLE_PCT = 0.05      # 单笔权利金 ≤ 帐户 5%
MAX_SPEC_PCT = 0.10        # delta<0.25 的投机段总额上限
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

    max_ct_size = int((cfg["account"] * MAX_SINGLE_PCT) // premium) if premium else 0

    fails = []
    months = t * 12
    if not (DTE_MIN_MONTHS <= months <= DTE_MAX_MONTHS):
        fails.append(f"到期窗 {months:.1f} 月不在 {DTE_MIN_MONTHS}-{DTE_MAX_MONTHS} 月")
    if half_spread_pct > MAX_HALF_SPREAD_PCT:
        fails.append(f"半价差 {half_spread_pct:.1%} > {MAX_HALF_SPREAD_PCT:.0%}")
    if oi < MIN_OI:
        fails.append(f"OI {oi} < {MIN_OI}")
    if max_ct_size < 1:
        fails.append("单口权利金已超帐户 5% 上限")
    if delta < 0.25:
        fails.append("delta<0.25：投机区，需并入投机总额限制")

    return {
        "label": f'{cfg["symbol"]} {q["expiry"]} {strike:g}C',
        "months": months, "mid": mid, "delta": delta, "iv": iv,
        "intrinsic": intrinsic, "extrinsic": extrinsic,
        "ext_pct": extrinsic / mid if mid else 0,
        "rent": rent, "leverage": leverage, "vega_1pt": vega * 100,
        "half_spread": half_spread, "half_spread_pct": half_spread_pct,
        "oi": oi, "bucket": bucket, "note": note,
        "premium": premium, "max_contracts": max_ct_size,
        "theo": theo, "rich": mid - theo,
        "fails": fails,
    }


def main():
    src = sys.argv[1] if len(sys.argv) > 1 else "-"
    cfg = json.load(sys.stdin if src == "-" else open(src))
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
    print(f'A5-1 帐户 ${cfg["account"]:,.0f} → 单笔权利金上限 ${cfg["account"]*MAX_SINGLE_PCT:,.0f}\n')

    hdr = f'{"合约":<26}{"月":>5}{"中价":>8}{"delta":>7}{"IV":>7}{"时值%":>7}{"年租金":>8}{"槓桿":>6}{"半价差":>8}{"OI":>7}  {"分类"}'
    print(hdr)
    print("-" * len(hdr))
    for r in rows:
        flag = "  " if not r["fails"] else "✗ "
        print(f'{flag}{r["label"]:<24}{r["months"]:>5.1f}{r["mid"]:>8.2f}{r["delta"]:>7.2f}'
              f'{r["iv"]*100:>6.1f}%{r["ext_pct"]*100:>6.1f}%{r["rent"]*100:>7.1f}%'
              f'{r["leverage"]:>5.1f}x{r["half_spread_pct"]*100:>7.1f}%{r["oi"]:>7}  {r["bucket"]}')

    print("\n--- 逐档细节 ---")
    for r in rows:
        print(f'\n▸ {r["label"]}  [{r["bucket"]}]')
        print(f'   {r["note"]}')
        print(f'   每口权利金 ${r["premium"]:,.0f}；等效持股 ${r["delta"]*cfg["spot"]*100:,.0f}；'
              f'5% 上限下最多 {r["max_contracts"]} 口')
        print(f'   内在 ${r["intrinsic"]:.2f} / 时间价值 ${r["extrinsic"]:.2f}'
              f'（年化槓桿租金 {r["rent"]*100:.1f}%，对照融资利率 {cfg["rate"]*100:.1f}%）')
        print(f'   IV 每动 1 点 ≈ ${r["vega_1pt"]:,.0f}/口；'
              f'一买一卖价差成本 ≈ ${r["half_spread"]*200:,.0f}/口')
        print(f'   三关：{"全过 ✓" if not r["fails"] else "否决 → " + "；".join(r["fails"])}')
    print("\n※ 本表是筛选与风险计算工具，不是投资建议；下单与否由你决定。")


if __name__ == "__main__":
    main()
