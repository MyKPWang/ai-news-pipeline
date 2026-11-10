#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/load_local_env.sh"

if [ -z "${PUBLIC_SOURCE_COLLECTOR_PIPELINE_TOKEN:-}" ] && [ -n "${WECHAT_COLLECTOR_PIPELINE_TOKEN:-}" ]; then
  export PUBLIC_SOURCE_COLLECTOR_PIPELINE_TOKEN="$WECHAT_COLLECTOR_PIPELINE_TOKEN"
fi
if [ -z "${PUBLIC_SOURCE_COLLECTOR_PIPELINE_TOKEN:-}" ]; then
  echo "PUBLIC_SOURCE_COLLECTOR_PIPELINE_TOKEN is missing in local-machine.env" >&2
  exit 2
fi

cd "$COLLECTOR_HOME"
COLLECTOR_CLI="$COLLECTOR_HOME/.venv/bin/public-source-collector"
if [ ! -x "$COLLECTOR_CLI" ]; then
  COLLECTOR_CLI="$COLLECTOR_HOME/.venv/bin/wechat-list-collector"
fi

exec "$COLLECTOR_CLI" \
  --config "$COLLECTOR_CONFIG" api --host "$COLLECTOR_API_HOST" --port "$COLLECTOR_API_PORT"
