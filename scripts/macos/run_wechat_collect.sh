#!/bin/sh
# WeChat article collection job.
#
# WeChat official-account articles are fetched through the WeRead channel
# (the collector's /cgi-bin/appmsgpublish backend path is permission-blocked for
# unverified accounts) and stored in the collector's `articles` table.
#
# This job enqueues a collection command and runs the worker until the queue is
# drained, so the daily report can read fresh articles from the collector.
#
# NOTE: this file must stay executable (mode 755). launchd refuses to spawn a
# job whose ProgramArguments script is not marked executable and then keeps the
# job in a penalty box, which survives a later chmod.
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/load_local_env.sh"

WORKER="$COLLECTOR_HOME/.venv/bin/public-source-collector"
CONFIG="$COLLECTOR_CONFIG"

cd "$COLLECTOR_HOME"

# Enqueue a daily collection for every enabled official account. The idempotency
# key is scoped to the Shanghai business date, so a retry on the same day reuses
# the existing command instead of piling up duplicate runs.
BUSINESS_DATE=$(TZ=Asia/Shanghai date +%Y-%m-%d)
echo "[wechat-collect] enqueue collect-all for $BUSINESS_DATE" >&2
"$WORKER" --config "$CONFIG" enqueue collect-all \
  --business-date "$BUSINESS_DATE" \
  --idempotency-key "scheduled:daily:$BUSINESS_DATE"

# Drain the queue. `worker --once` always exits 0 -- even when it could not
# claim a job -- and reports the real outcome on stdout as
# {"processed": true|false}. So the drain check must read that JSON, not the
# exit code; keying off the exit code spins for the full MAX_ROUNDS every run.
MAX_ROUNDS=30
round=1
processed_total=0
while [ "$round" -le "$MAX_ROUNDS" ]; do
  worker_output=$("$WORKER" --config "$CONFIG" worker --once 2>/dev/null) || worker_output=""
  # Strip whitespace so the match does not depend on the JSON printer's
  # indentation style.
  worker_flag=$(printf '%s' "$worker_output" | tr -d ' \t\r\n')
  case "$worker_flag" in
    *'"processed":true'*)
      processed_total=$((processed_total + 1))
      ;;
    *)
      # Queue is drained: the worker claimed nothing this round.
      break
      ;;
  esac
  round=$((round + 1))
done

echo "[wechat-collect] worker rounds completed: $((round - 1)), jobs processed: $processed_total" >&2

# Backfill digests for articles that were collected but never enriched.
#
# The collection-time enricher only fills `reader_summary_max_items_per_page`
# digests per page, so a normal collection run leaves most collected articles
# without a digest. The pipeline's quality filter drops those into the review
# bucket as `missing_summary_and_content`, which is why an otherwise healthy
# collection can produce a daily report with no WeChat articles in it.
#
# This pass only touches articles inside the Shanghai daily-report window, and
# it is bounded by --limit, so it stays within the request budget.
echo "[wechat-collect] backfilling digests for the report window" >&2
if ! "$WORKER" --config "$CONFIG" backfill-summaries --limit 60; then
  # A failed digest backfill must not fail the collection job: the articles are
  # already stored, and the pipeline can still publish the public-source half.
  echo "[wechat-collect] digest backfill failed; continuing with stored articles" >&2
fi
