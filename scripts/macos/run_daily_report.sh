#!/bin/sh
# Daily report job: refresh the public sources, rewrite with MiniMax, and write
# the WeChat draft. The collector refreshes sources itself, so this job does not
# depend on the separate 23:50 collection job.
#
# The jitter keeps the wake-up off an exact, repeated second.
#
# NOTE: this file must stay executable (mode 755). launchd refuses to spawn a
# job whose ProgramArguments script is not marked executable and then keeps the
# job in a penalty box, which survives a later chmod.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/load_local_env.sh"

sleep "$(/usr/bin/jot -r 1 0 120)"

# Refresh WeChat official-account articles first, so the pipeline reads a fresh
# snapshot from the collector's `articles` table. A WeChat failure must not
# block the public-source report, hence the guard.
if [ -x "$SCRIPT_DIR/run_wechat_collect.sh" ]; then
  if ! "$SCRIPT_DIR/run_wechat_collect.sh"; then
    echo "[daily-report] WeChat collection failed; continuing with public sources only" >&2
  fi
fi

# run_pipeline.sh already exports the collector token and honours
# PIPELINE_NO_PUBLISH_DEFAULT, so reuse it rather than duplicating that logic.
exec "$SCRIPT_DIR/run_pipeline.sh"

# Keep the machine awake for the whole job.
#
# The daily job runs for tens of minutes and the laptop is often idle on
# battery, where macOS sleeps after a very short idle time. A suspended
# process does not run: the job then burns hours of wall clock while only a
# few minutes of work happens. `caffeinate -i` keeps the system out of idle
# sleep for as long as this wrapper runs.
#
# The script re-executes itself under caffeinate, so the guard variable stops
# the second pass from re-execing again.
if [ "${PIPELINE_CAFFEINATED:-0}" != "1" ] && [ -x /usr/bin/caffeinate ]; then
  PIPELINE_CAFFEINATED=1
  export PIPELINE_CAFFEINATED
  exec /usr/bin/caffeinate -i "$SCRIPT_DIR/$(basename -- "$0")" "$@"
fi
