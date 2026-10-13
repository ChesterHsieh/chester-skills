#!/usr/bin/env bash
# 启动 job104-mcp（stdio）。首次执行建 venv 并依 requirements.lock 安装（含 sha256 校验）；
# lock 变动时自动重建。所有安装输出走 stderr —— stdout 必须保留给 MCP 协议。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$HERE/requirements.lock"
BASE="${JOB104_MCP_HOME:-$HOME/.cache/job104-mcp}"
VENV="$BASE/venv"
STAMP="$VENV/.lock.sha256"

want="$(shasum -a 256 "$LOCK" | cut -d' ' -f1)"
have="$(cat "$STAMP" 2>/dev/null || true)"

if [ ! -x "$VENV/bin/python" ] || [ "$want" != "$have" ]; then
  mkdir -p "$BASE"
  rm -rf -- "$VENV"
  if command -v uv >/dev/null 2>&1; then
    uv venv --python ">=3.11" "$VENV" >&2
    uv pip install --python "$VENV/bin/python" -r "$LOCK" >&2
  else
    # job104-mcp 需要 Python >= 3.11
    python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 11) else 1)' \
      || { echo "job104-mcp 需要 Python >= 3.11（或安装 uv）" >&2; exit 1; }
    python3 -m venv "$VENV" >&2
    "$VENV/bin/python" -m pip install --quiet -r "$LOCK" >&2
  fi
  printf '%s' "$want" > "$STAMP"
fi

# -I：忽略 PYTHON* 环境变量与当前目录，不吃到工作目录里的同名模块
exec "$VENV/bin/python" -I -m job104_mcp
