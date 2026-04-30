#!/bin/bash

# Load Environment Variables
if [ -f .env ]; then
  echo "Loading environment variables from .env file..."
  export $(grep -v '^#' .env | xargs)
else
  echo "Warning: .env file not found. Using defaults."
fi

# Function to gracefully stop background processes
cleanup() {
    echo ""
    echo "Stopping Metadata Engine Services..."
    kill $BE_PID
    kill $FE_PID
    echo "All services stopped."
    exit 0
}

# Trap SIGINT and SIGTERM signals
trap cleanup SIGINT SIGTERM

echo "======================================"
echo " Starting Metadata Engine Platform"
echo "======================================"

# Start Backend (Spring Boot)
echo "Starting Spring Boot Orchestrator..."
cd metadata_engine_be
./gradlew bootRun --args="--server.port=${SPRING_PORT:-8080}" &
BE_PID=$!
cd ..

# Start Frontend (Angular)
echo "Starting Angular UI Server..."
source ~/.nvm/nvm.sh
nvm use 18 &> /dev/null
cd metadata_engine_fe
npx -y @angular/cli@17 serve --host 0.0.0.0 --port ${NG_PORT:-4200} --disable-host-check &
FE_PID=$!
cd ..

echo "======================================"
echo " Services are booting up!"
echo " - Backend API: http://localhost:${SPRING_PORT:-8080}"
echo " - Frontend Dashboard: http://localhost:${NG_PORT:-4200}"
echo " Press [CTRL+C] to stop all services."
echo "======================================"

# Keep script running
wait $BE_PID $FE_PID
