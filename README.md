# chester-skills

Claude Code plugin。把「硅谷101式」深度内容的讲法变成一份**可打分的规格**，用来检核和生成简报、workshop 教材、interactive HTML，以及产出逐字稿。

规格的五个判准：一条骨干贯穿全篇、靠悬念而非目录推进、抽象立刻落地、图片只在语言效率不足时出现、每一页都有信息增量。

## 安装

```
/plugin marketplace add ChesterHsieh/chester-skills
/plugin install deck-skills@chester-skills
```

换机器时重跑这两行即可。私有 repo 需要本机 `gh` 已登入。

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

## 本地开发

改 skill 内容时，用软链装到 `~/.claude/` 直接生效，不必走 plugin 安装流程：

```bash
./install.sh              # 软链；--copy 改为复制；--uninstall 移除
```

同时装了 plugin 版和软链版会重复载入，二择一。

## 已知限制

规格针对**叙事型深度内容**设计，目前是单一标准、不分 profile。用于教学型 workshop 材料时，「议程页」「回顾页」「悬念驱动」三处会误判：需要跨天回查的教材，结构页和回顾段是有功能的。命中时报告会列在「已知限制」区块交由人判断，不自动豁免。

规格尚未用真实样本校准过。累积几份误判样本后，再决定要不要分 profile。

## License

MIT
