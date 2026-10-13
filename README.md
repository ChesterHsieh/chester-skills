# chester-skills

Chester 自用的 Claude Code plugin 集合。目前九个 plugin，各自独立安装：

| plugin | 做什么 |
|---|---|
| **skill-tree** | 把一个领域做成 **Path of Exile 式的互动技能树 ＋ 选择题检核点**，产出单一自足的 HTML |
| **leaps-strategy** | 针对特定股票制定 **LEAPS 长天期 call 槓桿策略**，串接 IBKR connector 抓真实选择权链，产出候选合约清单 |
| **grill-me** | 对一个计划、决策或想法**地毯式提问**，逐一走过决策树、每题给建议答案，直到达成共识才罢休 |
| **video-cut** | 从**固定机位的录影**里自动剪掉特写／观众席／转场卡等非主机位镜头，逐帧侦测＋关键帧对齐，支援无损直切 |
| **lol-replay** | 離線解析 **League of Legends .rofl 重播檔**：賽後統計、對位比較、任意時刻座標、擊殺者、購買順序、技能施放、連招分析，回答「這場為什麼輸」「凱特琳為什麼輸出低」「連招打得好不好」 |
| **concept-check** | 丢一个概念进去，讲清楚之后**用至少四轮四选一反问**，验证是真的懂而不是看懂；答错先给指针不给答案 |
| **tw-base-breakout** | 台股型态粗扫：给代码、题材或全部个股期货标的，找出符合「**稳定支撑区 → 均线纠结 → 突破**」且贴近底线（进场区）的股票，附失效价与期货保证金，可用 `--asof` 回测 |
| **finance-paper-search** | 量化／金融 × RL／ML **论文检索**，附带固定版本的 resp-mcp server；含 1990 年代早期文献的检索流程与种子论文清单 |
| **job104-search** | **104 人力银行职缺检索**，附带固定版本的 job104-mcp server（依赖 sha256 锁定）；用中文地区／职类名称搜职缺、取完整内容，含薪资筛选的坑与使用守则 |

## 安装

```
/plugin marketplace add ChesterHsieh/chester-skills
/plugin install skill-tree@chester-skills
/plugin install leaps-strategy@chester-skills
/plugin install grill-me@chester-skills
/plugin install video-cut@chester-skills
/plugin install concept-check@chester-skills
/plugin install lol-replay@chester-skills
/plugin install tw-base-breakout@chester-skills
/plugin install finance-paper-search@chester-skills
/plugin install job104-search@chester-skills
```

换机器时重跑这几行即可。私有 repo 需要本机 `gh` 已登入。只要其中一个就装其中一行。

## 目录

```
plugins/
├── skill-tree/      skill-tree
├── leaps-strategy/  leaps-strategy
├── grill-me/        grill-me, grilling
├── video-cut/       camera-cut
├── concept-check/   concept-check
├── lol-replay/      lol-replay（開發 repo：ChesterHsieh/lol-reviewer）
├── tw-base-breakout/ tw-base-breakout
├── finance-paper-search/ finance-paper-search + resp-mcp 启动脚本与 requirements.lock
└── job104-search/   job104-search + job104-mcp 启动脚本与 requirements.lock（带 hash）
```

---

# skill-tree

把课程大纲、职业转型路径或技术栈，做成一棵可点击的技能树：节点有前置依赖、任务清单与**可判定的验收条件**，
配 60–150 题四选一，每题附一段可复制的深挖 prompt，贴回 Claude 就能把答错的知识点挖到懂。

产出是**单一自足的 HTML**，可直接发布成 Artifact 或丢上 GitHub Pages。

```
定位 → 摸底 → 调研 → 排依赖 → 写任务 → 出题 → build → 闸门
```

两个设计重点：

- **摸底不能跳过。** 不问「你会不会 X」，而是给一串具体陈述让人判对错，并**刻意埋错的**——
  能不能抓到它，比十条自评有用。跳过摸底就只会生出一棵网上都有的通用路线图。
- **闸门是机械的。** `build.py` 会量四个数：答案位置分布、正解长度泄漏、解释自打脸、深挖 prompt 重复。
  定性原则挡不住这些反模式，未经检查的题库通常 90% 以上的正解都是最长选项——那等于选最长的就能拿高分。
  验证不过 exit 1，别绕过去手改产物。

规格：`plugins/skill-tree/skills/skill-tree/references/`（`data-model.md` 节点栏位、`questions.md` 出题、`ui.md` 样板契约）。

---

# leaps-strategy

给一个**股票代码 ＋ 一笔预算**，产出 LEAPS（长天期 call）槓桿策略与**可下单的候选合约清单**，含每档买几口。
预算是这次要花在选择权上的权利金总额，全额用于买选择权——skill 不反推帐户比例。

论点是：长期看好某标的，但资金规模有限，所以用 deep ITM 长天期 call 当**融资持股的替代品**——不是赌方向的彩券。

```
代码+预算 → 抓标的现况(IV 百分位) → 挑 12–24 月到期 → 抓链与逐档报价 → screener → 候选清单+口数
```

