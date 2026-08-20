# chester-skills

Chester 自用的 Claude Code plugin 集合。目前两个 plugin，各自独立安装：

| plugin | 做什么 |
|---|---|
| **deck-skills** | 把「硅谷101式」深度内容的讲法变成**可打分的规格**，用来检核与生成简报／workshop 教材／interactive HTML，并产出逐字稿 |
| **skill-tree** | 把一个领域做成 **Path of Exile 式的互动技能树 ＋ 选择题检核点**，产出单一自足的 HTML |

## 安装

```
/plugin marketplace add ChesterHsieh/chester-skills
/plugin install deck-skills@chester-skills
/plugin install skill-tree@chester-skills
```

换机器时重跑这几行即可。私有 repo 需要本机 `gh` 已登入。只要其中一个就装其中一行。

## 目录

```
plugins/
├── deck-skills/     narrative-spine, deck-audit, deck-script, deck-build + deck-reviewer agent
└── skill-tree/      skill-tree
```

---

# deck-skills

规格的五个判准：一条骨干贯穿全篇、靠悬念而非目录推进、抽象立刻落地、图片只在语言效率不足时出现、每一页都有信息增量。

## 四个 skill

```
narrative-spine   规格底座。五维评分表、四种横轴、钩子写法、图片双向判定、反模式图鉴
      │           本身不执行任务，被下面三个共同引用
      ├── deck-audit    检核（主入口）：.pptx/.potx/.html/.md → 骨架还原 + 评分 + 必修清单
      ├── deck-script   逐字稿：deck 或主题 → 可照念的稿子，含时间估算与画面提示
      └── deck-build    正向生成：素材 → 事实原子 → 骨架 → deck，交付前强制自检

deck-reviewer     subagent。30 页以上的大档案在独立 context 里逐页审，只回报结论
```

平常这样用：

```
帮我检查这份 deck            → deck-audit
把这份 deck 写成逐字稿        → deck-script
把这些访谈笔记做成一份分享     → deck-build
```

## 五个维度

| | 维度 | 判准 |
|---|---|---|
| S1 | 骨干 | 标题链单独抽出来读，是不是一篇连贯摘要 |
| S2 | 悬念 | 每段能否填出「回答什么问题／抛出什么问题」 |
| S3 | 具体性 | 抽象论点后 2 句内是否落到数字／公司／人／场景 |
| S4 | 视觉必要性 | 删掉这张图需要多讲几句话？**反向**：有没有该上图却在用文字硬讲 |
| S5 | 信息增量 | 每页标题是断言句还是名词短语 |

各 5 分，满分 25。21+ 可交付，15–20 需修，≤14 重做骨架。**S1 ≤2 时一律判重做骨架**——骨干错了，局部润色没有意义。

完整检核动作与评分锚点在 [`rubric.md`](skills/deck/narrative-spine/references/rubric.md)。

## 两条硬规定

**检核先只看标题链。** 先看内容会被细节吸引，开始润色文字而漏掉骨干问题——骨干问题的修复价值高一个数量级。所以 `deck-audit` 的 Step 2 明文规定这一步不准读正文。

**S4 必须双向。** 只抓多余的图会导向另一种失败：作者不敢放图，改用三段文字硬描述一个空间关系。两个方向同等扣分，判准是「用文字描述空间关系连续超过 2 句 = 该上图却没上」。

## 解析器

零外部依赖，stdlib 的 `zipfile` + `xml.etree` 解 pptx，`html.parser` 解 HTML。可以脱离 Claude 单独跑：

```bash
python3 skills/deck/deck-audit/scripts/extract.py <档案> --pretty
```

输出统一 IR：`title_chain`、逐页正文／图片／表格／讲者备注／flags。

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

## 本地开发

改 skill 内容时，用软链装到 `~/.claude/` 直接生效，不必走 plugin 安装流程：

```bash
./install.sh              # 软链两个 plugin 的全部 skill；--copy 改为复制；--uninstall 移除
```

同时装了 plugin 版和软链版会重复载入，二择一。

## 已知限制

**deck-skills** 的规格针对**叙事型深度内容**设计，目前是单一标准、不分 profile。用于教学型 workshop 材料时，「议程页」「回顾页」「悬念驱动」三处会误判：需要跨天回查的教材，结构页和回顾段是有功能的。命中时报告会列在「已知限制」区块交由人判断，不自动豁免。

规格尚未用真实样本校准过。累积几份误判样本后，再决定要不要分 profile。

**skill-tree** 的时数与週数是估计值，作用是给回馈节奏而不是精算。
另外技能树会过时——课纲换届、论文换代、硬体换代时值得重跑一次更新分支。

## License

MIT
