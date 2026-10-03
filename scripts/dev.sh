#!/usr/bin/env bash
# Start the API and the web UI together. Ctrl+C stops both.
#
#   ./scripts/dev.sh                        # API on 8000, web on 5173
#   API_PORT=8001 WEB_PORT=5174 ./scripts/dev.sh
#   LAN=1 ./scripts/dev.sh                  # also reachable from other devices on this network
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
API_PORT="${API_PORT:-8000}"
WEB_PORT="${WEB_PORT:-5173}"
# Pinned Node runtime, see docs/node-runtime.md
NODE_HOME="${NODE_HOME:-$HOME/.local/node/node-v24.21.0-darwin-arm64}"
WEB_DIR="${WEB_DIR:-$ROOT/apps/web}"
HOST="127.0.0.1"
LAN_IP=""
if [ "${LAN:-0}" = "1" ]; then
  HOST="0.0.0.0"
  LAN_IP="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
  if [ -z "$LAN_IP" ]; then
    echo "没有找到局域网地址，请确认电脑已连接 Wi-Fi 或网线。" >&2
    exit 1
  fi
  # Other devices must enter this code; keep the same one by setting AGENT_ACCESS_CODE.
  export AGENT_ACCESS_CODE="${AGENT_ACCESS_CODE:-$(printf '%06d' $(( (RANDOM * 32768 + RANDOM) % 1000000 )))}"
fi

# lsof exits non-zero when the port is free; that must not stop the script.
port_owner() { { lsof -nP -iTCP:"$1" -sTCP:LISTEN 2>/dev/null || true; } | awk 'NR==2 {print $1" (PID "$2")"}'; }

for port in "$API_PORT" "$WEB_PORT"; do
  owner="$(port_owner "$port")"
  if [ -n "$owner" ]; then
    echo "端口 $port 已被 $owner 占用。请先关掉之前启动的服务，或换端口：API_PORT=8001 WEB_PORT=5174 $0" >&2
    exit 1
  fi
done
if [ ! -x "$NODE_HOME/bin/node" ]; then
  echo "找不到 Node：$NODE_HOME。请按 docs/node-runtime.md 安装，或设置 NODE_HOME。" >&2
  exit 1
fi

# A Vite cache created by an earlier `sudo npm run dev` blocks normal starts.
if [ -d "$WEB_DIR/node_modules/.vite" ] && [ -n "$(find "$WEB_DIR/node_modules/.vite" ! -user "$(id -u)" -print -quit 2>/dev/null)" ]; then
  echo "前端缓存目录属于其他用户（通常是之前用 sudo 启动过前端）。请先执行一次：" >&2
  echo "  sudo rm -rf \"$WEB_DIR/node_modules/.vite\"" >&2
  exit 1
fi

API_PID=""
WEB_PID=""
# Stop only the processes this script started (npm spawns vite as a child).
kill_tree() {
  local pid="$1" child
  [ -z "$pid" ] && return 0
  for child in $(pgrep -P "$pid" 2>/dev/null); do kill_tree "$child"; done
  kill "$pid" 2>/dev/null || true
}
cleanup() {
  trap - INT TERM EXIT
  echo
  echo "正在停止服务…"
  kill_tree "$WEB_PID"
  kill_tree "$API_PID"
}
trap cleanup INT TERM EXIT

cd "$ROOT"
echo "检查数据库结构…"
uv run alembic upgrade head

echo "启动后台 API：http://127.0.0.1:$API_PORT"
PYTHONPATH=labs/phase0-langgraph/src .venv/bin/uvicorn services.api.app.main:app \
  --host "$HOST" --port "$API_PORT" &
API_PID=$!

for _ in $(seq 1 30); do
  curl -sf "http://127.0.0.1:$API_PORT/api/health" >/dev/null && break
  sleep 1
done
if ! curl -sf "http://127.0.0.1:$API_PORT/api/health" >/dev/null; then
  echo "后台 API 30 秒内没有启动成功，请查看上面的报错。" >&2
  exit 1
fi

echo "启动前端：http://127.0.0.1:$WEB_PORT"
(
  cd "$WEB_DIR"
  # Only point the web app elsewhere when the API is not on its default port;
  # changing VITE_API_URL makes Vite rebuild its dependency cache.
  if [ "$API_PORT" != "8000" ]; then export VITE_API_PORT="$API_PORT"; fi
  PATH="$NODE_HOME/bin:$PATH" exec npm run dev -- --host "$HOST" --port "$WEB_PORT" --strictPort
) &
WEB_PID=$!

echo
echo "两个服务都在运行。本机浏览器打开 http://127.0.0.1:$WEB_PORT ，按 Ctrl+C 同时停止。"
if [ -n "$LAN_IP" ]; then
  echo
  echo "同一网络下的其他设备打开： http://$LAN_IP:$WEB_PORT"
  echo "访问码： $AGENT_ACCESS_CODE   （其他设备第一次打开时输入；本机不需要）"
  echo "只在可信的网络下使用（家里或办公室），不要在公共 Wi-Fi 下开启。"
fi
wait
