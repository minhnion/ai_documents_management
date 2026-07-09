#!/usr/bin/env sh
set -eu

IMAGE="${IMAGE:-python:3.11-slim}"
PROJECT_DIR="$(CDPATH= cd -- "$(dirname -- "$0")/.." && pwd)"
NETWORK="${DOCKER_NETWORK:-}"
DB_HOST_VALUE="${DB_HOST:-db}"
DB_PORT_VALUE="${DB_PORT:-5432}"
ENV_FILE_ARGS=""
if [ -f "$PROJECT_DIR/.env" ]; then
  ENV_FILE_ARGS="--env-file $PROJECT_DIR/.env"
fi

if [ -z "$NETWORK" ]; then
  NETWORK="$(docker inspect guideline-db --format '{{range $name, $_ := .NetworkSettings.Networks}}{{println $name}}{{end}}' 2>/dev/null | head -n 1 || true)"
fi

if [ -z "$NETWORK" ]; then
  NETWORK="$(docker network ls --format '{{.Name}}' | grep '_default$' | grep -E 'ai_documents_management|guideline|document' | head -n 1 || true)"
fi

NETWORK_ARGS=""
if [ -n "$NETWORK" ]; then
  NETWORK_ARGS="--network $NETWORK"
fi

docker run --rm \
  $NETWORK_ARGS \
  -v "$PROJECT_DIR:/app" \
  -w /app \
  $ENV_FILE_ARGS \
  -e DB_HOST="$DB_HOST_VALUE" \
  -e DB_PORT="$DB_PORT_VALUE" \
  "$IMAGE" \
  sh -lc 'pip install --no-cache-dir -r requirements.txt && python scripts/register_accounts.py "$@"' \
  sh "$@"
