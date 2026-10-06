#!/usr/bin/env bash
# Whole-product check in a real browser: fresh database, mock model, nothing real is touched.
#   ./scripts/e2e.sh            text result only
#   SHOTS=1 ./scripts/e2e.sh    also save a screenshot per step into .tmp/e2e/shots
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
NODE_HOME="${NODE_HOME:-$HOME/.local/node/node-v24.21.0-darwin-arm64}"
export PATH="$NODE_HOME/bin:$PATH"
WORK="$ROOT/.tmp/e2e"; API_PORT="${E2E_API_PORT:-8041}"; WEB_PORT="${E2E_WEB_PORT:-5192}"
cd "$ROOT"
for port in "$API_PORT" "$WEB_PORT"; do
  if lsof -nP -iTCP:"$port" -sTCP:LISTEN >/dev/null 2>&1; then echo "端口 $port 被占用，先关掉占用它的程序，或用 E2E_API_PORT / E2E_WEB_PORT 换端口。"; exit 2; fi
done
rm -rf "$WORK"; mkdir -p "$WORK/uploads"
export AGENT_RUN_DB_PATH="$WORK/ops.db" AGENT_CHECKPOINT_DB_PATH="$WORK/checkpoints.sqlite" AGENT_UPLOAD_DIR="$WORK/uploads"
export AGENT_ERROR_LOG="$WORK/api-errors.log" AGENT_LLM_PROVIDER=mock AGENT_ACCESS_CODE=""
mkdir -p "$WORK/inbox"; export AGENT_INBOX_DIR="$WORK/inbox" E2E_INBOX_DIR="$WORK/inbox"
uv run alembic upgrade head >"$WORK/migrate.log" 2>&1 || { echo "建库失败，见 $WORK/migrate.log"; exit 2; }
PYTHONPATH=. .venv/bin/uvicorn services.api.app.main:app --host 127.0.0.1 --port "$API_PORT" >"$WORK/api.log" 2>&1 & API_PID=$!
(cd apps/web && VITE_API_URL="http://127.0.0.1:$API_PORT" npx vite --host 127.0.0.1 --port "$WEB_PORT" --strictPort >"$WORK/web.log" 2>&1) & WEB_PID=$!
stop() {
  pkill -f "vite --host 127.0.0.1 --port $WEB_PORT" 2>/dev/null; pkill -P "$WEB_PID" 2>/dev/null
  kill "$WEB_PID" "$API_PID" 2>/dev/null; wait "$WEB_PID" "$API_PID" 2>/dev/null
}
trap stop EXIT
for _ in $(seq 1 40); do
  curl -s -o /dev/null "http://127.0.0.1:$API_PORT/api/health" && curl -s -o /dev/null "http://127.0.0.1:$WEB_PORT" && break
  perl -e 'select(undef,undef,undef,0.5)'
done
[ -n "${SHOTS:-}" ] && export E2E_SHOT_DIR="$WORK/shots"
E2E_WEB_URL="http://127.0.0.1:$WEB_PORT" node apps/web/e2e/full-flow.mjs; CODE=$?
if [ -s "$WORK/api-errors.log" ]; then echo; echo "后台出过错，记录在 $WORK/api-errors.log："; head -12 "$WORK/api-errors.log"; CODE=1; fi
exit $CODE
