# 本机运行清单

本机由三个常驻任务组成：`public-source-collector` 负责访问公开资讯列表、缓存和来源级审计；资讯日报控制台负责人工操作与任务追踪；`ai-news-pipeline` 负责筛选、改写与生成。它们不使用 Docker，也不采集微信公众号文章。

## 当前检查结果

检查日期：2026-08-08。

| 项目 | 状态 | 说明 |
| --- | --- | --- |
| macOS arm64、Xcode 工具链 | 就绪 | 当前机器为 Apple Silicon。 |
| 存储空间 | 就绪 | 约 653 GiB 可用，足以保存 SQLite、日志和备份。 |
| Google Chrome | 就绪 | 浏览器型公开源会使用专属 Collector Profile，不读取日常 Chrome Cookie。 |
| Pipeline venv | 就绪 | `.venv/bin/python` 可运行。 |
| Collector venv / CLI | 就绪 | `.venv/bin/public-source-collector` 可运行；旧命令保留为兼容别名。 |
| Collector API (`127.0.0.1:4100`) | 手工运行可用 | 由 `run_collector_api.sh` 启动，服务仅监听本机。 |
| Collector API Token | 已配置 | 仅保存于本机环境文件，不应写入仓库。 |
| `launchd` 常驻/定时任务 | 需先部署运行副本 | 不能直接从 `Documents` 下的开发目录运行。 |

## 配置文件

本机唯一的跨进程私密配置是 [local-machine.env](../deploy/macos/local-machine.env)。它已被 Git 忽略；可提交样例是 [local-machine.env.example](../deploy/macos/local-machine.env.example)。

必须填写：

```text
PUBLIC_SOURCE_COLLECTOR_PIPELINE_TOKEN=<使用随机长字符串>
```

该 Token 只保存在本机环境文件中：Collector API 用它保护读取与刷新接口，Pipeline 运行包装脚本把相同值导出为 `PUBLIC_SOURCE_API_TOKEN`。不要写入 `config.yaml`、plist、终端历史或代码仓库。

`secrets.yaml` 继续只保存 MiniMax Key，以及正式发布时所需的公众号 App ID / App Secret。首次验证时保持 `wechat_publish.auto_publish: false`，并用 `--no-publish` 运行。

```bash
chmod 600 deploy/macos/local-machine.env
python3 -c 'import secrets; print(secrets.token_urlsafe(32))'
```

将第二条命令生成的值填入环境文件后，执行以下初始化。命令均在各自项目目录中运行：

```bash
# Collector: 建表并登记当前启用的公开源
.venv/bin/public-source-collector --config collector.toml db upgrade
.venv/bin/public-source-collector --config collector.toml sources sync
.venv/bin/public-source-collector --config collector.toml sources diagnose
.venv/bin/public-source-collector --config collector.toml sources health
```

## macOS 常驻与定时

三个 `launchd` 模板位于 `deploy/macos/`：

| Job | 行为 |
| --- | --- |
| `com.wangkp.ai-news.collector-api` | 开机登录后启动 API，仅监听 `127.0.0.1:4100`；异常退出后自动重启。 |
| `com.wangkp.ai-news.collector-refresh` | 每天 23:50 触发一次公开源采集，脚本增加 0-120 秒抖动。 |
| `com.wangkp.ai-news.console` | 登录 macOS 后启动本机控制台，仅监听 `127.0.0.1:4102`；异常退出后自动重启。 |
| `com.wangkp.ai-news.daily-report` | 每天 08:00 触发一次日报生成，脚本增加 0-120 秒抖动。该任务自身会刷新 Collector 缓存，不依赖 23:50 的采集任务。 |

### 开发副本与运行副本

`~/Documents` 下的两个目录是**开发副本**，`~/Developer` 下的是 **launchd 运行副本**。launchd 只能执行运行副本，因此：

- 改代码在 `~/Documents` 里改，改完执行 `scripts/macos/sync_runtime.sh` 同步到 `~/Developer`，再 `manage_services.sh restart` 生效。
- `deploy/macos/*.plist` 里的绝对路径指向运行副本（`~/Developer`），同步脚本不会覆盖它们。
- 运行副本里的 `.venv` 是独立创建的，不能从开发副本复制（shebang 指向固定路径）。

### 首次部署：把运行副本放到非受保护目录

macOS 的 TCC 隐私保护不会自动授权 `launchd` 读取 `~/Documents`、`Desktop`、`Downloads`。终端能运行不代表后台 Job 也能运行。将 Pipeline 与 Collector 的**实际运行副本**放到 `~/Developer` 或 `~/Library/Application Support`；不要用指回 `Documents` 的符号链接。

然后更新运行副本中 `deploy/macos/local-machine.env` 的以下绝对路径：`PIPELINE_HOME`、`COLLECTOR_HOME`、`PIPELINE_CONFIG`、`PIPELINE_SECRETS`、`COLLECTOR_CONFIG`。开发目录可继续保留在 `Documents` 用于编辑；更新代码后，再重新部署运行副本。

