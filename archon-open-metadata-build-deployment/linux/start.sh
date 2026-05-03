#!/bin/bash
echo "Starting Archon Metadata Platform..."

BASE_DIR=$(cd "$(dirname "$0")/../.." && pwd)

echo "1. Starting Backend (Java) on port 8080..."
cd "$BASE_DIR/archon-open-metadata_be"
./gradlew bootRun &
BE_PID=$!

echo "2. Starting Metadata Engine (Python) on port 8000..."
cd "$BASE_DIR/archon-open-metadata-py"
source venv/bin/activate
DISCOVERY_API_TOKEN=dev RESULTS_DB_PASSWORD=dev SOURCE_DB_PASSWORD=dev python3 -m uvicorn main:app --port 8000 &
PY_PID=$!

echo "3. Starting Frontend (Angular) on port 4200..."
cd "$BASE_DIR/archon-open-metadata-fe"
npm start &
FE_PID=$!

cd "$BASE_DIR/archon-open-metadata-build-deployment/linux"

echo "All services are starting! Press Ctrl+C to terminate all processes."

trap "echo 'Stopping all services...'; kill $BE_PID $PY_PID $FE_PID; exit" SIGINT SIGTERM

wait $BE_PID $PY_PID $FE_PID
