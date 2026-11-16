#!/bin/sh
# Permanent-link recovery for articles the WeRead index never listed.
#
# The WeRead index caps how many articles of one Official Account it shows per
# day (measured: APPSO returns 3), so a push can be missing from the corpus
# entirely. Recovery works from permanent links instead: it derives the
# official short link from the articles already in the production DB, fetches
# each page, and follows the "相关阅读" permalinks to the sibling articles that
# the index dropped.
#
# Timing: each HTTP request is paced at a 4 s minimum interval plus 0-2 s
# jitter, and a full sweep over 14 accounts takes tens of minutes. So this runs
# in the 23:50 collector slot -- the night before the 08:00 report -- instead
# of inside the report path, where it would delay the draft.
#
# The write is idempotent (the uq_articles_account_source unique constraint
# plus the repository's content-hash merge), so a second run on the same day
# adds no rows.
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

COLLECTOR_PY="$COLLECTOR_HOME/.venv/bin/python"
RECOVERY_SCRIPT="$COLLECTOR_HOME/scripts/recover_missing_articles.py"

if [ ! -x "$COLLECTOR_PY" ]; then
  echo "[recovery] collector venv missing: $COLLECTOR_PY" >&2
  exit 2
fi
if [ ! -f "$RECOVERY_SCRIPT" ]; then
  echo "[recovery] recovery script missing: $RECOVERY_SCRIPT" >&2
  exit 2
fi

cd "$COLLECTOR_HOME"

# Seed and article caps keep the sweep inside a predictable time budget.
# --apply is what writes to the production DB; without it the script only
# rehearses. The DB write itself is idempotent, so a failed or duplicated run
# cannot produce duplicate articles.
echo "[recovery] recovering articles missed by the WeRead index" >&2
if ! "$COLLECTOR_PY" "$RECOVERY_SCRIPT" \
  --config "$COLLECTOR_CONFIG" \
  --seed-limit 12 \
  --max-articles 25 \
  --max-depth 1 \
  --apply; then
  # A failed recovery must not fail the collector job: the articles already in
  # the DB are still valid and the pipeline can still publish them.
  echo "[recovery] recovery failed; continuing with the stored articles" >&2
fi
