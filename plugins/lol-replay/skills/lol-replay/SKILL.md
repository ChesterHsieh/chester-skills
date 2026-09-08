---
name: lol-replay
description: 解析並分析 League of Legends 的 .rofl 重播檔（ROFL2），回答「這場為什麼輸」「凱特琳為什麼輸出這麼低」「打野在幹嘛」「裝備／符文對不對」這類賽後檢討問題。只要使用者提到 .rofl、LoL／英雄聯盟重播、回放、replay、賽後數據、某場某個英雄表現如何，或直接丟一個 .rofl 路徑，就用這個 skill；即使問題只講了英雄名或位置（例如「下路怎麼了」）也要用。內建 Python CLI，能讀出全部賽後統計、對位比較、死亡／升級／購物時間軸與自動判讀，不需要遊戲畫面。
---

# LoL Replay（.rofl）分析

## 這個 skill 能做什麼、不能做什麼

能：從 .rofl 檔直接讀出 10 位玩家約 400 個賽後欄位（KDA、CS、金錢、各種傷害、視野、施法次數、裝備、符文…），
算出每分鐘值、對位差距、隊伍佔比；從 payload 抽出**死亡時間、升級時間、購物時間、每分鐘操作量**；
在有該版本規格檔（`lolreplay/specs/<patch>.json` 與 `specs/<patch>/packet_*.json`，目前 16.17）時還能還原：
- **每個英雄任意時刻的座標**（死亡地點、當時附近敵我、每波交戰是否在場、任意兩人的距離）
- **擊殺者**（每次死亡被誰殺、死在哪）
- **購買順序**（時間、道具、價格、買後剩餘金錢）
- **技能施放**（Q/W/E/R 含二段、召喚師技能、道具主動、飾品眼、回城；目標、施放位置、指向點。普攻不在施法封包裡）

全部離線、純 Python，不需要 Riot API 或遊戲客戶端。

不能（目前）：血量曲線、傷害逐筆明細、技能是否命中（只知道指向點與目標，命中要用 `near` 佐證）、物件（眼、塔）狀態。回答時遇到就明說「這版還沒有」。
遇到沒有規格檔的版本，位置／擊殺者／購買／施法區塊會缺席，其餘功能不受影響。細節見 `references/rofl2-format.md` 與 `references/payload-crypto-notes.md`。

## 環境

```bash
pip install zstandard   # 唯一的第三方依賴（解 payload 用；只讀 metadata 時不需要）
```
CLI 在本 skill 目錄的 `scripts/rofl_cli.py`，用 `python3` 執行；下面的 `$SKILL` 代表本 skill 的目錄。

## 工作流程（一次解析，整段對話重複使用）

1. **開 session**：拿到 .rofl、對象玩家、想診斷的問題後，先跑一次；它會解析整個檔案、把精簡摘要印出來，並在 `~/.cache/lol-replay/<game>/` 留下 `context.md` 與 `game.json`。**把印出的內容當成這段對話的 context 直接回答**，不必再跑 summary／player。
   ```bash
   python3 $SKILL/scripts/rofl_cli.py session "/path/to/game.rofl" --focus "凱特琳" --question "為什麼輸出這麼低"
   ```
   `--focus` 可以是中文名、英文名、常見暱稱、位置（`紅方打野`、`blue adc`）、Riot ID，錯字也能模糊比對（「凱特零」會對到凱特琳）；對應不到時 stderr 會列候選，用編號重跑。使用者沒指定對象就省略 `--focus`，之後再補跑一次即可。
2. **依 `references/analysis-playbook.md` 的檢查順序寫結論**：先講最大的 2–3 個原因，每個原因引用具體數字並和對位比，最後給可執行的建議。自動判讀（🔴🟠🟡ℹ️）是線索不是答案，要把旗標之間的關係串起來（例如「經濟其實領先，但傷害/1000 金只有全場的 6 成 → 問題在參戰而不是發育」）。位置區塊能直接回答「那波團戰他在哪」「為什麼會死」：死亡地點、當時 1200 內的敵我、每波交戰是否在場都已列出。
3. **追問時用查詢指令**，它們讀取最近一次 session，不必再給檔案路徑（要查別場再加 `--file`）：
   ```bash
   python3 $SKILL/scripts/rofl_cli.py where 凱特琳 7:20              # 座標、區域、移速、3000 內的敵我
   python3 $SKILL/scripts/rofl_cli.py near 凱特琳 5:47 --radius 1500  # 半徑內的英雄與距離
   python3 $SKILL/scripts/rofl_cli.py track 凱特琳 5:30 6:00 --step 10 # 一段時間的軌跡
   python3 $SKILL/scripts/rofl_cli.py fight 7:20 --window 15          # 全員位置與前後 15 秒的死亡
   python3 $SKILL/scripts/rofl_cli.py kills --who 凱特琳               # 她的每次死亡：被誰殺、在哪；以及她的最後一擊
   python3 $SKILL/scripts/rofl_cli.py items 凱特琳                     # 購買順序、價格、買後剩餘金錢
   python3 $SKILL/scripts/rofl_cli.py casts 凱特琳 5:30 6:00           # 這段時間放了什麼技能、對誰、在哪
   ```
   問題只靠 context 就能回答時不必呼叫；需要精確時刻、距離、軌跡時才呼叫。
