#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
PORT="${DOUK_LITE_PORT:-8765}"

echo "正在关闭 星图画廊 的界面服务..."

if [ -f "logs/server.pid" ]; then
    LPID="$(cat logs/server.pid 2>/dev/null || true)"
    if [ -n "$LPID" ] && kill -0 "$LPID" 2>/dev/null; then
        kill "$LPID" 2>/dev/null || true
    fi
    rm -f "logs/server.pid"
fi

if command -v lsof >/dev/null 2>&1; then
    PIDS="$(lsof -iTCP:"$PORT" -sTCP:LISTEN -t 2>/dev/null || true)"
    if [ -n "$PIDS" ]; then
        echo "$PIDS" | xargs kill 2>/dev/null || true
    fi
fi

echo "已停止。端口 $PORT 现在应该是空的了。"
