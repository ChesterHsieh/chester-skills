---
name: leaps-strategy
description: 针对某档股票制定 LEAPS（长天期 call）槓桿策略，并从 IBKR connector 抓真实选择权链，产出可下单的候选合约清单。当用户丢一个股票代码说要做 LEAPS／长天期 call／选择权槓桿／option 策略／PMCC 长腿，或问「XXX 该买哪个履约价／哪个到期日的 call」时触发。产出含 delta、时间价值占比、年化槓桿租金、实质槓桿、价差成本与三关检查判定。
---

# LEAPS 策略与候选合约筛选

用户的核心论点：**长期看好某标的，但资金规模有限，所以用 deep ITM 长天期 call 当槓桿工具**——不是赌方向的彩券，是「融资持股的替代品」。

所有门槛都来自 [references/playbook.md](references/playbook.md)（从用户自己的交易检讨报告萃取）。**先读它**，再往下做。核心分水岭：赚钱的 call 进场时近 ATM／偏 ITM，赔钱的是深度 OTM。

> 这个 skill 做筛选与风险计算，**不做投资建议、不下单**。永远不要呼叫 `create_order_instruction`。最后一步是把候选清单交给用户自己决定。

## 流程

### 1. 边界检查

标的是美股吗？不是就先提醒 playbook 的系统边界（槓桿 ETP／ETF、时区外市场已暂停，证据是约 −£11k）。用户坚持再继续。

### 2. 抓标的现况（IBKR connector）

```
search_contracts(query="NVDA")
```
选 **symbol 完全相符** 那一列（`NVDA` 而不是 `NVDL`/`NVDU`/`NVDY` 这些槓桿或收益 ETF——它们同样有 OPT section，很容易选错），取其 `underlying_contract_id`。

```
get_price_snapshot(contract_id=<underlying>, market_data_names=[
  "last","bid_ask","implied_vol_underlying","historical_vol",
  "implied_volatility_percentile","misc_statistics"])
```
- `implied-volatility-percentile.high_52w` 是**价格关的 IV 门槛数字**（分数，0.048 = 4.8 百分位 = 很便宜）
- 比对 `implied-vol-underlying.annual_iv` 与 `historical-vol.annual_pct` 判断 IV 相对 HV 贵贱

### 3. 挑到期日

```
get_option_parameters(underlying_contract_id=<id>)
```
只看 `regular: true` 的月选，挑 **今天起 12–24 个月**的。有 `trading_class` 重复日期时（`NVDA` vs `2NVDA`）选不带前缀的标准链。把 `id` **原样**传下去，别自己拼字串。

### 4. 抓链与逐档报价

```
get_option_data(expiration_id=<verbatim id>, min_strike=..., max_strike=...)
```
range 以 spot 为中心，往下涵盖到 **delta 0.7 左右的履约价**（约 spot 的 0.6–0.9 倍）。

`get_option_data` **只回合约结构，没有价格也没有 greeks**。每个候选履约价要各打一次：

```
get_price_snapshot(contract_id=<call_contract_id>, market_data_names=[
  "bid_ask","implied_vol","option_open_interest","option_volume"])
```

### 5. 跑 screener（关键步骤）

connector **不回传 delta**，所以自己用 Black-Scholes 算。把上面抓到的东西写成 JSON：

```json
{
  "symbol": "NVDA", "spot": 226.72, "asof": "2026-08-28",
  "account": 130000, "rate": 0.04, "div_yield": 0.0002,
  "iv_percentile": 4.8,
  "earnings_date": "2026-11-19",
  "quotes": [
    {"expiry":"20280121","strike":140,"bid":100.90,"ask":103.00,"iv":0.4316,"oi":3893,"volume":0},
    {"expiry":"20280121","strike":165,"bid":80.45,"ask":85.50,"iv":0.4157,"oi":1261,"volume":0},
    {"expiry":"20280121","strike":190,"bid":67.65,"ask":69.50,"iv":0.4064,"oi":8603,"volume":0}
  ]
}
```
价格一律**每股**（不是每口）。`iv` 取 `implied-vol.annual_iv`。`account` 没给就问用户帐户规模——没有它算不出部位上限。

```bash
python3 plugins/leaps-strategy/skills/leaps-strategy/screener.py quotes.json
```

产出（上面这份 NVDA 真实报价的实际输出）：

```
合约                            月      中价  delta     IV    时值%     年租金    槓桿     半价差     OI  分类
✗ NVDA 20280121 140C       16.8  101.95   0.90  43.2%  14.9%    4.8%  2.0x    1.0%   3893  深度 ITM（超框架）
✗ NVDA 20280121 165C       16.8   82.97   0.84  41.6%  25.6%    6.7%  2.3x    3.0%   1261  替代持股
✗ NVDA 20280121 190C       16.8   68.58   0.77  40.6%  46.5%   10.0%  2.5x    1.3%   8603  替代持股
```

每档还会印出：每口权利金、等效持股金额、5% 上限下最多几口、内在/时间价值拆解、年化槓桿租金 vs 融资利率、IV 每动 1 点的美元影响、一买一卖的价差成本、三关判定与否决原因。

### 6. 交付

给用户一份候选清单：**每档的定位（核心/加速/投机）、通过或否决的原因、建议口数、限价起点（中价）**，加上论点与催化剂日期、预定处理日（剩 6–9 个月）、出场规则（+100% 卖一半；delta<0.15 认赔）。

## Gotchas（实测踩到的）

- **`option_midpoint_iv` 在长天期合约上回传 `isValid: false`、annualIv 是负数**（实测 NVDA Jan'28 140C 回 `-15.87`）。改用 `implied_vol` 的 `annual_iv`，那个才有效。
- **snapshot 回应的 key 是连字号，不是底线**：请求 `option_open_interest` → 回应是 `option-open-interest`。
- `get_option_data` 不含价格／greeks，得逐档 snapshot——所以 range 要收窄，否则打爆呼叫数。
- **LEAPS 常常整天零成交**（`volume: 0`），流动性要看 **OI 而不是 volume**。实测 Jan'28 165C 的 OI 只有 1261 且半价差 3.0%（$505/口来回成本），190C 的 OI 8603 半价差却只有 1.3%——**OI 高的履约价（整数关卡、100 的倍数）价差明显更好，别只挑 delta 最漂亮的**。
- **高价股会全数被资金关否决**：$226 的 NVDA，delta 0.77 那口权利金就 $6,858，在 $130k 帐户的 5% 上限（$6,500）下一口都买不起。这不是 bug，是 playbook 的结论。这时给用户三条路：换较低价的标的、改用 debit spread（A6-4）、或明确写下这次的规则例外。**别默默放宽 5%。**
- `search_contracts("NVDA")` 会回一堆 `NVDL`/`NVDU`/`NVDY`/`NVDB` 槓桿与收益 ETF，全都有 OPT section。必须用 symbol 完全相符 + `country_code: US` 筛。
- 报价延迟／盘後会让 `bid_ask` 回空物件；此时 `last.is_close: true`，要跟用户说明这是收盘价不是即时报价。

## 不要做的事

- 不呼叫 `create_order_instruction`、不下单、不改单。
- 不讲「建议买入」这类个人化投资建议；讲的是「这档在框架内／框架外，因为哪个数字」。
- 不为了让某档通过而放宽 screener 门槛。门槛是用户自己的教训换来的。
