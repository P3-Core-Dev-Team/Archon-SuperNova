#!/bin/bash
echo "Starting Archon Metadata Python..."


BASE_DIR=$(cd "$(dirname "$0")/../.." && pwd)
echo "2. Starting Metadata Engine (Python) on port 8000..."
cd "$BASE_DIR/archon-open-metadata-py"
source venv/bin/activate
DISCOVERY_API_TOKEN=dev RESULTS_DB_PASSWORD=dev SOURCE_DB_PASSWORD=dev python3 -m uvicorn main:app --port 8000 &
PY_PID=$!
