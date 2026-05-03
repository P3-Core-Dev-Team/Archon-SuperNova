#!/bin/bash
echo "Stopping Archon Metadata Platform..."

# Stop Backend (port 8080)
BE_PID=$(lsof -t -i:8080)
if [ -n "$BE_PID" ]; then
  kill -9 $BE_PID
  echo "Backend stopped."
else
  echo "Backend not running on port 8080."
fi

# Stop Metadata Engine (port 8000)
PY_PID=$(lsof -t -i:8000)
if [ -n "$PY_PID" ]; then
  kill -9 $PY_PID
  echo "Metadata Engine stopped."
else
  echo "Metadata Engine not running on port 8000."
fi

# Stop Frontend (port 4200)
FE_PID=$(lsof -t -i:4200)
if [ -n "$FE_PID" ]; then
  kill -9 $FE_PID
  echo "Frontend stopped."
else
  echo "Frontend not running on port 4200."
fi

echo "All services stopped."
