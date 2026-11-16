#!/bin/sh
# 外部转载站的原文永久链接 -> 补采公众号文章。
#
# 微信读书的索引每天每个公众号最多只显示 3 篇（实测 APPSO），所以有些推送
# 会整个不在索引里。recover_missing_articles.py 能沿官方原文页的「相关阅读」
# 做广度优先扩展，但它的种子只有「库里已经存在的完整永久链接」——闭包里捞
# 完就走不动了，因为从未被抓到过的文章连种子都没有。
#
# aipick.news 这类转载/索引站保留了原文的完整微信永久链接（__biz/mid/idx/
# sn/chksm 齐全），它们就是新种子。所以这条链路必须分两步：
#
#   1. harvest_external_permalinks.py  从 aipick 的 RSS / 按日归档 / 站点检索
#      里抽出原文永久链接，产出种子 JSON。只读公开页面，不碰微信。
#   2. recover_from_external_seeds.py  把种子交给 PermalinkRecoveryRunner，去
#      官方页面抓**原文**的标题、摘要、封面和准确发布时间，再走正式
#      repository 幂等写库。
#
# 分两步不能合并：转载站的元数据不可信（标题被编辑过、时间是转载时间、摘要
# 被截断），合并就等于把转载站的元数据写进库里污染日报。只有官方页面是真值。
#
# 排期 23:30：在 23:50 的 collector-refresh 之前跑完，这样 00:10 的 recovery
# 能吃到当天新写入的永久链接当 BFS 种子。抓取按 4 s 最小间隔 + 0~2 s jitter
# 限速，68 条种子约 6 分钟，必须留足到 23:50 的余量。
#
# 不要传 --cache：缓存文件存在时会完全跳过抓取（只复用旧结果），每日任务
# 就再也不会发现新文章。--cache 只是调试写库逻辑用的工具。
#
# NOTE: 这个文件必须保持 755 可执行。launchd 拒绝启动 ProgramArguments 指向
# 的不可执行脚本，并会把作业关进 penalty box，之后再 chmod 也救不回来。
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
HARVEST="$COLLECTOR_HOME/scripts/harvest_external_permalinks.py"
RECOVER="$COLLECTOR_HOME/scripts/recover_from_external_seeds.py"
SEEDS="$COLLECTOR_HOME/data/external_seeds.json"

if [ ! -x "$COLLECTOR_PY" ]; then
  echo "[external-recovery] collector venv 缺失: $COLLECTOR_PY" >&2
  exit 2
fi
for script in "$HARVEST" "$RECOVER"; do
  if [ ! -f "$script" ]; then
    echo "[external-recovery] 脚本缺失: $script" >&2
    exit 2
  fi
done

mkdir -p "$(dirname "$SEEDS")"
cd "$COLLECTOR_HOME"

# 第一步：发现种子。
# --max-pages 2000：候选条目页有 960 个左右（14 天归档 + 24 个检索词），
# 默认的 600 会漏掉三分之一的候选页，实测漏掉的页里还有 51 条永久链接。
echo "[external-recovery] 采集外部永久链接种子" >&2
if ! "$COLLECTOR_PY" "$HARVEST" --out "$SEEDS" --days 14 --max-pages 2000; then
  echo "[external-recovery] 种子采集失败；沿用上一份种子继续补采" >&2
  if [ ! -f "$SEEDS" ]; then
    exit 1
  fi
fi

# 第二步：补采并写库。--apply 才会真正写；写库走正式 repository 且幂等，
# 同一天跑两次不会多出重复文章。
echo "[external-recovery] 用外部种子补采公众号文章" >&2
if ! "$COLLECTOR_PY" "$RECOVER" \
  --config "$COLLECTOR_CONFIG" \
  --seeds "$SEEDS" \
  --max-articles 80 \
  --max-depth 1 \
  --apply; then
  # 补采失败不能拖垮整条链路：库里已有的文章仍然有效，日报照常能出。
  echo "[external-recovery] 补采失败；沿用库里已有的文章" >&2
fi
