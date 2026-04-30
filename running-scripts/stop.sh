#!/bin/bash

# Load Environment Variables for ports
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

SPRING_PORT=${SPRING_PORT:-8080}
NG_PORT=${NG_PORT:-4200}

echo "Stopping Metadata Engine Services..."

# Check and kill the process bound to the Backend port
BE_PID=$(lsof -t -i:$SPRING_PORT)
if [ -n "$BE_PID" ]; then
    echo "Killing Backend on port $SPRING_PORT (PID: $BE_PID)..."
    kill -9 $BE_PID
else
    echo "Backend is not running on port $SPRING_PORT."
fi

# Check and kill the process bound to the Frontend port
FE_PID=$(lsof -t -i:$NG_PORT)
if [ -n "$FE_PID" ]; then
    echo "Killing Frontend on port $NG_PORT (PID: $FE_PID)..."
    kill -9 $FE_PID
else
    echo "Frontend is not running on port $NG_PORT."
fi

# Fallback: Terminate stray Gradle daemons and Node processes associated with the project dir
pkill -f "metadata_engine_be/.*gradlew" 2>/dev/null
pkill -f "metadata_engine_fe/.*ng serve" 2>/dev/null

echo "✔ Services stopped."