安装前确认运行副本的 `local-machine.env` 已配置 Token，再执行：

```bash
./scripts/macos/manage_services.sh install
```

此命令只需首次执行一次。之后登录 macOS 即自动启动；日常打开 `http://127.0.0.1:4102/console` 即可，不需要账号或密码。紧急重启运行 `./scripts/macos/manage_services.sh restart`，检查安装状态运行 `./scripts/macos/manage_services.sh status`。若路径仍位于受保护目录，安装脚本会停止并给出迁移提示，而不会创建失败的后台任务。

日报的触发时间不固化在此处：它可以由既有 Pipeline 任务或人工命令随时触发。每次日报都会再次调用 Collector 的刷新接口；Collector 的来源级间隔和同一幂等键会防止重复网络请求。

## 验收顺序

```bash
# 1. 只检查环境，不输出任何密钥。
python3 scripts/macos/doctor.py --strict

# 2. 检查 API 是否可用。
curl http://127.0.0.1:4100/health/ready

# 3. 手动执行一次采集。单个来源失败会写审计日志，但不会阻断其他来源。
/Users/wangkp/Documents/tools/ai-news-pipeline/scripts/macos/run_collector_scheduled.sh

# 4. 生成日报但不上传草稿。
/Users/wangkp/Documents/tools/ai-news-pipeline/scripts/macos/run_pipeline.sh --no-publish
```

第 3 步完成后，用 `sources diagnose` 查看每个来源的 `accepted/rejected/new/updated` 数量和失败原因，用 `sources health` 查看本批次的健康结论。每次采集还会将完整健康摘要写入 `data/health/`。第 4 步会先刷新 Collector 缓存，再以刷新返回的批次 ID 读取上海时区“昨日 00:00 至明日 00:00”的文章列表；之后才进入 Pipeline 的跨源去重、筛选与改写。

健康摘要按每次采集落盘：`data/health/latest.json` 始终指向最近一次，`data/health/<business-date>-<run-id>.json` 保留历史。摘要会列出每个来源的状态、数量、耗时、失败原因和告警。需要外部通知时，在 Collector 的 `collector.toml` 填写 `[health].webhook_url`；只有出现失败或部分失败告警时才发送精简 JSON，不包含文章正文、Cookie 或请求参数。未配置 webhook 时，采集不会受影响，仍可通过文件、日志和 `sources health` 排查。

## 日常运维

- API 日志：`logs/collector-api.launchd.log` 与 `logs/collector-api.launchd.error.log`。
- 定时采集日志：`logs/collector-refresh.launchd.log` 与 `logs/collector-refresh.launchd.error.log`。
- 日报日志：`logs/daily-report.launchd.log` 与 `logs/daily-report.launchd.error.log`。
- Collector 审计与应用日志：`public-source-collector/data/collector.db`、`public-source-collector/data/collector.log`、`public-source-collector/data/health/`。
- Pipeline 审计、输出与日志：`data/news.db`、`output/`、`logs/`。
- 数据库备份：`public-source-collector --config collector.toml backup --to <安全备份路径>`；升级前先备份，再运行 `db upgrade`。

不需要 Docker、微信扫码、内网穿透或开放 4100 端口到局域网。浏览器仅服务于少数明确配置的公开列表页适配器。


## WeChat article channel (微信读书)

The worker fetches WeChat Official Account articles through the WeRead channel
(`/mp/chapters`, `/mp/cover`, `/review/single`). The Official Account backend route
(`/cgi-bin/appmsgpublish`) is retired — it returns `ret=200013` for unverified accounts.

- Credentials live in the encrypted vault (`weread_accounts.enc`). WeRead `accessToken`s are
  short-lived; the client silently refreshes them via `refreshToken` on a 401.
- `-2014` (请求频率过高) is an account-level penalty. The client retries once after 1.5 s and then
  cools the account for 300 s. Do not burst these endpoints.
- Daily collection: `scripts/macos/run_wechat_collect.sh` (called by `run_daily_report.sh`).
- Full-chain check:
  ```bash
  cd ~/Developer/public-source-collector
  .venv/bin/python scripts/macos/verify_wechat_pipeline.py --account 量子位
  ```
  It prints each hop: credentials → live fetch → `articles` table → 4100 API → pipeline NewsItem.
- The 4100 endpoint is `GET /api/v1/pipeline/wechat/articles` (Bearer = collector pipeline token).
  It needs `published_from`, `published_before` and `limit`; send UTC with a `Z` suffix
  (`2026-11-09T00:00:00Z`) — an ISO offset like `+08:00` breaks the query string because the
  `+` is read as a space. The payload key is `articles`, not `items`.
