#!/usr/bin/env bash
# Stop the Archon-SuperNova stack: mock + uvicorn + ng serve.
# Reads PIDs from /tmp/archon-stack.pids written by start.sh; falls back
# to pkill-by-pattern if the file is missing or stale.
set -u
PIDFILE="/tmp/archon-stack.pids"

stop_pid () {
  local label="$1" pid="$2"
  if [[ -z "$pid" ]] || ! kill -0 "$pid" 2>/dev/null; then
    return 0
  fi
  echo "[stop]  $label  pid=$pid"
  kill -TERM "$pid" 2>/dev/null || true
  for _ in 1 2 3 4 5; do
    sleep 1
    if ! kill -0 "$pid" 2>/dev/null; then return 0; fi
  done
  echo "[stop]    forcing SIGKILL on $label pid=$pid"
  kill -KILL "$pid" 2>/dev/null || true
}

if [[ -f "$PIDFILE" ]]; then
  # shellcheck disable=SC1090
  source "$PIDFILE"
  stop_pid "ng serve"        "${UI_PID:-}"
  stop_pid "uvicorn"          "${API_PID:-}"
  stop_pid "mock_extraction"  "${MOCK_PID:-}"
  rm -f "$PIDFILE"
fi

# Defensive sweep — kill any straggler that escaped the PID-file path.
pkill -f "uvicorn main:app"            2>/dev/null || true
pkill -f "mock_extraction_service.py"  2>/dev/null || true
pkill -f "ng serve"                    2>/dev/null || true
pkill -f "node.*@angular/cli"          2>/dev/null || true

# Wait for ports to release.  ng serve binds ::1 not 127.0.0.1, so check
# both transports — port is "free" only if neither responds.
for port in 8000 8080 4200; do
  if nc -z localhost "$port" 2>/dev/null || nc -z 127.0.0.1 "$port" 2>/dev/null; then
    echo "[stop]    port $port still bound; lingering process?"
  else
    echo "[stop]    port $port released"
  fi
done

echo "✓ Archon-SuperNova stack stopped."
