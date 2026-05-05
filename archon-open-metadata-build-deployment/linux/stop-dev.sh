#!/bin/bash
echo "Stopping Archon Open Metadata Development Services..."

# Kill Frontend (Port 4200)
echo "Stopping Frontend (4200)..."
fuser -k 4200/tcp 2>/dev/null || echo "Frontend already stopped."

# Kill Backend Java (Port 8080)
echo "Stopping Java Backend API (8080)..."
fuser -k 8080/tcp 2>/dev/null || echo "Backend API already stopped."

# Kill Python ML (Port 7000)
echo "Stopping Python ML API (7000)..."
fuser -k 7000/tcp 2>/dev/null || echo "Python ML API already stopped."

echo "All services stopped."
