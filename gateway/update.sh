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
# Container Manager may have named the project differently from the folder; use the running
# container's own project name so this updates it instead of clashing with it.
P="$("$DOCKER" inspect -f '{{ index .Config.Labels "com.docker.compose.project" }}' harbour-gateway 2>/dev/null || true)"
COMPOSE="$COMPOSE -p ${P:-harbour-gateway}"
echo "Pulling the newest Harbour gateway…"
$COMPOSE pull
echo "Restarting it…"
$COMPOSE up -d
echo "Removing the old image…"
"$DOCKER" image prune -f >/dev/null
sleep 10
"$DOCKER" ps --filter name=harbour-gateway --format '{{.Names}} {{.Status}}'
V="$(curl -s "http://localhost:8765/?json=1" | grep -o '"version": *"[^"]*"' | cut -d'"' -f4)"
echo "Done — Harbour gateway ${V:-is starting}."
[ -x /usr/syno/bin/synodsmnotify ] && /usr/syno/bin/synodsmnotify @administrators "Harbour" "Gateway update finished, now running ${V:-unknown}" || true
