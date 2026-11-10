#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/load_local_env.sh"

# Keep the requested 23:50 schedule while avoiding an exact, repeated second.
sleep "$(/usr/bin/jot -r 1 0 120)"
cd "$COLLECTOR_HOME"
COLLECTOR_CLI="$COLLECTOR_HOME/.venv/bin/public-source-collector"
if [ ! -x "$COLLECTOR_CLI" ]; then
  COLLECTOR_CLI="$COLLECTOR_HOME/.venv/bin/wechat-list-collector"
fi

exec "$COLLECTOR_CLI" \
  --config "$COLLECTOR_CONFIG" sources collect --scheduled
