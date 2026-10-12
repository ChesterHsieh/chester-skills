#!/usr/bin/env bash
# 启动 resp-mcp（stdio）。首次执行建 venv 并依 requirements.lock 安装；
# lock 变动时自动重建。所有安装输出走 stderr —— stdout 必须保留给 MCP 协议。
set -euo pipefail

HERE="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
LOCK="$HERE/requirements.lock"
BASE="${RESP_MCP_HOME:-$HOME/.cache/resp-mcp}"
VENV="$BASE/venv"
STAMP="$VENV/.lock.sha256"

want="$(shasum -a 256 "$LOCK" | cut -d' ' -f1)"
have="$(cat "$STAMP" 2>/dev/null || true)"

if [ ! -x "$VENV/bin/python" ] || [ "$want" != "$have" ]; then
  mkdir -p "$BASE"
  rm -rf -- "$VENV"
  if command -v uv >/dev/null 2>&1; then
    uv venv "$VENV" >&2
    uv pip install --python "$VENV/bin/python" -r "$LOCK" >&2
  else
    python3 -m venv "$VENV" >&2
    "$VENV/bin/python" -m pip install --quiet -r "$LOCK" >&2
  fi
  printf '%s' "$want" > "$STAMP"
fi

export RESP_CACHE_DIR="${RESP_CACHE_DIR:-$BASE/cache}"
mkdir -p "$RESP_CACHE_DIR"
# -I：忽略 PYTHON* 环境变量与当前目录，不吃到工作目录里的同名模块
exec "$VENV/bin/python" -I -m respmcp.server
