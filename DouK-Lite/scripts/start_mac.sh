#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
mkdir -p "$ROOT/logs"
LOGF="$ROOT/logs/启动日志.txt"
echo "[$(date '+%Y-%m-%d %H:%M:%S')] mac launcher start" > "$LOGF"

export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
PORT="${DOUK_LITE_PORT:-8765}"

find_python() {
    local candidates=("$ROOT/.venv/bin/python" python3.12 python3.11 python3.10 python3)
    for py in "${candidates[@]}"; do
        if command -v "$py" >/dev/null 2>&1 || [ -x "$py" ]; then
            if "$py" -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" 2>/dev/null; then
                echo "$py"
                return 0
            fi
        fi
    done
    return 1
}

PY="$(find_python || true)"
if [ -z "$PY" ]; then
    echo "ERROR: 需要 Python 3.10+（请先安装 Python 3.12 或创建 DouK-Lite/.venv）" | tee -a "$LOGF"
    exit 1
fi

port_up() {
    lsof -iTCP:"$PORT" -sTCP:LISTEN -t >/dev/null 2>&1
}

need_start=1
if port_up; then
    if [ -f "$ROOT/logs/server.build" ] && cmp -s "$ROOT/logs/server.build" "$ROOT/web/build.txt"; then
        echo "lite already up" >> "$LOGF"
        need_start=0
    else
        echo "build changed, restart lite" >> "$LOGF"
        bash "$ROOT/scripts/stop_mac.sh" >> "$LOGF" 2>&1 || true
    fi
fi

if [ "$need_start" -eq 1 ]; then
    echo "launching lite server with $PY" >> "$LOGF"
    nohup "$PY" -u "$ROOT/server.py" > "$ROOT/logs/server.log" 2>&1 &
    ok=0
    for _ in $(seq 1 30); do
        if port_up; then
            ok=1
            break
        fi
        sleep 1
    done
    if [ "$ok" -ne 1 ]; then
        echo "ERROR: 界面服务未在 30 秒内监听端口 $PORT，请查看 logs/server.log" | tee -a "$LOGF"
        exit 1
    fi
fi

URL="http://127.0.0.1:$PORT/"
CHROME="/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"
PROF="$HOME/Library/Application Support/DouK-Lite/ChromeApp"

if [ -x "$CHROME" ]; then
    mkdir -p "$PROF"
    nohup "$CHROME" --app="$URL" --user-data-dir="$PROF" --no-first-run --no-default-browser-check >/dev/null 2>&1 &
else
    open "$URL"
fi
