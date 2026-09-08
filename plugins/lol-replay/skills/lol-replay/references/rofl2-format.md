# ROFL2 檔案格式（實測還原，patch 16.17）

`.rofl` 是 League of Legends 客戶端下載的重播檔。2023 年後的檔案是 ROFL2 格式，magic 為 `RIOT\x02\x00`。
本文件是 `lolreplay/` 解析器的依據，全部由真實檔案逐 byte 驗證（52 個壓縮 frame 全部剛好解析到結尾）。

## 1. 整體配置

| 區段 | 位置 | 內容 |
|---|---|---|
| Magic | 0–5 | `52 49 4F 54 02 00` = `RIOT\x02\x00` |
| 未知 | 6–13 | 8 bytes（hash／nonce，每檔不同） |
| 遊戲版本 | 14 | u8 長度 + ASCII 字串，例如 `16.17.810.4348` |
| Frames | 緊接版本字串 | 連續的 `[17-byte header][data]`，見 §2 |
| 簽名 | frames 之後 | 256 bytes 高熵資料（RSA 簽名） |
| Metadata | 簽名之後 | UTF-8 JSON，見 §4 |
| Metadata 長度 | 檔案最後 4 bytes | u32 LE；`metadata = file[-4-len : -4]` |

先讀檔尾 4 bytes 拿到 metadata 長度，就能只讀 metadata 而不用碰 payload。

## 2. Frame header（17 bytes，little-endian）

```
u32 id                 chunk 或 keyframe 的編號（chunk 與 keyframe 各自從 1 起算）
u32 next_id            下一個同類 frame 的編號
u8  type               1 = chunk（實況串流，約 30 秒一個）
                       2 = keyframe（狀態快照，約 60 秒一個，用來 seek）
                       3 = startup（單一 block，內容另有壓縮／混淆）
                       4 = raw chunk（compressed_len = 0，資料未壓縮；實測只有 chunk 2，17 bytes）
u32 uncompressed_len   解壓後長度
u32 compressed_len     zstd frame 長度；0 表示資料以原始形式存放 uncompressed_len bytes
```

data 是**單一 zstd frame**（magic `28 B5 2F FD`），**沒有** ROFL1 時代的 Blowfish 加密。
`lastGameChunkId` / `lastKeyFrameId`（metadata）＝ chunk / keyframe 的最大 id。

## 3. Block 串流（解壓後的 chunk／keyframe 內容）

一個 frame 是連續的 block，每個 block：

```
u8  marker      flag bits：
                0x80  time 為 u8，相對前一個 block 的毫秒差；否則為 float32 絕對秒數
                0x10  content 長度為 u8；否則 u32
                0x40  省略 type（沿用前一個 block）；否則 u16 packet type
                0x20  net_id 為 int8 差值（相對前一個 block 的 net_id）；否則 u32
                0x0F  channel（實測 1、2、3）
[time]  u8 | f32
[len]   u8 | u32
[type]  u16（可省略）
[net]   i8  | u32
content len bytes
```

- 每個 chunk 通常只有第一個 block 帶絕對時間（chunk 起始秒數），其餘全是相對毫秒差；30 秒的 chunk 累加起來正好 ≈ 30000 ms。
- keyframe 的所有 block 時間都等於 keyframe 時間（快照）。
- `net_id` 是遊戲物件 ID：`0x40000000 + n`；`0` 是廣播。10 位玩家英雄的 net_id 連號（本檔 `0x400000AE`～`0x400000B7`），**順序與 metadata 的 statsJson 玩家順序一致**（用每人死亡數向量驗證，完全吻合）。
- packet `type` 是 u16，每個 patch 都會重新編號，本檔共 250 種。

## 4. Metadata JSON

```json
{"gameLength": 1011546, "lastGameChunkId": 36, "lastKeyFrameId": 17, "statsJson": "[ {...}, ... ]"}
```

`statsJson` 是字串，再 `json.loads` 一次得到 10 個玩家的賽後統計（約 400 個欄位，值全是字串）。
順序：藍方 (TEAM=100) TOP/JUNGLE/MIDDLE/BOTTOM/UTILITY，再紅方 (200)。欄位意義見 `stat-fields.md`。

## 5. Packet 內容：能與不能

content 的 byte 是**逐 type 混淆**過的：同一 type 固定位置的 byte 恆定（例如死亡封包 5 bytes 只有兩個 byte 會變），
但不同 type 的固定 byte 不同，也找不到全域 XOR key；座標、道具 ID、擊殺者 net_id 都無法直接以明文或固定 XOR 讀出。
因此本專案只用 **envelope**（time / type / net_id），不解 content。

能取得的事件（靠 `calibrate.py` 用 metadata 數字自動比對 type）：

| 事件 | 判定方式 | 16.17 的 type |
|---|---|---|
| 死亡 | 每英雄 block 數 == `NUM_DEATHS` 向量 | 732（詳細，31–37 bytes）、743（5 bytes） |
| 升級 | 每英雄 block 數 == `LEVEL - 1` | 1151 |
| 購物／裝備變動 | 每英雄 block 數 ≈ `ITEMS_PURCHASED`（±4） | 777 |
| 操作量 | 每分鐘指向該英雄的 block 數 | 全部 type |

其他觀察（未使用）：type 966 是全場最多的 2-byte 封包（小兵移動類）、type 35 = 史詩野怪擊殺（只出現在打野身上，數量 = DRAGON_KILLS）、type 877 ≈ 技能升級。

## 6. ROFL1（舊格式）

magic `RIOT\x00\x00`，header 為 `6 + 256 簽名 + <u16 header_len, u32 file_len, u32 meta_off, u32 meta_len, u32 payload_header_off, u32 payload_header_len, u32 payload_off>`，
metadata 直接在 `meta_off`。payload 需以 game id 派生的 Blowfish key 解密再 gzip，本專案只讀 metadata。
