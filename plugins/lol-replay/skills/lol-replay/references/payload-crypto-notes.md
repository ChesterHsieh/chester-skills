# Payload 封包內容逆向筆記（進行中）

目標：不靠遊戲畫面，直接從 .rofl 的封包內容讀出座標、道具、擊殺者等資料。
本文件記錄 2026-09 對 patch 16.17（TW2-443949239.rofl）的實測結果，供後續繼續攻堅。

## 已確定的事實

1. **Envelope 是明文**（time／u16 type／net_id），內容（content）是逐 type 混淆過的。
2. **參考專案 Mowokuma/ROFL**（Rust，2025-11 封存）的 block 解析與本專案完全一致，
   但它**不解演算法**：把 Windows 客戶端 `League of Legends.exe` 的 `.text/.data/.rdata` dump 成 `.patch`，
   用 Unicorn 模擬器直接執行客戶端裡該封包的解碼函式，只做了移動路徑與插眼兩種封包，每個 patch 要重新找函式位址。
   它給出的**解碼後**移動封包結構：
   `u16 parsing_type｜u32 net_id｜f32 speed｜[u8]｜bitmask｜waypoints(u16 x,y 或 i8 delta)`，
   世界座標 = i16 × 2 + (7358, 7412)。
3. 混淆**不是**：全域 XOR、逐位置 XOR、鏈式（CBC 式）、單一全域替換表。
   - 逐位置 XOR 被否定：升級封包 byte1 相鄰等級差好幾個 bit（`2c/1f/53/72/d5/17/79/9c/6a/3e/df/ae/18` 對應 L4–L16）。
   - 鏈式被否定：死亡封包 `5x fc YY 4b 4b` 只有 byte0 與 byte2 變動，尾端恆定。
   - 全域替換表被否定：`0x00` 在 743 映成 `4b`、在 732 映成 `f3`。
4. **每個欄位各用不同的運算**（推測是程式碼產生的逐欄位混淆）：
   - 767（移動）倒數第 2 byte = 英雄 id，10 個英雄的值 `2a 0a ad 8d ac 8c 2d 0d 2c 0c` 對應 net_id 低位 `ae..b7`，
     完全符合「bit 重排 + XOR」（明文 bit0→密文 bit5、bit1→bit0、bit2→bit7、bit3/4→bit1/2）。
   - 107 offset 11（開場時 = 自身 net_id 低位）值 `5e 5d 4c 4b 4a 49 48 47 46 45`，符合「逐 nibble 相減」
     （hi' = f − hi，lo' = (c − lo) mod 16），不符合任何 XOR 線性模型。
   - 1151（升級）byte1 = F(等級)，F 對 1～3 層 {xor, add, sub, mul, rotl, bitrev, nibble-swap, not, nibsub} 組合暴力搜尋無解 → 查表或更複雜的組合。
5. 已識別的封包 type（16.17）：743／732 死亡（內含依等級的復活時間，`0x41→0x42` 指數變化在兩個封包同步出現）、
   1151 升級、877 技能升級（時間與升等完全對應）、777 購物、767 移動路徑、35 史詩野怪擊殺、966 小兵 2-byte 狀態。

## 未解的問題

- 767 的座標欄位位置：泉水出發的封包在同隊之間沒有共同 byte，推測座標是相對於該單位上一筆狀態的差值，或欄位順序與解碼後不同。
- 1151 byte1 的運算。
- 每個欄位的運算都要個別以已知明文破解，缺乏足夠已知明文的欄位（座標、道具 ID）在統計上不可行。

## 可行的路線

A. **模擬客戶端**（Mowokuma 路線）：取得對應 patch 的 `League of Legends.exe`（Riot CDN 的 rman manifest 可只下載該檔），
   用反組譯找出各封包解碼函式的 RVA，以 Unicorn（Python 有 binding）餵入 content 取回結構。
   每個 patch 都要重做定位；工作量以「天」計，且需要反組譯工具。這台 Mac 沒有安裝客戶端，也沒有 Windows 版檔案。
B. **逐欄位密碼分析**：只對有大量已知明文的欄位可行（id、等級、復活時間），座標與道具 ID 不可行。
C. **接受目前的 envelope 層**：死亡／升級／購物／活動量時間軸（已完成）。

## 2026-09-07 進度：客戶端靜態分析（等級 2 路線）

- 取得 16.17.8104348 的 Mac 版遊戲執行檔：sieve 端點 `version-sets/TW2?q[platform]=macos` → `lol-game-client` manifest `A85CF8825A9BC0A4`，
  用自寫的 Python RMAN 解析器（`scratchpad/rman.py`：zstd 多 frame body + FlatBuffers）只下載 `LeagueofLegends.app/Contents/MacOS/LeagueofLegends`（81 MB，universal x86_64+arm64）。
- 分析 x86_64 slice。`LC_FUNCTION_STARTS` 有 171,627 個函式起點（記得加 imagebase 0x100000000）。
- **解碼分派器**：函式 `0x1012f9b70`，跳躍表 `0x101306868`，以 type id（0..1262）為索引，覆蓋 937 個 id，replay 的 245 種 type 全部命中。
- 另一個表 `0x100790fec`（函式 `0x10077ea40`，`type−7` 為索引，274 個 id）是解碼後的邏輯 handler 分派器；case 1185 呼叫 StartSpawn handler。
- 封包內容格式：存在旗標 byte（bit0–2 直接是布林欄位，其餘 bit 表示後續可選欄位是否存在）＋ 只寫出的欄位，多 byte 整數以 big-endian 組裝；每個欄位再套產生式的混淆運算。

