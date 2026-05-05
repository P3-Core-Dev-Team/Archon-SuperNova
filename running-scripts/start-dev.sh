#!/bin/bash
echo "Starting Archon Open Metadata Development Services..."
echo "Launching separate terminal tabs for each service..."

BASE_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )/../.." && pwd )"

gnome-terminal \
  --tab --title="Java Backend API (8080)" -- bash -c "echo 'Starting Java Backend...'; cd $BASE_DIR/archon-open-metadata_be && ./gradlew bootRun; exec bash" \
  --tab --title="Angular Frontend (4200)" -- bash -c "echo 'Starting Angular UI...'; source ~/.nvm/nvm.sh && nvm use 20 && cd $BASE_DIR/archon-open-metadata-fe && npx -y @angular/cli@17 serve --host 0.0.0.0 --port 4200; exec bash" \
  --tab --title="Python ML API (7000)" -- bash -c "echo 'Starting Python ML API...'; cd $BASE_DIR/archon-open-metadata-py && source venv/bin/activate && python3 -m uvicorn main:app --host 127.0.0.1 --port 7000 --reload; exec bash"

echo "Done! You can close these tabs or run stop-dev.sh to terminate the processes."
