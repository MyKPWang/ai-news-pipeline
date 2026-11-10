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
sync_file "$DEV_PIPELINE/config.yaml" "$PIPELINE_HOME/config.yaml"
sync_file "$DEV_PIPELINE/secrets.yaml" "$PIPELINE_HOME/secrets.yaml"
sync_file "$DEV_PIPELINE/README.md" "$PIPELINE_HOME/README.md"
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

echo "Synced development copy into runtime copy:"
echo "  $PIPELINE_HOME"
echo "  $COLLECTOR_HOME"
echo "Restart the services so the new code takes effect:"
echo "  $PIPELINE_HOME/scripts/macos/manage_services.sh restart"
