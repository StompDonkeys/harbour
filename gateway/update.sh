#!/bin/sh
# Update Harbour's gateway to the newest image — the same as Container Manager's
# Stop → Clean → delete image → Build, in one go. Settings in ./config are kept.
#
#   SSH:            sudo sh /volume1/docker/harbour-gateway/update.sh
#   Task Scheduler: user root, script  sh /volume1/docker/harbour-gateway/update.sh
set -e
DIR="${HARBOUR_DIR:-/volume1/docker/harbour-gateway}"
DOCKER="$(command -v docker || echo /usr/local/bin/docker)"
cd "$DIR"
if "$DOCKER" compose version >/dev/null 2>&1; then COMPOSE="$DOCKER compose"; else COMPOSE="$(command -v docker-compose || echo /usr/local/bin/docker-compose)"; fi
echo "Pulling the newest Harbour gateway…"
$COMPOSE pull
echo "Restarting it…"
$COMPOSE up -d --remove-orphans
echo "Removing the old image…"
"$DOCKER" image prune -f >/dev/null
$COMPOSE ps
echo "Done — Harbour is up to date."
