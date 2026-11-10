#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
DEFAULT_PIPELINE_HOME=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
ENV_FILE=${LOCAL_MACHINE_ENV:-"$DEFAULT_PIPELINE_HOME/deploy/macos/local-machine.env"}

if [ ! -f "$ENV_FILE" ]; then
  echo "Missing local machine environment file: $ENV_FILE" >&2
  exit 2
fi

# The file is local-only and contains simple KEY=value assignments.
set -a
. "$ENV_FILE"
set +a

: "${PIPELINE_HOME:=$DEFAULT_PIPELINE_HOME}"
: "${COLLECTOR_HOME:?COLLECTOR_HOME is required}"
: "${COLLECTOR_CONFIG:?COLLECTOR_CONFIG is required}"
: "${COLLECTOR_API_HOST:=127.0.0.1}"
: "${COLLECTOR_API_PORT:=4100}"
: "${COLLECTOR_API_URL:=http://${COLLECTOR_API_HOST}:${COLLECTOR_API_PORT}}"
