#!/bin/bash

# Archon-Open-Metadata-Py Linux Setup Script
# This script automates the installation of the Python ML/Discovery engine.

set -e

echo "----------------------------------------------------"
echo "🚀 Starting Archon-Open-Metadata-Py Setup"
echo "----------------------------------------------------"

# 1. Check Python version
if ! command -v python3 &> /dev/null; then
    echo "❌ Error: python3 not found. Please install Python 3.11 or higher."
    exit 1
fi

PYTHON_VERSION=$(python3 -c 'import sys; print(".".join(map(str, sys.version_info[:2])))')
echo "✅ Found Python $PYTHON_VERSION"

# 2. Create and activate virtual environment
echo "⚙️  Creating virtual environment (venv)..."
python3 -m venv venv
source venv/bin/activate

# 3. Upgrade pip
echo "📦 Upgrading pip..."
pip install --upgrade pip

# 4. Install project and dependencies
echo "📦 Installing dependencies from pyproject.toml..."
pip install -e .

# 5. Download NLP models
echo "🧠 Downloading spaCy NLP model (en_core_web_sm)..."
python3 -m spacy download en_core_web_sm

# 6. Create environment template
if [ ! -f .env.template ]; then
    echo "📝 Creating .env.template file..."
    cat <<EOT > .env.template
# Archon-Open-Metadata-Py Configuration
export RESULTS_DB_HOST="localhost"
export RESULTS_DB_PORT="5432"
export RESULTS_DB_NAME="discovery_results"
export RESULTS_DB_USER="adsuser"
export RESULTS_DB_PASSWORD="CHANGE_ME"

export SOURCE_DB_PASSWORD="CHANGE_ME"
export DISCOVERY_API_TOKEN="dev-token"
EOT
fi

echo "----------------------------------------------------"
echo "✅ Setup Complete!"
echo "----------------------------------------------------"
echo "To start the engine:"
echo "1. source venv/bin/activate"
echo "2. Edit .env.template with your database secrets"
echo "3. source .env.template"
echo "4. uvicorn main_data_analysis:app --host 0.0.0.0 --port 8000 --reload"
echo "----------------------------------------------------"