## 2026-09-08 封包對照（patch 16.17，經模擬器驗證）

| type | 意義 | 關鍵欄位（物件偏移） |
|---|---|---|
| 1262 | **WaypointGroup 路徑點**（寄給 net_id 0，每秒約 22 筆） | blob：每筆 `u16 type｜u32 net_id｜f32 speed｜bitmask｜i16/i8 路徑點`，座標 = i16×2 + (7358, 7412) |
| 217 | **施法／攻擊**（CastSpellAns） | +0x18 施法者、+0x48 技能 hash(varint)、+0x6c 時間、+0x7c/0x80/0x84 與 +0xa8/0xac/0xb0 座標、+0x90/0x98 方向、+0xb4 目標、+0xf0/f4/f8 終點 |
| 767 | 攻擊指令類（目標 net_id + hash + float） | +0x10 目標、+0x2c 自己 |
| 1189 | 實體狀態 blob（HP 等 big-endian float） | blob |
| 743 / 732 | 死亡（復活秒數 f32 BE） | +0x10 |
| 1151 | 升級（技能點數、等級） | +0x10, +0x11 |
| 877 | 技能升級 | |
| 306 | 物件清單類批次封包 | blob |

模擬 harness：`scratchpad/emu.py`（Unicorn，只離線用），工廠 `0x1012f9b70`、跳躍表 `0x101306868`；
helper 對照表可用 `helper_probe.py`／`varint_probe.py`／`blob_helper_probe.py` 自動抽出（固定 4-byte 為單一 256 表 + big-endian；varint 為 7-bit 表 + 接續位元）。

## 2026-09-08 規格產生器（`tools/re/specgen.py`）與已驗證的封包內容

純 Python 解碼器 `lolreplay/payload/generic.py` 吃 `lolreplay/specs/<patch>/packet_<type>.json`，規格由 `specgen.py` 離線產生、`validate.py` 對模擬器逐封包驗證。

### 封包內容的通用模型（16.17 客戶端 codegen）

- 內容 = 旗標 bytes（little-endian bit 流）＋ 依「分派順序」排列的欄位。每個欄位對應 1／2／3 個 bit 的群組；模式決定：常數（stored domain）、helper 讀取（固定 K byte／varint／blob／list）、內聯讀取、巢狀物件（自己的旗標 bytes）、或不寫。
- 群組不一定對齊 byte，例如 777 巢狀物件的 bit 15–16 是跨 byte 的 2-bit 群組。`casescan.py` 用模擬「索引計算區塊」找出每個 `jmp reg` 分派點的旗標位元與跳躍表，再靜態解析每個 case（`lea rdi,[this+OFF]; call H`＝helper、`mov [this+OFF], imm`＝常數、`call [rax+8]`＝巢狀、暫存器來源的 store＝內聯）。這比純動態旗標翻轉可靠：等價的 helper 變體在動態比較下看不出差異。
- **plain 的定義**：helper 累加後、tail 轉換前的值。有些 helper 會先經過 bswap 包裝函式（`0x10170e140`／`0x10170e150`／`0x10170e160`，16／32／64 bit）再進 tail；不看這一步會把 400.0 讀成 0x0000c843。specgen 用 code hook 記錄這些呼叫，validate 也接受 bswap 對照。
- vec3：一個 helper 讀 12 byte 寫三個 4-byte 分量，分量的 payload 順序不一定是 x,y,z（方向向量是 z,y,x）；規格用 `fixedv` 記錄各分量對應的 payload 位置。
- 向量／字串：計數是 7-bit varint（逐 byte 轉換表）＋ count×elem byte，可能內聯在反序列化器裡；規格種類 `vector`。
- 每個群組的「安靜模式」（0 消耗）用逐群組驗證取得（靜態猜測 → 跑 → 以總消耗量最小的模式為準），filler byte 依 helper 的 varint 終止位元選擇（0x00 對某些 varint 是接續位元，會讀到天荒地老）。

### 已驗證欄位（root 偏移；巢狀物件已攤平）

| type | 欄位 | 驗證 |
|---|---|---|
| 732 擊殺 | +0x10 復活秒數 f32、+0x18 擊殺者 net_id(varint)、+0x24/+0x28/+0x2c 死亡座標 (x, 高度, z) | 48/48 封包（+0x40 一個不需要的 varint 有 36 筆對不上模擬器的「倒數第二次寫入」，疑似參考值本身不對） |
| 777 購買 | +0x10 購後金錢 f32、+0x4c 道具 id(varint)、+0x50 價格 f32 | 165/165 |
| 217 施法 | +0x18 施法者、+0x48 技能 hash、+0x6c 施放時間、+0x90/0x94/0x98 方向、+0xa8/0xac/0xb0 起點、+0xb4 目標 net_id、+0xe4 時間、+0xf0/0xf4/0xf8 終點 | 1674 封包：674 全欄位一致；施法欄位（到終點為止）≥1597 一致，尾端 +0x138/+0x13c/+0x140 三個不需要的 varint 在約半數封包不符、77 筆在尾端解碼失敗 → `decode_cast` 用 partial 解碼只取前面的欄位 |

### 技能 hash

`+0x48` 是 League 傳統的 spell hash：對小寫腳本名做 ELF hash（28 bit）。實測 `IreliaE` → `0x008c2f05` 與模擬器解出的值完全一致。
`lolreplay/spells.py` 用 Data Dragon 的每位英雄 Q/W/E/R/被動腳本 id（`tools/fetch_champion_spells.py` 產生 `data/champion_spells.json`）、召喚師技能 id、`<Champ>BasicAttack*`／`CritAttack*`、`Recall` 建反查表。
