#!/usr/bin/env bash
# Start the full Archon-SuperNova stack: mock extraction service, FastAPI
# backend, Angular dev server. Idempotent: kills any existing instance
# before starting.
#
# Logs: /tmp/{mock,api,ng}.log    PIDs: /tmp/archon-stack.pids
#
# After start: open http://localhost:4200
set -u

# Repo root is the parent of this script's directory.
SCRIPTS_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" && pwd )"
BASE="$( cd "$SCRIPTS_DIR/.." && pwd )"
PIDFILE="/tmp/archon-stack.pids"

# Required env vars (override by exporting before invocation).
: "${SOURCE_DB_PASSWORD:=Ads@3421}"
: "${RESULTS_DB_PASSWORD:=Ads@3421}"
: "${DISCOVERY_API_TOKEN:=dev-secret}"
: "${EXTRACTION_SERVICE_TOKEN:=dev-token}"
: "${MOCK_PORT:=8080}"
: "${API_PORT:=8000}"
: "${UI_PORT:=4200}"

# Default parquet storage rotates per schema; the mock service points at
# whatever the calling pipeline expects. We start it pointed at /tmp/archon-parquet
# (a generic dir) — submitting a fresh job will trigger a per-schema directory.
: "${MOCK_STORAGE_PATH:=/tmp/archon-parquet}"
mkdir -p "$MOCK_STORAGE_PATH"

# --- Stop any running stack first (idempotency) ----------------------------
if [[ -f "$PIDFILE" ]]; then
  echo "[start] cleaning up old stack..."
  bash "$SCRIPTS_DIR/stop.sh" >/dev/null 2>&1 || true
fi

# --- 1. Mock extraction service (Python stdlib server, port 8080) ---------
echo "[start] mock_extraction_service on :${MOCK_PORT}"
setsid bash -c "exec env \
  SOURCE_DB_PASSWORD='$SOURCE_DB_PASSWORD' \
  EXTRACTION_SERVICE_TOKEN='$EXTRACTION_SERVICE_TOKEN' \
  STORAGE_PATH='$MOCK_STORAGE_PATH' \
  MOCK_EXTRACTION_PORT=$MOCK_PORT \
  python3 '$BASE/backend/python/mock_extraction_service.py'" \
  >/tmp/mock.log 2>&1 < /dev/null &
disown
MOCK_PID=$!

# --- 2. FastAPI backend (uvicorn, port 8000) -------------------------------
echo "[start] uvicorn (FastAPI) on :${API_PORT}"
setsid bash -c "exec env \
  SOURCE_DB_PASSWORD='$SOURCE_DB_PASSWORD' \
  RESULTS_DB_PASSWORD='$RESULTS_DB_PASSWORD' \
  DISCOVERY_API_TOKEN='$DISCOVERY_API_TOKEN' \
  ARCHON_PIPELINE_SRC='$BASE/backend/python/pipeline/src' \
  python3 -m uvicorn main:app \
    --app-dir '$BASE/backend/python/api' \
    --host 127.0.0.1 \
    --port $API_PORT \
    --log-level info" \
  >/tmp/api.log 2>&1 < /dev/null &
disown
API_PID=$!

# --- 3. Angular dev server (npm start, port 4200) -------------------------
echo "[start] ng serve on :${UI_PORT}"
setsid bash -c "
  source \"\${HOME}/.nvm/nvm.sh\" >/dev/null
  nvm use 20 >/dev/null 2>&1
  cd '$BASE/frontend/ui'
  exec npm start
" >/tmp/ng.log 2>&1 < /dev/null &
disown
UI_PID=$!

# --- Persist PIDs ----------------------------------------------------------
echo "MOCK_PID=$MOCK_PID"   >  "$PIDFILE"
echo "API_PID=$API_PID"     >> "$PIDFILE"
echo "UI_PID=$UI_PID"       >> "$PIDFILE"

# --- Wait for readiness ----------------------------------------------------
echo "[start] waiting for services to come up..."

wait_for_port () {
  local label="$1" port="$2" max=${3:-60}
  local i=0
  # `ng serve` binds to ::1 only by default; localhost resolves to either
  # 127.0.0.1 or ::1 depending on /etc/hosts so we use that for the check.
  until nc -z localhost "$port" 2>/dev/null || nc -z 127.0.0.1 "$port" 2>/dev/null; do
    sleep 1; ((i++))
    if (( i >= max )); then
      echo "[start] FAIL: $label on :$port did not come up in ${max}s"
      return 1
    fi
  done
  echo "[start]   $label on :$port — ready (${i}s)"
}

wait_for_port "mock_extraction" "$MOCK_PORT" 30 || exit 1
wait_for_port "uvicorn"          "$API_PORT"  30 || exit 1
wait_for_port "ng serve"         "$UI_PORT"   180 || exit 1

# Also verify HTTP responses (port-listening != serving).
curl -sf http://127.0.0.1:$API_PORT/api/health >/dev/null && echo "[start]   /api/health OK"
curl -sf http://localhost:$UI_PORT/ | grep -q '<app-root>' && echo "[start]   ng index.html OK"

echo
echo "✓ Archon-SuperNova stack is up:"
echo "   Mock extraction : http://127.0.0.1:$MOCK_PORT  (log: /tmp/mock.log)"
echo "   FastAPI         : http://127.0.0.1:$API_PORT  (log: /tmp/api.log)"
echo "   Angular UI      : http://localhost:$UI_PORT   (log: /tmp/ng.log)"
echo
echo "   Submit a job:    http://localhost:$UI_PORT/submit"
echo "   Stop the stack:  bash $SCRIPTS_DIR/stop.sh"
