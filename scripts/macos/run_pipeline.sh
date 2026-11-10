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

export PUBLIC_SOURCE_API_BASE_URL="$COLLECTOR_API_URL"
export PUBLIC_SOURCE_API_TOKEN="$PUBLIC_SOURCE_COLLECTOR_PIPELINE_TOKEN"
cd "$PIPELINE_HOME"
if [ "${PIPELINE_NO_PUBLISH_DEFAULT:-true}" = "true" ]; then
  set -- --no-publish "$@"
fi
exec "$PIPELINE_HOME/.venv/bin/python" -m src.main \
  --config "$PIPELINE_CONFIG" --secrets "$PIPELINE_SECRETS" "$@"