- **Digest backfill runs automatically** at the end of `run_wechat_collect.sh`:
  ```bash
  cd ~/Developer/public-source-collector
  .venv/bin/public-source-collector --config collector.toml backfill-summaries --limit 60
  ```
  Collection only fills `reader_summary_max_items_per_page` (2) digests per page, so without this
  pass most stored articles have no digest and the pipeline files them under
  `missing_summary_and_content` — the report then ships with zero WeChat articles. The pass only
  touches articles inside the Shanghai report window (yesterday 00:00 → tomorrow 00:00).
  Output is `{"scanned": N, "filled": N, "empty": N, "stopped_reason": "..."}`; `empty` counts
  articles whose WeChat source genuinely has no `mpInfo.content`, which is not a bug.
- `worker --once` always exits 0, even when it claims no job. Its real outcome is on stdout as
  `{"processed": true|false}` — read that, not the exit code, when draining the queue.

## The pipeline's collector refresh timeout

`config.yaml` → `public_source_api.request_timeout_seconds` must exceed the real refresh duration.
The refresh crawls all 13 public endpoints (Playwright included) and takes **182–215 s**. A 180 s
timeout aborts the pipeline with `PublicSourceApiError: collector refresh request failed: ... Read
timed out` even though the refresh succeeded server-side. Current value: **420**.

## 自动创建微信公众号草稿（NexusAI Hub）

- 目标公众号：`NexusAI Hub` / AppID `wxdef888862e3ecca1` / gh_id `gh_559efaf1658e`
- **只创建草稿，不做正式发布**。该个人订阅号 `freepublish/*` 与 `message/mass/sendall` 均返回 `errcode=48001`，无需也无法走正式群发。
- 官方 API 链路：`material/add_material?type=thumb` → `draft/add`。
  - 封面源文件固定为 `wechat-publish-tool/cover.jpg`（**不要**用 `openclaw-skills/cover.jpg`，那是另一套图，与历史文章不一致）。
  - `cover_info.crop_percent_list` 使用 2.35:1 与 1:1 两种裁剪，与历史草稿一致。
- 凭据在 `secrets.yaml` 的 `wechat_publish` 段（mode 600），开关在 `deploy/macos/local-machine.env`：
  `PIPELINE_NO_PUBLISH_DEFAULT=false`。
- **微信短链规范**：微信短链字符集只有 `[A-Za-z0-9_-]`，不含 `~`。微信读书 reviewId 短 ID 里的 `~` 必须转成 `_`
  （`publish_news.py:normalize_wechat_links` 与 Collector 的 `short_id_from_review_id()` 都已处理）。
  带 `~` 的链接会被微信拒绝（`ret 64550`）。
- 平台行为：微信保存草稿后会把 `mp.weixin.qq.com/s/xxx` 改写成 `__biz` 长链，并可能移除部分**外站** href，
  但可见 URL 文本仍保留。这与历史草稿一致，非丢失。
- 官方 `draft/add` **没有** `claim_source_type` 字段，AI 生成声明只能在公众号后台编辑器里勾选。
- 验证草稿：`POST /cgi-bin/draft/get`，再下载 `thumb_url` 与历史封面做 SHA/RGB 比对。

### 上传失败只补传，绝不重跑 Pipeline

发布与内容生产已解耦：HTML 先落盘，再上传；上传失败会被记录为日志并把状态置为 `partial`，
不会中断采集，也不会重跑 MiniMax 改写。

- **上传幂等**：`src/wechat_draft.py::upsert_draft()` 先用 `draft/batchget` 按标题查同名草稿，
  存在则 `draft/update` 覆盖，不存在才 `draft/add`。重复执行同一天只会留下一条草稿。
- **白名单 IP 直接从错误里取**：`errcode 40164` 的 errmsg 里带微信看到的出口 IP，
  `WeChatIpNotWhitelistedError.ip` 会把它解析出来，日报日志和补传脚本都会打印这个 IP。
- **IP 变了由你手工处理**：在 微信公众平台 → 设置与开发 → 基本配置 → IP白名单 里加上脚本打印的 IP，
  然后重新执行补传命令即可。**不要**改 FlClash / Clash 规则或代理设置，也不要加自动重试的定时任务。

```bash
cd ~/Developer/ai-news-pipeline
.venv/bin/python scripts/macos/republish_draft.py --date 20261111          # 补传指定日期
.venv/bin/python scripts/macos/republish_draft.py                          # 补传最新一份 HTML
.venv/bin/python scripts/macos/republish_draft.py --date 20261111 --dry-run # 只检查，不调 API
```

脚本只读 `wechat-publish-tool/output/news_YYYYMMDD.html`，不重新采集、不重新摘要、
不调用 MiniMax。成功时打印 `action`（`created`/`updated`）和 `media_id`；
被白名单拒绝时打印 IP 与三步操作提示并以退出码 **2** 结束。
