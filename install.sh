#!/usr/bin/env bash
# 把这个 repo 里的 skill 与 agent 软链到 ~/.claude/，改档即时生效。
#
# 用法:
#   ./install.sh            安装（软链）
#   ./install.sh --copy     改用复制（需要隔离版本时）
#   ./install.sh --uninstall 移除
#
# 平常建议走 plugin 安装（见 README），这个脚本是本地开发用的快捷方式。

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DST="$HOME/.claude/skills"
AGENT_DST="$HOME/.claude/agents"

# 每一项是「skill 名称:相对 REPO 的来源目录」
SKILLS=(
  "narrative-spine:plugins/deck-skills/skills/deck/narrative-spine"
  "deck-audit:plugins/deck-skills/skills/deck/deck-audit"
  "deck-script:plugins/deck-skills/skills/deck/deck-script"
  "deck-build:plugins/deck-skills/skills/deck/deck-build"
  "skill-tree:plugins/skill-tree/skills/skill-tree"
)
AGENTS=("deck-reviewer:plugins/deck-skills/agents/deck-reviewer.md")

mode="${1:-link}"

uninstall() {
  for entry in "${SKILLS[@]}"; do
    name="${entry%%:*}"
    rm -rf "${SKILL_DST:?}/$name"; echo "移除 skill: $name"
  done
  for entry in "${AGENTS[@]}"; do
    name="${entry%%:*}"
    rm -f "${AGENT_DST:?}/$name.md"; echo "移除 agent: $name"
  done
}

if [[ "$mode" == "--uninstall" ]]; then
  uninstall
  exit 0
fi

mkdir -p "$SKILL_DST" "$AGENT_DST"

for entry in "${SKILLS[@]}"; do
  name="${entry%%:*}"; src="$REPO/${entry#*:}"
  [[ -d "$src" ]] || { echo "缺少 $src，中止" >&2; exit 1; }
  rm -rf "${SKILL_DST:?}/$name"
  if [[ "$mode" == "--copy" ]]; then
    cp -R "$src" "$SKILL_DST/$name"
  else
    ln -sfn "$src" "$SKILL_DST/$name"
  fi
  echo "安装 skill: $name"
done

for entry in "${AGENTS[@]}"; do
  name="${entry%%:*}"; src="$REPO/${entry#*:}"
  [[ -f "$src" ]] || { echo "缺少 $src，中止" >&2; exit 1; }
  if [[ "$mode" == "--copy" ]]; then
    cp "$src" "$AGENT_DST/$name.md"
  else
    ln -sfn "$src" "$AGENT_DST/$name.md"
  fi
  echo "安装 agent: $name"
done

echo
echo "完成。开新的 Claude Code session 后生效。"
echo "验证 deck:       python3 $REPO/plugins/deck-skills/skills/deck/deck-audit/scripts/extract.py <你的档案> --pretty"
echo "验证 skill-tree: cd <你的题目资料夹> && python3 $REPO/plugins/skill-tree/skills/skill-tree/assets/build.py"
