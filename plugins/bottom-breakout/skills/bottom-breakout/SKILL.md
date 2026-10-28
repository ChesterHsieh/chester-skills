---
name: bottom-breakout
description: 底翻突破判讀：給一檔股票（美股、ADR、韓股等 IBKR 抓得到的），用 Murphy《Technical Analysis of the Financial Markets》的規則判斷是「上升趨勢中的回檔」還是「下跌後打底」，標出底部確認四步（① 前低、② 反彈高點 X、③ 回測、④ 帶量突破）、破底翻、反轉日、缺口、量能與報酬風險比；跨市場掛牌（原股＋ADR）時改用全球量能。當用戶問「可以上車了嗎」「底部確認了嗎」「這是 W 底／第二隻腳嗎」「破底翻」「有沒有帶量」「突破了嗎」「彈跳摸底」「書裡怎麼看」，或丟代碼問現在能不能買時觸發。台股平台整理型態用 tw-base-breakout。
---

# 底翻突破判讀

用戶的問題通常是：**這檔跌了一段，現在可以進了嗎？還是要等？**

答案用書裡的規則逐條檢查，數字全部由腳本算。不是投資建議、不下單，
**永遠不要呼叫 `create_order_instruction`**。

先讀 [references/rules.md](references/rules.md)（規則與頁碼）。這個 skill 是從 SK Hynix 2026-10 的案例長出來的，
[references/case-skhy-2026-10.md](references/case-skhy-2026-10.md) 記錄了當時判讀錯的地方，避免重犯。

## 1. 找合約

```
search_contracts(query="CRWD")
```
選 `symbol` 完全相符、主要掛牌的那一列，取 `underlying_contract_id`。

**ADR 要連原股一起抓**（例：SKHY → KRX `000660`，`exchange="KRX"`）。跨市場掛牌時，
查官方換股比例（WebSearch「<公司> ADR ratio」），全球量能要用到。

## 2. 抓資料

```
get_price_history(contract_id=..., security_type="STK", step="ONE_DAY",
                  period="ONE_YEAR", outside_rth=false)        # 韓股加 exchange="KRX"
get_price_snapshot(contract_id=..., market_data_names=["last","change","volume","high","low","bid_ask","top_status"])
```

把 `get_price_history` 回傳的 JSON **原樣**寫成檔案（例：`/tmp/crwd.json`），不要整包貼進對話或手動改數字。

**量的資料只用日線歷史**。`get_price_snapshot` 的 `volume` 會把夜盤、盤前盤後、其他交易平台都算進去，
SKHY 曾經差 30%（見案例）。snapshot 只拿來看現價和買賣價差。

## 3. 跑腳本

```bash
S="$SKILL/scripts"
python3 "$S/analyze.py" /tmp/crwd.json --symbol CRWD
python3 "$S/analyze.py" /tmp/skhy.json --symbol SKHY --event          # 今天是財報日
python3 "$S/global_volume.py" --adr /tmp/skhy.json --home /tmp/krx.json --k 0.1   # 跨市場
```

`$SKILL` 是這份 SKILL.md 所在的目錄。只用 Python 標準函式庫。`--json` 輸出機器可讀格式。

腳本做的事：
- 分模式：回吐整段漲幅 ≤ 2/3 是**回檔**，否則是**打底**
- 找 ①②③④；分辨雙重底和破底翻（③ 比 ① 低但不超過 3%）
- 只有一個低點、還沒回測時，列出下降過程中的反彈高點（第一關、第二關…）
- 價格過濾 1%、兩日規則、突破日相對量、上漲日／下跌日量比、OBV
- 底部反轉日、① 之後的向上缺口與回補狀態
- 下跌段的 33／38／50／62／66% 反彈位置、型態測量目標
- 報酬風險比：現價追進 vs 拉回到 X，結構停損 vs 緊停損

## 4. 回覆用戶

第一句直接回答能不能進：**可以分批進／只能小量試單／再等**，以及卡在哪一條。然後：

1. **模式**：回檔還是打底（兩種的進場標準不同，rules.md §1）
2. **階段表**：①②③④ 各自的價位、日期、有沒有過
3. **量**：關鍵日的相對量；跨市場的股票一定要附全球量能表
4. **報酬風險比**：現價追 vs 拉回 X。追價不到 3:1 就要明講，建議等拉回（p.401）
5. **劇本**：突破／盤整／跌破三種，各自的價位
6. **財報**：有沒有落在近期，跳空可能跳過停損

### 幾條硬規則（都是踩過的坑）

- **X 一定要是 ① 之後實際走出來的反彈高點**。下跌途中的壓力價位只能叫「第一關／第二關」，
  不能叫「底部確認」
- **收盤前不下「無量」結論**。量常集中在最後一小時和收盤競價，盤中按時間比例推估會嚴重低估
- **報酬風險比只看最近的目標**：緊停損配遠目標會算出 40:1 這種沒有意義的數字。
  第一個目標用最近的反彈高點或型態測量目標
- **用戶問「破了 X 就能買嗎」**：照書是收盤＋過濾＋量。只破價、沒量，最多小量試單（p.110、p.163）
- **跨市場掛牌**：不要只看一個市場的量就說帶量或無量，跑 `global_volume.py`。
  原則與限制見 [references/global-volume.md](references/global-volume.md)
- **夜盤價格**：用戶問夜盤價格可不可信，依 global-volume.md「夜盤價格可信嗎」三步驟，用數據回答
- **不要把 IBKR alert 當停損**：這個案例裡兩個條件都達到的 alert 都沒有通知。停損用停損單

### 用戶想要提醒時

可以用 `create_alert`，但先說清楚：
- alert 只在 IBKR Desktop 看得到，要不要寄 email 先問
- 盤中碰到就觸發，不能判斷收盤價，收盤條件寫進 `email_note`
- 實際案例中 alert 不一定會觸發，停損要靠掛單

## 5. 用戶不同意判讀時

問清楚是哪一段不一樣，對照 rules.md 的頁碼，調整 `scripts/analyze.py` 的 `Params`（每個門檻都有註解），
用測試資料確認沒有改壞：

```bash
python3 -m pytest -q "$SKILL/tests"
```

## 限制

- 只看日線量價，不看籌碼、基本面、大盤
- 低點、高點用相鄰 K 棒判斷（pivot），盤整很亂的時候可能抓到不是用戶眼中的那一個，要對照圖
- 規則來自一本 1999 年的書加上一個案例，**不是統計驗證過的勝率**
- 夜盤沒有歷史資料；韓股日線只算 KRX、不含 Nextrade
