#!/bin/bash
echo "Starting Archon Metadata Platform..."

BASE_DIR=$(cd "$(dirname "$0")/../.." && pwd)

echo "1. Starting Backend (Java) on port 8080..."
cd "$BASE_DIR/archon-open-metadata_be"
./gradlew bootRun &
BE_PID=$!
