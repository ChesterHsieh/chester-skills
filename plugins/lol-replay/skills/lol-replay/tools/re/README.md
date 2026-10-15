# tools/re — 產生新版本的封包規格

skill 執行時不需要這些工具；它們只在換 patch 時用來產生 `lolreplay/specs/<patch>.json` 與 `lolreplay/specs/<patch>/packet_<type>.json`。
原理：下載該版 macOS 客戶端，用 Unicorn 模擬客戶端自己的封包解碼函式，把它當成標準答案，量出每個欄位的讀法與逐 byte 對照表，
輸出成 `lolreplay/payload/generic.py` 吃的 JSON。細節與歷史見 `references/payload-crypto-notes.md`。

```bash
pip install unicorn capstone lief zstandard
export LOL_REPLAY=/path/to/game.rofl            # 新版本的任一場 replay（用來找封包 type、驗證）
cd tools/re

# 1. 下載同版本的客戶端執行檔（約 80 MB；只有該版還是線上版本時抓得到）
python3 fetch_client.py "$LOL_REPLAY" --region TW2 --out ~/lol-client
export LOL_CLIENT=~/lol-client/LeagueofLegends

# 2. 自動偵測封包工廠／配置器／基底解碼函式（結果快取在執行檔旁）
python3 emu.py

# 3. 找出各角色的封包 type 候選
python3 roles.py types

# 4. 對候選 type 產生規格（施法封包約 10 分鐘）
P=16.20
python3 specgen.py --out ../../lolreplay/specs/$P 421 63 1215 61

# 5. 驗證：真實封包與客戶端逐欄位比對、隨機旗標模糊測試
python3 validate.py 1215 ../../lolreplay/specs/$P/packet_1215.json
python3 fuzz.py 1215 ../../lolreplay/specs/$P/packet_1215.json 300

# 6. 用賽後數據自動找欄位偏移，把輸出的 packets / waypoint / fields 寫進 lolreplay/specs/$P.json
python3 roles.py fields --specs ../../lolreplay/specs/$P --kill 421 --shop 63 --cast 1215 --waypoint 61
```

`specs/<patch>.json` 的其餘欄位：`patch`、`waypoint_type`（＝路徑點封包 type）、`map_center`（目前一直是 `[7358, 7412]`）、`notes`。
最後在 `tests/` 加幾筆該版真實封包的回歸測試（參考 `tests/test_spec_1620.py`）。

| 檔案 | 作用 |
|---|---|
| `fetch_client.py` | replay 版本 → Riot sieve → RMAN manifest → 只下載遊戲執行檔 |
| `client.py` | 讀 Mach-O x86_64 slice（segments、函式起點、import stubs） |
| `disasm.py` | Capstone 反組譯、call 目標解析；`python3 disasm.py ADDR [N]` 看程式碼 |
| `emu.py` | Unicorn harness：位址自動偵測、配置器與 libc stub、`Deser` 跑真實封包 |
| `explore.py` | 攔截讀取函式、逐 bit 翻旗標，找出欄位群組與各模式的動作 |
| `readers.py` | 讀取函式黑箱量測：固定長度／vec3／varint／byte 陣列／物件陣列的對照表，明文擷取規則 |
| `specgen.py` | 組成完整規格（巢狀物件、物件陣列、行內欄位、分派順序、常數明文） |
| `calltrace.py` | 在真實封包上追蹤每個讀取函式的寫入（validate 的標準答案） |
| `validate.py` / `fuzz.py` | 規格 vs 客戶端：真實封包、隨機旗標 |
| `roles.py` | 封包 type 候選與欄位偏移的自動判定 |
| `replay.py` | 讀 `$LOL_REPLAY` 的 blocks（快取在 replay 旁） |
