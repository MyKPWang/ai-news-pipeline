#!/bin/sh
# Sync the development copy in ~/Documents into the launchd runtime copy in
# ~/Developer. The runtime copy is the only one launchd may execute, because
# macOS TCC blocks background jobs from reading ~/Documents.
#
# Usage: ./scripts/macos/sync_runtime.sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/load_local_env.sh"

DEV_PIPELINE=/Users/wangkp/Documents/tools/ai-news-pipeline
DEV_COLLECTOR=/Users/wangkp/Documents/wechat-paper-collect/public-source-collector

# Sync a directory tree, replacing the destination contents.
sync_dir() {
  src=$1
  dst=$2
  if [ ! -d "$src" ]; then
    echo "SKIP  missing source dir: $src" >&2
    return 0
  fi
  mkdir -p "$dst"
  rsync -a --delete \
    --exclude='.venv' --exclude='.git' --exclude='__pycache__' \
    --exclude='.pytest_cache' --exclude='.mypy_cache' \
    --exclude='logs' --exclude='output' --exclude='data' \
    "$src/" "$dst/"
}

# Sync a single file.
sync_file() {
  src=$1
  dst=$2
  if [ ! -f "$src" ]; then
    echo "SKIP  missing source file: $src" >&2
    return 0
  fi
  mkdir -p "$(dirname "$dst")"
  cp "$src" "$dst"
}

sync_dir "$DEV_PIPELINE/src" "$PIPELINE_HOME/src"
sync_dir "$DEV_PIPELINE/scripts" "$PIPELINE_HOME/scripts"
sync_dir "$DEV_PIPELINE/tests" "$PIPELINE_HOME/tests"
sync_dir "$DEV_PIPELINE/docs" "$PIPELINE_HOME/docs"
sync_file "$DEV_PIPELINE/config.yaml" "$PIPELINE_HOME/config.yaml"
sync_file "$DEV_PIPELINE/config.example.yaml" "$PIPELINE_HOME/config.example.yaml"
sync_file "$DEV_PIPELINE/secrets.yaml" "$PIPELINE_HOME/secrets.yaml"
sync_file "$DEV_PIPELINE/README.md" "$PIPELINE_HOME/README.md"
sync_file "$DEV_PIPELINE/requirements.txt" "$PIPELINE_HOME/requirements.txt"
sync_file "$DEV_PIPELINE/SPEC.md" "$PIPELINE_HOME/SPEC.md"

# launchd job definitions. manage_services.sh copies these into ~/Library/LaunchAgents.
# Only the .plist files are synced: local-machine.env holds the live app
# credentials and is local-only, so it must never be overwritten from here.
mkdir -p "$PIPELINE_HOME/deploy/macos"
for plist in "$DEV_PIPELINE"/deploy/macos/*.plist; do
  [ -f "$plist" ] || continue
  cp "$plist" "$PIPELINE_HOME/deploy/macos/$(basename "$plist")"
done

# The WeChat draft writer lives in wechat-publish-tool/. Its news-config.json
# holds the live app credentials and is rewritten by the publisher at runtime,
# so only the code and the assets are synced.
mkdir -p "$PIPELINE_HOME/wechat-publish-tool"
rsync -a --delete \
  --exclude='news-config.json' --exclude='output' --exclude='__pycache__' \
  --exclude='.git.vendor' --exclude='.DS_Store' \
  "$DEV_PIPELINE/wechat-publish-tool/" "$PIPELINE_HOME/wechat-publish-tool/"

# The launchd runtime copy must keep every wrapper script executable; launchd
# refuses to spawn a job whose script is not marked executable.
for script in "$PIPELINE_HOME"/scripts/macos/*.sh; do
  [ -f "$script" ] || continue
  chmod +x "$script"
done

sync_dir "$DEV_COLLECTOR/src" "$COLLECTOR_HOME/src"
sync_dir "$DEV_COLLECTOR/tests" "$COLLECTOR_HOME/tests"
sync_file "$DEV_COLLECTOR/public-sources.toml" "$COLLECTOR_HOME/public-sources.toml"
sync_file "$DEV_COLLECTOR/collector.toml" "$COLLECTOR_HOME/collector.toml"

# The external-permalink recovery scripts are copied one by one rather than
# with sync_dir: the runtime scripts/ tree holds restore_deduped.py, which is
# not in the development tree, and rsync --delete would remove it.
for script in harvest_external_permalinks.py recover_from_external_seeds.py; do
  sync_file "$DEV_COLLECTOR/scripts/$script" "$COLLECTOR_HOME/scripts/$script"
done
chmod +x "$COLLECTOR_HOME/scripts/harvest_external_permalinks.py" "$COLLECTOR_HOME/scripts/recover_from_external_seeds.py"

echo "Synced development copy into runtime copy:"
echo "  $PIPELINE_HOME"
echo "  $COLLECTOR_HOME"
echo "Restart the services so the new code takes effect:"
echo "  $PIPELINE_HOME/scripts/macos/manage_services.sh restart"