门槛全部来自使用者自己的交易检讨报告（`references/playbook.md`），不是教科书通则：

- **delta 框架**：0.70–0.85 替代持股（核心仓）／0.40–0.60 高信念加速／<0.25 彩券限额。
  赚钱的 call 进场时近 ATM 偏 ITM，赔钱的是深度 OTM——这是 +$22k 与 −$14k 的分水岭。
- **三关检查**：标的关（催化剂 + 到期日在其后至少 6 个月）、价格关（IV 百分位 + delta + 到期窗）、资金关（单笔权利金 ≤ 帐户 5%）。
- **成本关**：显性佣金约 $220/年，但成本大头是 LEAPS 的买卖价差，常是佣金的十倍以上。

IBKR connector **不回传 greeks**，所以 `screener.py` 用 Black-Scholes 自己补算 delta 与 vega，
再摊开时间价值占比、年化槓桿租金、实质槓桿倍数、来回价差成本与部位上限：

```bash
python3 plugins/leaps-strategy/skills/leaps-strategy/screener.py quotes.json
```

核心产出是取舍：同样 $25k 买 NVDA Jan'28，delta 0.90 那档拿 181 等效股、年化槓桿租金 4.8%；
delta 0.77 那档拿 230 股，代价是年租金 10.0%。**摊开给你看，不替你决定。**

**只做筛选与风险计算，不下单、不给个人化投资建议。**

---

# grill-me

对一个计划、决策或想法进行**地毯式提问**，逐一走过决策树、把彼此有依赖关系的决定拆开来一个一个确认，直到双方对方案有共识才罢休。

包含两个 skill：

- **grilling** — 实际执行提问的 skill：一次只问一题、每题都给出建议答案，等使用者回覆才问下一题；能从环境（档案、工具）查到的**事实**自己去查，不问使用者，只把**决策**丢回来讨论。在使用者确认达成共识前，不会动手实作。
- **grill-me** — 极简触发入口（`disable-model-invocation: true`，不会被模型自动叫用），效果等同直接叫用 `grilling`。

触发时机：使用者想压力测试自己的想法、或讲出任何「grill」相关的字眼时。

---

# video-cut

给一支**有固定主机位**的录影（球场后方的定机、讲台正面的定机），自动剪掉特写、观众席、赞助商转场卡这些杂镜头，只留主机位那一路。

```
缩图墙确认镜头结构 → 逐帧侦测 → 保留段清单给人看过 → 剪 → 逐帧验证成片
```

手法很土但很稳：整片降采样成 64×36，取中位数当「主机位的平均长相」，每帧对它算 L1 距离。
主机位的帧距离小、杂镜头距离大，两群分得很开，阈值自动抓（Otsu 与 MAD 取较小者——单用 Otsu 会被
帧数压倒性的主机位带偏，把纯色转场卡也算进去）。

```bash
python3 plugins/video-cut/skills/camera-cut/scripts/detect.py 影片.mp4 --contact-sheet /tmp/sheets
python3 plugins/video-cut/skills/camera-cut/scripts/detect.py 影片.mp4 --json plan.json --min-seg 3
python3 plugins/video-cut/skills/camera-cut/scripts/cut.py  影片.mp4 --plan plan.json -o out.mp4 --mode hybrid --verify
```

三种剪法的取舍是这个 plugin 的重点。`-c copy` 无法从 P 帧开始解，所以每段起点只能落在关键帧上：

| 模式 | 重编码 | 代价 |
|---|---|---|
| `lossless` | 0% | 对不齐关键帧时**整个段首要往后让**。实测一支 320s 球赛片少掉 13 秒好画面 |
| `hybrid` | 实测 4.9% | 只把段首到下一个关键帧那一小截重压，其余照抄。**预设** |
| `reencode` | 100% | 切点与时间戳都最干净，成片要进后制流程时用 |

`--verify` 会在成片上重跑侦测，逐帧回报有没有漏掉的杂镜头——**这是这个 skill 唯一的验收标准**，
不是「看起来差不多」。踩过的 ffmpeg 坑（`-to` 是封包层级会多含一帧、concat 的重复时间戳、
zsh 的 glob 中止、ffmpeg 吃 stdin）写在 `references/gotchas.md`。

---

# concept-check

丢一个概念、术语、体系，或两个东西的关系进来，交付物**不是一篇解释**，是一段有输赢的对话。

```
/concept-check 動態平衡是什麼
/concept-check 地端翻譯容器的辭典覆蓋準則
```

```
定深度 → 解释（300–500 字）→ 检核循环（≥4 轮，一次一题）→ 结算
```

设计上只有一件事要防：**写一篇很好的解释，然后草草出两题、还自己把答案接在后面。** 那就退化成一次普通问答了。
教学发生在检核循环里，解释那一段只是给弹药——所以解释刻意压短，而且规定出完一题就停住等回答。

三个重点：

- **四轮是一道难度阶梯，不是四题同级的题。** 轮 1 辨识（跟最近的邻居概念分开）、轮 2 边界（什么情况下它不成立）、
  轮 3 迁移（换一个解释里没出现过的场景还认不认得出来）、轮 4 取舍或量级（知道代价）。
  **第 3、4 轮才拉得开差距**——四轮都考定义，跟单字卡没两样。