4. 其他指令（不開 session 也能用）：`summary`、`player FILE WHO`、`timeline`、`meta --keys`；加 `--json` 得到結構化資料，`--no-payload` 只讀 metadata 秒回。

## 讀報告時的重要脈絡

- **加速模式**：開場即 L3、17 分鐘 ADC 就有 14000 金，是 Swiftplay 類快速模式（會出 `accelerated_mode` 旗標）。此時 CS/分之類的絕對門檻不準，以對位與全場平均為準；短局也不代表投降。
- **總量 vs 效率**：短局所有人的總傷害都低，要看傷害/分、隊伍佔比、傷害/1000 金。
- **`Missions_*` 之類的欄位多數是任務進度**，不是數據（`Missions_CreepScoreBy10Minutes = 1` 不是 10 分鐘只補 1 隻）。可信欄位與陷阱見 `references/stat-fields.md`。
- 傷害/1000 金低但金錢高 → 「有錢沒打出來」（參戰少、打不到人、傷害打在建築）；金錢也低 → 發育問題。兩者結論不同。
- 每分鐘操作量是「指向該英雄的網路事件數」，第 1 格是 0:00–0:59；只能看相對高低（哪幾分鐘特別低＝閒置／死亡／回城）。
- `player` 報告已包含 Q/W/E/R 分項施放、召喚師技能各用幾次、未花費金錢、投降旗標（在 summary 標頭），不需要再跑 `meta` 撈。
- 交戰時間軸的「在場／不在場」來自位置（<1500 視為在場），擊殺者來自擊殺封包；兩者都有時可以直接寫「那波她在場但只放了 Q」。沒有規格檔的版本只知道「誰在那波陣亡」，不要寫成「她沒參加那波團」。
- 技能施放的「指向點」是玩家點的位置，不代表命中；要說「放空」請用 `near` 看指向點附近有沒有敵人。普攻不在施法封包裡（在另一種尚未解的攻擊封包），所以「有沒有在打人」要看傷害數據與位置，不能用施法次數。少數技能名稱反查不到會顯示「未知技能#hash」。

## 換版本時

封包 type 每個 patch 都會重新編號。工具會用每人死亡數／等級／購買次數自動比對並存到 `lolreplay/profiles/<patch>.json`；
若時間軸區塊空白或 stderr 出現 `packet profile unverified`，跑：
```bash
python3 $SKILL/scripts/rofl_cli.py calibrate "/path/to/game.rofl"
```
研究新封包用 `packets` 子命令（`--type`、`--player`、`--keyframe`、`--limit`）。

## 回答格式（建議）

```
**結論**：一句話講最大原因。
**證據**：2–4 點，每點一個數字＋對位或全場對照（例：傷害/分 567 vs 對位 541、全場 740）。
**時間軸**：關鍵死亡／交戰／升級時間點（來自 payload）。
**建議**：2–3 個具體可做的事。
**沒有的資料**：（若使用者的問題需要走位／購買順序等）一句話說明並建議看重播或 Riot API timeline。
```

## 檔案

- `scripts/rofl_cli.py` — 入口（session / where / near / track / fight / summary / player / timeline / packets / calibrate / meta）
- `lolreplay/` — 解析與分析套件；`lolreplay/data/` 是 Data Dragon 16.17 的中英對照表（含每位英雄的技能腳本名）；`lolreplay/specs/` 是各版本的 payload 規格（`<patch>.json` + `<patch>/packet_<type>.json`）；`lolreplay/payload/` 是純 Python 的封包內容解碼（`generic.py` 通用解碼器、`events.py` 擊殺／購買／施法欄位）、`positions.py` 路徑、`spells.py` 技能 hash 反查
- `tools/re/` — 產生規格檔用的離線逆向工具（需要遊戲執行檔與 Unicorn，skill 執行時不需要）
- `references/analysis-playbook.md` — 各類問題的檢查清單與寫法
- `references/stat-fields.md` — 欄位意義與陷阱
- `references/rofl2-format.md` — 檔案格式（reverse engineering 結果）
- `tests/` — `python3 -m pytest -q`
