#!/usr/bin/env bash
# Health-gated deploy with automatic rollback: /opt/roomly/deploy.sh <image_tag>
#
# Safe to run twice with the same tag. A fresh instance boots through this same script.
set -euo pipefail

TAG="${1:?Usage: deploy.sh <image_tag>}"
ROOT=/opt/roomly
HEALTH_TIMEOUT_SECONDS=60
HEALTH_INTERVAL_SECONDS=2

# shellcheck source=/dev/null
source "$ROOT/env"
export IMAGE_REPO AWS_REGION SITE_ADDRESS ACME_CA

COMPOSE=(docker compose -p roomly -f "$ROOT/compose.prod.yml")
CURRENT_TAG_FILE="$ROOT/current_tag"

log() { echo "[deploy $(date -u +%H:%M:%S)] $*"; }

# Passes only if /health says the database is OK and the running commit is the one we expect.
# A slow rollout of the old container can't pass by accident.
healthy() {
  local expected="$1"
  "${COMPOSE[@]}" exec -T app python -c "
import json, sys, urllib.request
with urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=3) as response:
    body = json.load(response)
sys.exit(0 if body['status'] == 'ok' and body['commit'] == sys.argv[1] else 1)
" "$expected" >/dev/null 2>&1
}

wait_for_health() {
  local expected="$1" deadline=$((SECONDS + HEALTH_TIMEOUT_SECONDS))
  while [ "$SECONDS" -lt "$deadline" ]; do
    if healthy "$expected"; then
      return 0
    fi
    sleep "$HEALTH_INTERVAL_SECONDS"
  done
  return 1
}

PREV="$(cat "$CURRENT_TAG_FILE" 2>/dev/null || true)"
log "Deploying $TAG (previous: ${PREV:-none})"

aws ecr get-login-password --region "$AWS_REGION" |
  docker login --username AWS --password-stdin "${IMAGE_REPO%%/*}" >/dev/null
docker pull -q "$IMAGE_REPO:$TAG"

# If this fails, set -e stops here with the old version still serving.
log "Running migrations"
docker run --rm -e SSM_PREFIX=/roomly/prod -e AWS_REGION="$AWS_REGION" -e APP_ENV=prod \
  "$IMAGE_REPO:$TAG" python -m roomly.migrate

log "Starting containers, then waiting up to ${HEALTH_TIMEOUT_SECONDS}s for /health to report commit $TAG"
export TAG
if "${COMPOSE[@]}" up -d --remove-orphans && wait_for_health "$TAG"; then
  echo "$TAG" >"$CURRENT_TAG_FILE"
  log "Deploy succeeded"
  exit 0
fi

log "Health check failed, rolling back to ${PREV:-nothing}"
# App logs are in CloudWatch. They aren't printed here because this output ends up in the
# public GitHub Actions log, and a database error can name the database host.
if [ -n "$PREV" ]; then
  export TAG="$PREV"
  "${COMPOSE[@]}" up -d --remove-orphans
  if wait_for_health "$PREV"; then
    log "Rollback to $PREV succeeded"
  else
    log "Rollback to $PREV is unhealthy too: needs a human"
  fi
fi
exit 1
