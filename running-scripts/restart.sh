#!/bin/bash

# Load Environment Variables
if [ -f .env ]; then
  export $(grep -v '^#' .env | xargs)
fi

echo "======================================"
echo " Restarting Metadata Engine Platform  "
echo "======================================"

# Stop running services
./stop.sh

echo "Waiting for ports to clear..."
sleep 3

# Start services
./start.sh
