# Archon Open Metadata Platform - Deployment Guide

Welcome to the deployment guide for the Archon Open Metadata platform. This repository contains a full-stack data discovery solution comprising three integrated services:
1. **Frontend (Angular)** - Port 4200
2. **Backend Orchestrator (Java/Spring Boot)** - Port 8080
3. **Metadata Analysis Engine (Python/FastAPI)** - Port 8000

## Prerequisites
- **Java 17+**
- **Node.js 18+ & npm**
- **Python 3.11+**

## One-Click Execution
To spin up all three services simultaneously for local development, run the startup scripts provided in this folder. We also provide stop scripts to gracefully terminate them.

### Linux / macOS
```bash
cd archon-open-metadata-build-deployment/linux
chmod +x start.sh stop.sh
./start.sh
# To stop:
./stop.sh
```

### Windows
```cmd
cd archon-open-metadata-build-deployment\windows
start.bat
# To stop:
stop.bat
```

## Manual Installation Steps
If you prefer to start the services individually, follow these steps:

### 1. Java Backend
```bash
cd archon-open-metadata_be
./gradlew bootRun
```
*Note: The backend connects to PostgreSQL via `application.yml` and will auto-seed the default IAM users & templates.*

### 2. Python Metadata Engine
```bash
cd archon-open-metadata-py
python -m venv venv
source venv/bin/activate  # On Windows use `venv\Scripts\activate`
pip install -e .
DISCOVERY_API_TOKEN=dev RESULTS_DB_PASSWORD=dev SOURCE_DB_PASSWORD=dev python -m uvicorn main:app --port 8000 --reload
```

### 3. Angular Frontend
```bash
cd archon-open-metadata-fe
npm install
npm start
```
*Access the dashboard at http://localhost:4200*
