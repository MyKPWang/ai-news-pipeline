#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/load_local_env.sh"

# Keep the requested 23:50 schedule while avoiding an exact, repeated second.
#
# Keep the machine out of idle sleep for the whole job.
#
# The laptop is often idle on battery with a very short battery sleep
# profile, so the job would otherwise be suspended mid-run. `caffeinate -i`
# prevents *idle* system sleep while this wrapper runs. It cannot prevent a
# lid-close (clamshell) sleep — that case is out of scope.
#
# This must come before the wrapper's first `exec`: `exec` replaces the shell
# process, so a guard placed after it would never run.
. "$SCRIPT_DIR/keep_awake.sh"
keep_awake "$0" "$@"

sleep "$(/usr/bin/jot -r 1 0 120)"
cd "$COLLECTOR_HOME"
COLLECTOR_CLI="$COLLECTOR_HOME/.venv/bin/public-source-collector"
if [ ! -x "$COLLECTOR_CLI" ]; then
  COLLECTOR_CLI="$COLLECTOR_HOME/.venv/bin/wechat-list-collector"
fi

exec "$COLLECTOR_CLI" \
  --config "$COLLECTOR_CONFIG" sources collect --scheduled
