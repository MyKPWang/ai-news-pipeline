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