- **答错先给指针，不公布答案。** 指针要针对他选的**那一个**选项：先说那个选项在什么情况下确实成立，
  再指出这题里哪个条件让它不成立，然后「再想一次？」。同一题第二次还错才公布答案，并补一题变形题。
  说「不知道」也走指针那条路——硬猜跟真的懂是两件事，指针能把它们分开。
- **题目出成 UI 选单**（`AskUserQuestion`），四选一正好对上它每题 2–4 个 option 的上限。
  一次 call 只放**一个** question——工具允许一次问四题，但那等于一次把四题丢出去，难度自适应与指针机制同时失效。
  选项主干写在 `label`（有些客户端只显示 label，不能靠 description 补完），机制说明写在 `description`。
  没有这个工具的环境退回 A/B/C/D 文字格式。
- **不准安慰。** 「很接近了！」会让人误以为方向是对的。结算也不准写「你完全掌握了」，没验到的要明写没验到。

解释本身按问法分四种骨架（单一术语／两者关系／一整套体系／「为什么会 X」），
体系型先给地图再指出承重墙，题目考**部件之间的依赖**而不是「有哪几个部件」。
规格：`plugins/concept-check/skills/concept-check/references/`（`explain-shapes.md` 解释骨架、`question-design.md` 出题与指针）。

---

## 本地开发

改 skill 内容时，用软链装到 `~/.claude/` 直接生效，不必走 plugin 安装流程：

```bash
./install.sh              # 软链全部 plugin 的 skill；--copy 改为复制；--uninstall 移除
```

同时装了 plugin 版和软链版会重复载入，二择一。

## 已知限制

**skill-tree** 的时数与週数是估计值，作用是给回馈节奏而不是精算。
另外技能树会过时——课纲换届、论文换代、硬体换代时值得重跑一次更新分支。

**leaps-strategy** 的 delta 是用 Black-Scholes 从 IV 反推的近似值，不是券商回报的官方 greeks，
无股息／连续复利假设下与实际会有小差距；判断分类够用，精算保证金不够用。
另外 IBKR 的 `option_midpoint_iv` 在长天期合约上会回无效值，脚本改用 `implied_vol`。

**concept-check** 的四轮检核挡得住「看懂了以为自己懂」，挡不住**解释本身就讲错**——
skill 规定涉及版本、数字、规格时要去查，但没有机械闸门强制它查。讲错的概念配上四轮题，只会把错误观念钉得更牢。
另外它是纯对话式的，没有跨 session 记忆：同一个概念隔週再问，会从头再走一次四轮。

**video-cut** 只对**机位固定**的片子有效。手持跟拍、频繁推轨变焦的素材每一帧长相都不同，这套方法会整片误判——skill 会先要求用缩图墙确认前提，不成立时直接讲，不硬做。
另外 `hybrid`／`lossless` 走 concat 出来的档案，接点上会有重复时间戳（`non monotonically increasing dts`）。播放器都正常播，但拿去重新编码会看到警告，试过 `+genpts` 与 `setts` 都修不掉，要根除只能走 `reencode`。

**finance-paper-search** 首次启动 MCP server 时会在 `~/.cache/resp-mcp/` 建 venv 并安装，需要网络与 `uv`（没有 `uv` 退回 `python3 -m venv`）。
版本钉在 [monk1337/resp_mcp](https://github.com/monk1337/resp_mcp) 的 commit `6e5727c`，且 **`mcp` 必须 `<2`**——v2 把 `FastMCP` 改名，server 会直接起不来。
Semantic Scholar 不设 `SEMANTIC_SCHOLAR_API_KEY` 几乎必 429；没有 SSRN 检索。1990 年代命中只实测了 OpenAlex，未量化召回率。
另外论文摘要是第三方内容，与交易／写入类 connector 同 session 使用有间接 prompt injection 风险。

**job104-search** 首次启动 MCP server 时会在 `~/.cache/job104-mcp/` 建 venv 并安装（`requirements.lock` 每个包都带 sha256），需要网络与 `uv`（或 Python ≥ 3.11）。
上游 [mozzan/job104-mcp](https://github.com/mozzan/job104-mcp) 钉在 v0.1.8（commit `2c1215e`，PyPI wheel 已与该 commit 逐档比对一致），**`mcp` 必须 `<2`**。
它靠 `curl_cffi` 模拟 Chrome TLS 指纹绕过 104 的 Cloudflare 防护、抓网页端非公开 JSON：**未经 104 官方授权**，104 改版或加强防护就会 403，只适合个人低频使用。
`salary_min` 只是改成薪资排序、不是硬筛选；职缺内容是第三方文字，与交易／写入类 connector 同 session 使用有间接 prompt injection 风险。
升级版本时用上游 `uv export --frozen --no-dev --no-emit-project --no-annotate` 重新匯出 lock，并重跑 diff 比对 wheel 与原始码。

## License

MIT
