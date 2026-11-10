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
export AI_NEWS_PIPELINE_CONFIG="$PIPELINE_CONFIG"
export AI_NEWS_PIPELINE_SECRETS="$PIPELINE_SECRETS"

case "${AI_NEWS_CONSOLE_HOST:-127.0.0.1}" in
  127.0.0.1|::1|localhost) ;;
  *)
    echo "AI_NEWS_CONSOLE_HOST must stay on the local machine" >&2
    exit 2
    ;;
esac

cd "$PIPELINE_HOME"
exec "$PIPELINE_HOME/.venv/bin/python" -m src.console.app
