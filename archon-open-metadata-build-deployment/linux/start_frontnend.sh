#!/bin/bash
echo "Starting Archon Metadata Front end..."

BASE_DIR=$(cd "$(dirname "$0")/../.." && pwd)
echo "3. Starting Frontend (Angular) on port 4200..."
cd "$BASE_DIR/archon-open-metadata-fe"
npm start &
FE_PID=$!
