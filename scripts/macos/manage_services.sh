#!/bin/sh
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
. "$SCRIPT_DIR/load_local_env.sh"

COMMAND=${1:-status}
USER_ID=$(id -u)
AGENT_DIR="$HOME/Library/LaunchAgents"
LABELS="com.wangkp.ai-news.collector-api com.wangkp.ai-news.collector-refresh com.wangkp.ai-news.console com.wangkp.ai-news.daily-report"

require_launchd_access() {
  for path in "$PIPELINE_HOME" "$COLLECTOR_HOME"; do
    case "$path" in
      "$HOME/Documents"/*|"$HOME/Desktop"/*|"$HOME/Downloads"/*)
        echo "无法安装 launchd：$path 位于 macOS 受保护目录。" >&2
        echo "请将 Pipeline 与 Collector 的实际运行副本放到 ~/Developer 或 ~/Library/Application Support 后，" >&2
        echo "更新 deploy/macos/local-machine.env 中的绝对路径，再重新执行本命令。" >&2
        echo "开发目录可继续留在 Documents；不要用符号链接替代运行副本。" >&2
        exit 2
        ;;
    esac
  done
}

install_services() {
  require_launchd_access
  mkdir -p "$AGENT_DIR"
  # launchd refuses to spawn a job whose ProgramArguments script lacks the
  # executable bit, and it then keeps that job in a penalty box (EX_CONFIG 78)
  # that survives later chmod. Fix the mode before bootstrapping.
  for script in "$PIPELINE_HOME"/scripts/macos/*.sh; do
    [ -f "$script" ] || continue
    chmod +x "$script"
  done
  failed=""
  for label in $LABELS; do
    cp "$PIPELINE_HOME/deploy/macos/$label.plist" "$AGENT_DIR/$label.plist"
    launchctl bootout "gui/$USER_ID/$label" 2>/dev/null || true
    # `launchctl bootout` is asynchronous: the service is only fully removed
    # once `launchctl print` stops resolving it. Bootstrapping in the same
    # instant fails with "37: Operation already in progress" and, because the
    # script runs under `set -e`, that one failure aborts the whole loop --
    # leaving every later job uninstalled and the already-booted-out ones
    # dead (the Collector API goes down with them).
    wait=0
    while [ "$wait" -lt 50 ]; do
      if ! launchctl print "gui/$USER_ID/$label" >/dev/null 2>&1; then
        break
      fi
      sleep 0.2
      wait=$((wait + 1))
    done
    if ! launchctl bootstrap "gui/$USER_ID" "$AGENT_DIR/$label.plist"; then
      echo "安装失败：$label" >&2
      failed="$failed $label"
    fi
  done
  launchctl kickstart -k "gui/$USER_ID/com.wangkp.ai-news.collector-api" 2>/dev/null || true
  launchctl kickstart -k "gui/$USER_ID/com.wangkp.ai-news.console" 2>/dev/null || true
  if [ -n "$failed" ]; then
    echo "以下服务安装失败：$failed" >&2
    echo "排查：log show --predicate 'process == \"launchd\"' --last 5m --style compact | grep -i wangkp" >&2
    return 1
  fi
  echo "已安装并启动本机资讯服务。浏览器打开 http://127.0.0.1:4102/console"
}

status_services() {
  for label in $LABELS; do
    if launchctl print "gui/$USER_ID/$label" >/dev/null 2>&1; then
      echo "$label: 已安装"
    else
      echo "$label: 未安装"
    fi
  done
}

restart_services() {
  for label in com.wangkp.ai-news.collector-api com.wangkp.ai-news.console; do
    launchctl kickstart -k "gui/$USER_ID/$label"
  done
  echo "Collector API 与控制台已重启。"
}

case "$COMMAND" in
  install) install_services ;;
  status) status_services ;;
  restart) restart_services ;;
  *)
    echo "用法: $0 {install|status|restart}" >&2
    exit 2
    ;;
esac
