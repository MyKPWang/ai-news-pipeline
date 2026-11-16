#!/bin/sh
# Shared helper: keep macOS out of *idle* sleep while a batch job runs.
#
# WHY THIS EXISTS
#
# The laptop is often left idle on battery, and the battery profile's sleep
# value is very short (`pmset -g custom` shows `sleep 1` — the system sleeps
# after ONE minute of idle time). A suspended process does not run: the job
# then burns hours of wall clock while only a few minutes of actual work
# happen. Measured on 2026-11-15: a 7.5 h "run" that contained ~6 min of real
# API work, because the machine slept 451 of the run's 460 minutes.
#
# `caffeinate -i` prevents *idle* system sleep for as long as it runs, which
# is exactly the case this system needs.
#
# WHAT IT DOES NOT DO
#
# `caffeinate` CANNOT prevent a clamshell (lid-close) sleep. That is a
# hardware-level suspend; only clamshell mode (lid closed + external display
# + AC power) or `sudo pmset disablesleep 1` keeps the machine awake. The
# owner has stated that lid-close behaviour does not need to be handled, so
# this helper targets idle sleep only. Do not describe it as a fix for a
# closed lid.
#
# HOW TO USE IT
#
# Source it from a wrapper and call `keep_awake` BEFORE the wrapper's first
# `exec`:
#
#     . "$SCRIPT_DIR/keep_awake.sh"
#     keep_awake "$0" "$@"
#
# `keep_awake` re-executes the script under `caffeinate -i` exactly once. The
# re-executed script sources this file again, and the sentinel
# PIPELINE_CAFFEINATED=1 makes the second pass skip the re-exec and carry on
# with the real work.
#
# ORDERING IS LOAD-BEARING: `exec` REPLACES the shell process, so a call to
# `keep_awake` placed after an `exec` is never reached and the sleep
# protection silently never runs. Put the call immediately after the env load.
#
# NOTE: this file must stay executable (mode 755) if it is invoked directly.
# It is normally sourced, in which case the exec bit is irrelevant.

# keep_awake <script-path> [args...]
#
# Re-executes the given script under `caffeinate -i` unless it is already
# running under the guard. Never returns in the guarded case (exec replaces
# the process); returns 0 in the already-guarded case.
keep_awake() {
  if [ "${PIPELINE_CAFFEINATED:-0}" = "1" ]; then
    return 0
  fi
  if [ ! -x /usr/bin/caffeinate ]; then
    # No caffeinate on this machine: run the job unprotected rather than
    # failing it. The job still works, it just may be interrupted by sleep.
    return 0
  fi
  PIPELINE_CAFFEINATED=1
  export PIPELINE_CAFFEINATED
  exec /usr/bin/caffeinate -i "$@"
}
