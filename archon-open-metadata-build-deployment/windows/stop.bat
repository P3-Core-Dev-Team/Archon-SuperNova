@echo off
echo Stopping Archon Metadata Platform...

echo Stopping Backend (port 8080)...
FOR /F "tokens=5" %%a IN ('netstat -aon ^| findstr :8080 ^| findstr LISTENING') DO taskkill /F /PID %%a

echo Stopping Metadata Engine (port 8000)...
FOR /F "tokens=5" %%a IN ('netstat -aon ^| findstr :8000 ^| findstr LISTENING') DO taskkill /F /PID %%a

echo Stopping Frontend (port 4200)...
FOR /F "tokens=5" %%a IN ('netstat -aon ^| findstr :4200 ^| findstr LISTENING') DO taskkill /F /PID %%a

echo All services stopped!
pause
