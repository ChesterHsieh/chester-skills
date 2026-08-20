# tree_data.py 规格

单一资料来源。三个顶层物件：`META`、`N`（节点）、`Q`（题目）。

## META

```python
META = {
  "title": "ROS 2 Robotics Mastery",       # HUD 主标题
  "subtitle": "從通訊核心 → Isaac Sim → 真實手臂",
  "slug": "ros2",                          # 汇出档名前缀
  "storage_key": "ros2-skilltree-v1",      # localStorage key，换版本要换
  "weekly_hours": 12,                      # 决定 HUD 的「预估週数」
  "xp_per_level": 120,
  "quiz_gate": True,                       # 题库是否参与点亮判定，见下
  "dig_intro": "我正在學 ROS 2，",           # 深挖 prompt 开头
  "dig_level": "術語認得但都還模糊",           # 深挖 prompt 里的程度自述
  "titles": [[0,"見習生"],[5,"節點工匠"],[10,"系統整合者"],[15,"模擬架構師"]],
  "acts": [
    {"id":"A0","name":"序章 · 立足點","en":"Foothold","color":"#89a9d6"},
    ...
  ],
}
```

`titles` 是 `[等级下限, 称号]`，取最后一个满足的。`acts` 的 `color` 会用在节点光晕、章节 chip、图例进度条与题目卡的章节标示——**每章一个 hue，别都用同色系**。

`quiz_gate`：`True` 时节点要「任务全勾 **且** 该节点题目全答、正确率 ≥80%」才点亮；`False` 时只看任务。学习型的树建议开着——否则题库会变成没人点的装饰。

## 节点

```python
{
 "id": "A1-3", "act": "A1", "type": "notable", "track": "main",
 "title": "生命週期節點", "en": "Lifecycle Nodes",
 "x": 1200, "y": 640, "hours": 5, "deps": ["A1-1","A0-3"],
 "desc": "...", "why": "...",
 "tasks": ["...", "...", "..."],
 "dod": "...",
 "res": [{"t":"官方教學","u":"https://...","k":"hands-on","h":2}],
}
```

| 栏位 | 说明 |
|---|---|
| `id` | 稳定 slug。**改 id 会让该节点的进度归零**，宁可改 title 也别改 id |
| `act` | 所属章，要对得上 `META.acts` |
| `type` | `start` / `normal`（小圆）/ `notable`（大圆）/ `keystone`（菱形枢纽） |
| `track` | `main` / `side`。side 是虚线边框的选修支线 |
| `x` `y` | 画布座标，手写。惯例见 SKILL.md Step 3 |
| `hours` | 投入估计。XP = 任务完成比例 × hours × 10 |
| `deps` | 前置 id 阵列。空阵列 = 起点 |
| `desc` | 这是什么。具体，别堆抽象名词 |
| `why` | 为什么值得花这几小时。一句会被记住的话 |
| `tasks` | 3–8 条可勾选动作，每条有可验证产物。**进度的唯一来源** |
| `dod` | 一句可判定的验收条件。不可以是「理解 X」 |
| `res` | 资源。`k` ∈ `read`/`video`/`hands-on`/`interactive`/`paper`，`h` = 时数 |

### type 怎么选

- **`keystone`（◆）** — 通过它之后，**后面的做法会改变**。不是「比较难」也不是「比较大」。一章 1 个，最多 2 个。
- **`notable`（●）** — 该章的主要能力点，缺了会卡住好几个后续节点。
- **`normal`** — 其余。
- **`start`** — 全树起点，通常只有一个。

滥发 keystone 会让菱形失去意义——那是视觉上最重的符号，要留给真正的分水岭。

## 题目

```python
{
 "n": "A1-3",              # 挂在哪个节点
 "ty": "concept",          # concept | debug | tradeoff | numeric | scenario
 "q": "...",               # 题干，可含 <code>
 "o": ["...","...","...","..."],
 "a": 2,                   # 0-indexed 正解
 "ex": "...",              # 为什么对 + 其他三个各错在哪
 "dig": {"s":"...","m":"...","t":"...","p":"..."},
}
```

`dig` 四个栏位由样板组成完整 prompt：`s` 卡住的知识点、`m` 我原本的误解、`t` 正确的是、`p` 这一题专属的追问。详见 `questions.md`。

## 预填摸底结果

Step 1 摸底后，把用户答「懂」的节点在档尾预先勾好：

```python
PRESET = {"A0-1": [0,1,2,3,4], "A1-2": [0,1]}   # node id -> 已完成的 task index
```

产生器不处理这个——请在 README 告诉用户第一次开启时用「汇入」载入一份预填的 progress json，或直接在浏览器 console 塞进 localStorage。**别把摸底结果硬编进 tasks 文字里。**

## 验证

`build.py` 会挡下：重复 id、不存在的前置、依赖循环、空任务、缺 `desc`/`why`/`dod`、`dod` 以「理解／熟悉／了解」开头、题目挂错节点、非四选一、答案索引越界、缺 `ex` 或 `dig`、偷懒选项、解释自打脸、每节点题数不足、答案位置分布失衡、长度泄漏、dig 重复。

警告但不挡：跨章依赖 <3、章内无 keystone、任务数不在 3–8、节点无资源、总题数 <60、前置画在子节点下方。
