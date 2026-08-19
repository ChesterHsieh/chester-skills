#!/usr/bin/env bash
# 把这套 skill 与 agent 软链到 ~/.claude/，改档即时生效。
#
# 用法:
#   ./install.sh            安装（软链）
#   ./install.sh --copy     改用复制（需要隔离版本时）
#   ./install.sh --uninstall 移除

set -euo pipefail

REPO="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
SKILL_DST="$HOME/.claude/skills"
AGENT_DST="$HOME/.claude/agents"
SKILLS=(narrative-spine deck-audit deck-script deck-build)
AGENTS=(deck-reviewer)

mode="${1:-link}"

uninstall() {
  for s in "${SKILLS[@]}"; do rm -rf "${SKILL_DST:?}/$s"; echo "移除 skill: $s"; done
  for a in "${AGENTS[@]}"; do rm -f "${AGENT_DST:?}/$a.md"; echo "移除 agent: $a"; done
}

if [[ "$mode" == "--uninstall" ]]; then
  uninstall
  exit 0
fi

mkdir -p "$SKILL_DST" "$AGENT_DST"

for s in "${SKILLS[@]}"; do
  src="$REPO/skills/deck/$s"
  [[ -d "$src" ]] || { echo "缺少 $src，中止" >&2; exit 1; }
  rm -rf "${SKILL_DST:?}/$s"
  if [[ "$mode" == "--copy" ]]; then
    cp -R "$src" "$SKILL_DST/$s"
  else
    ln -sfn "$src" "$SKILL_DST/$s"
  fi
  echo "安装 skill: $s"
done

for a in "${AGENTS[@]}"; do
  src="$REPO/agents/$a.md"
  [[ -f "$src" ]] || { echo "缺少 $src，中止" >&2; exit 1; }
  if [[ "$mode" == "--copy" ]]; then
    cp "$src" "$AGENT_DST/$a.md"
  else
    ln -sfn "$src" "$AGENT_DST/$a.md"
  fi
  echo "安装 agent: $a"
done

echo
echo "完成。开新的 Claude Code session 后生效。"
echo "验证: python3 $REPO/skills/deck/deck-audit/scripts/extract.py <你的档案> --pretty"
