# statsJson 欄位速查與陷阱

所有值都是字串，`Player.int()` 會轉整數。單位：時間欄位為秒，傷害／金錢為整數。

## 身分
| 欄位 | 意義 |
|---|---|
| `SKIN` | 英雄英文 key（Caitlyn、Khazix、MonkeyKing…），對照 `lolreplay/data/champions.json` 取中文名 |
| `RIOT_ID_GAME_NAME` / `RIOT_ID_TAG_LINE` | Riot ID；`NAME` 通常相同 |
| `TEAM` | 100 藍方、200 紅方 |
| `TEAM_POSITION` / `INDIVIDUAL_POSITION` | TOP / JUNGLE / MIDDLE / BOTTOM / UTILITY |
| `WIN` | `Win` / `Fail` |
| `PUUID`, `SUMMONER_ID`, `ID` | 帳號識別 |

## 戰鬥
| 欄位 | 意義 |
|---|---|
| `CHAMPIONS_KILLED` / `NUM_DEATHS` / `ASSISTS` | K / D / A |
| `LARGEST_KILLING_SPREE`, `LARGEST_MULTI_KILL`, `DOUBLE_KILLS`…`PENTA_KILLS` | 連殺 |
| `TOTAL_DAMAGE_DEALT_TO_CHAMPIONS` | 對英雄總傷害（分析輸出的主指標） |
| `PHYSICAL/MAGIC/TRUE_DAMAGE_DEALT_TO_CHAMPIONS` | 對英雄傷害分項 |
| `TOTAL_DAMAGE_DEALT` | 對所有單位總傷害（含小兵野怪），對英雄佔比低＝都在清兵 |
| `TOTAL_DAMAGE_DEALT_TO_TURRETS` / `_TO_BUILDINGS` / `_TO_OBJECTIVES` / `_TO_EPIC_MONSTERS` | 建築／目標傷害 |
| `TOTAL_DAMAGE_TAKEN`, `TOTAL_DAMAGE_TAKEN_FROM_CHAMPIONS`, `TOTAL_DAMAGE_SELF_MITIGATED` | 承傷、被英雄打的傷害、自身減傷 |
| `TOTAL_HEAL`, `TOTAL_HEAL_ON_TEAMMATES`, `TOTAL_DAMAGE_SHIELDED_ON_TEAMMATES` | 治療／護盾 |
| `TIME_CCING_OTHERS`, `TOTAL_TIME_CROWD_CONTROL_DEALT_TO_CHAMPIONS` | 控場秒數 |
| `LARGEST_CRITICAL_STRIKE`, `LARGEST_ATTACK_DAMAGE`, `LARGEST_ABILITY_DAMAGE` | 單次最大傷害 |
| `SPELL1_CAST`…`SPELL4_CAST` | Q/W/E/R 施放次數 |
| `SUMMON_SPELL1_CAST`, `SUMMON_SPELL2_CAST` | 召喚師技能施放次數 |
| `SUMMONER_SPELL_1/2` | 召喚師技能 ID（4 閃現、12 傳送、11 懲戒、14 點燃、7 治療、21 屏障、3 虛弱、6 幽靈疾步） |

## 經濟與發育
| 欄位 | 意義 |
|---|---|
| `MINIONS_KILLED` | 小兵補刀 |
| `NEUTRAL_MINIONS_KILLED`, `..._YOUR_JUNGLE`, `..._ENEMY_JUNGLE` | 野怪 |
| `GOLD_EARNED` / `GOLD_SPENT` | 總金／花費 |
| `LEVEL`, `EXP` | 等級與經驗 |
| `ITEM0`…`ITEM5`, `ITEM6` | 終局六格裝備 + 飾品（ID 對照 `lolreplay/data/items.json`） |
| `ITEMS_PURCHASED`, `CONSUMABLES_PURCHASED` | 購買次數 |
| `Missions_LegendaryItems` | **成型大件數（真的可用）** |

## 視野與溝通
`VISION_SCORE`, `WARD_PLACED`, `WARD_PLACED_DETECTOR`, `WARD_KILLED`, `VISION_WARDS_BOUGHT_IN_GAME`, `SIGHT_WARDS_BOUGHT_IN_GAME`；
各種 `*_PINGS`（`ENEMY_MISSING_PINGS`、`ON_MY_WAY_PINGS`、`ALL_IN_PINGS`…）。

## 時間與狀態
| 欄位 | 意義 |
|---|---|
| `TIME_PLAYED` | 秒 |
| `TOTAL_TIME_SPENT_DEAD` | 死亡總秒數 |
| `LONGEST_TIME_SPENT_LIVING` | 最長存活秒數 |
| `LAST_TAKEDOWN_TIME` | 最後一次擊殺／助攻的秒數 |
| `WAS_AFK`, `WAS_LEAVER`, `TIME_SPENT_DISCONNECTED`, `TIME_OF_FROM_LAST_DISCONNECT` | 掛機／斷線 |
| `GAME_ENDED_IN_SURRENDER`, `GAME_ENDED_IN_EARLY_SURRENDER`, `TEAM_EARLY_SURRENDERED` | 投降 |

## 符文
`KEYSTONE_ID`, `PERK0`…`PERK5`（PERK0 = 基石，PERK1–3 主系，PERK4–5 副系）, `PERK_PRIMARY_STYLE`, `PERK_SUB_STYLE`（8000 精密、8100 主宰、8200 巫術、8300 啟發、8400 堅決）, `STAT_PERK_0..2`（屬性碎片）。
`PERKn_VAR1..3` 是該符文的統計值（例如征服者疊層造成的傷害）。

## 陷阱（不要拿來當數據）
- `Missions_*`、`2026_*`、`Event_*`、`HoL_*`、`WeeklyMission_*`、`ActMission_*`、`DemonsHand_*` 多數是**任務進度計數**。
  例如 `Missions_CreepScoreBy10Minutes = 1`、`Missions_GoldPerMinute = 1` 只代表任務條件達成，不是 10 分鐘 CS 或 GPM。
  可信的例外：`Missions_LegendaryItems`（成型件數）、`Missions_CreepScore`（≈ 補刀）、`Missions_TakedownsBefore15Min`。
- `PLAYER_SCORE_0..11`、`NODE_*`、`VICTORY_POINT_TOTAL` 是其他模式用的，召喚峽谷全是 0。
- `CHAMPION_MISSION_STAT_0..3` 是英雄專屬計數（例如尤娜拉的技能疊層），意義視英雄而定。
- 金錢／傷害的絕對值隨賽季變動（2026 賽季一場 17 分鐘的 ADC 可以有 14000 金），比較時用**對位差距與每分鐘值**。
