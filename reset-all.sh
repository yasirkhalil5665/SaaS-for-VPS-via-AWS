#!/bin/bash
# Wipes all provisioned test customers locally and restarts nginx-proxy clean.
# Local development use only — do NOT run this on a production server.

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

echo "Stopping and removing all customer containers..."
for dir in customers/*/; do
    slug=$(basename "$dir")
    if [ -f "${dir}docker-compose.yml" ]; then
        echo "  - $slug"
        docker compose -f "${dir}docker-compose.yml" down -v --remove-orphans 2>/dev/null || true
    fi
done

echo "Removing customer directories..."
rm -rf customers/*

echo "Clearing nginx configs..."
rm -f nginx/conf.d/*.conf

echo "Restarting nginx-proxy clean..."
cd nginx
docker compose -f docker-compose-nginx.yml down 2>/dev/null || true
docker compose -f docker-compose-nginx.yml up -d
cd ..

echo ""
echo "Done. All test customers removed, nginx-proxy restarted clean."
echo "Note: restart your FastAPI server too, to clear in-memory status/port state."
