#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/load_local_env.sh"

# Keep the requested 23:50 schedule while avoiding an exact, repeated second.
#
# Keep the machine awake for the whole job.
#
# The daily job runs for tens of minutes and the laptop is often idle on
# battery, where macOS sleeps after a very short idle time. A suspended
# process does not run: the job then burns hours of wall clock while only a
# few minutes of work happens. `caffeinate -i` keeps the system out of idle
# sleep for as long as this wrapper runs.
#
# The script re-executes itself under caffeinate, so the guard variable stops
# the second pass from re-execing again. This block must come BEFORE any
# `exec`: an `exec` replaces the process, so a guard placed after it is dead
# code that never runs.
if [ "${PIPELINE_CAFFEINATED:-0}" != "1" ] && [ -x /usr/bin/caffeinate ]; then
  PIPELINE_CAFFEINATED=1
  export PIPELINE_CAFFEINATED
  exec /usr/bin/caffeinate -i "$SCRIPT_DIR/$(basename -- "$0")" "$@"
fi

sleep "$(/usr/bin/jot -r 1 0 120)"
cd "$COLLECTOR_HOME"
COLLECTOR_CLI="$COLLECTOR_HOME/.venv/bin/public-source-collector"
if [ ! -x "$COLLECTOR_CLI" ]; then
  COLLECTOR_CLI="$COLLECTOR_HOME/.venv/bin/wechat-list-collector"
fi

exec "$COLLECTOR_CLI" \
  --config "$COLLECTOR_CONFIG" sources collect --scheduled
