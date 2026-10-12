#!/usr/bin/env bash
# 把这个 repo 里的 skill 软链到 ~/.claude/，改档即时生效。
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

# 每一项是「skill 名称:相对 REPO 的来源目录」
SKILLS=(
  "skill-tree:plugins/skill-tree/skills/skill-tree"
  "leaps-strategy:plugins/leaps-strategy/skills/leaps-strategy"
  "grill-me:plugins/grill-me/skills/grill-me"
  "grilling:plugins/grill-me/skills/grilling"
  "camera-cut:plugins/video-cut/skills/camera-cut"
  "concept-check:plugins/concept-check/skills/concept-check"
  "lol-replay:plugins/lol-replay/skills/lol-replay"
  "tw-base-breakout:plugins/tw-base-breakout/skills/tw-base-breakout"
)

mode="${1:-link}"

uninstall() {
  for entry in "${SKILLS[@]}"; do
    name="${entry%%:*}"
    rm -rf "${SKILL_DST:?}/$name"; echo "移除 skill: $name"
  done
}

if [[ "$mode" == "--uninstall" ]]; then
  uninstall
  exit 0
fi

mkdir -p "$SKILL_DST"

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

echo
echo "完成。开新的 Claude Code session 后生效。"
echo "验证 skill-tree: cd <你的题目资料夹> && python3 $REPO/plugins/skill-tree/skills/skill-tree/assets/build.py"
echo "验证 leaps:      python3 $REPO/plugins/leaps-strategy/skills/leaps-strategy/screener.py <quotes.json>"
echo "验证 lol-replay:  python3 $REPO/plugins/lol-replay/skills/lol-replay/scripts/rofl_cli.py summary <game.rofl> --no-payload"
echo "验证 camera-cut: python3 $REPO/plugins/video-cut/skills/camera-cut/scripts/detect.py <影片> --json /tmp/plan.json"
