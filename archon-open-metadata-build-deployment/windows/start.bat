@echo off
echo Starting Archon Metadata Platform...

set BASE_DIR=%~dp0\..\..

echo 1. Starting Backend (Java) on port 8080...
cd "%BASE_DIR%\archon-open-metadata_be"
start "Java Backend" cmd /c "gradlew.bat bootRun"

echo 2. Starting Metadata Engine (Python) on port 8000...
cd "%BASE_DIR%\archon-open-metadata-py"
start "Python API" cmd /c "venv\Scripts\activate && set DISCOVERY_API_TOKEN=dev&& set RESULTS_DB_PASSWORD=dev&& set SOURCE_DB_PASSWORD=dev&& python -m uvicorn main:app --port 8000"

echo 3. Starting Frontend (Angular) on port 4200...
cd "%BASE_DIR%\archon-open-metadata-fe"
start "Angular Frontend" cmd /c "npm start"

cd "%BASE_DIR%\archon-open-metadata-build-deployment\windows"

echo All services have been launched in separate windows!
pause
