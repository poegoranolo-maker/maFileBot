#!/usr/bin/env bash
# Start or update maFileBot from the repository root:
# bash deploy/start.sh
set -Eeuo pipefail

project_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$project_dir"

if [[ ! -f .env ]]; then
  echo "Missing .env. Copy the existing production .env before starting the service."
  exit 1
fi

if ! command -v docker >/dev/null 2>&1; then
  echo "Docker is not installed. Run the server setup first."
  exit 1
fi

docker compose up -d --build --remove-orphans
docker compose ps

if ! docker compose exec -T app /app/.venv/bin/python -c \
  "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=10)"; then
  echo "The app did not pass its health check. Inspect with: docker compose logs --tail=100 app"
  exit 1
fi

echo "maFileBot is running."
