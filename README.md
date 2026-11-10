# AI News Pipeline

AI 资讯日报的筛选、改写和微信公众号草稿箱上传工具。

## 当前架构

本项目不直接访问资讯网站，也不抓取微信公众号文章。公开网站的访问、来源级重试/降级、SQLite 缓存和来源内去重由独立的 `public-source-collector` 负责；Pipeline 只负责刷新该服务、读取缓存、跨源去重、筛选、改写和发布。

```text
public-source-collector
  public source adapters -> collector.db -> local Read API :4100
                                              |
                                              v
ai-news-pipeline
  refresh -> cached list query -> filter/dedup -> MiniMax -> HTML/draft
```

每次日报都按上海时区查询“昨日 00:00 至明日 00:00”的文章列表。Collector 单一来源失败会记录为 `partial` 并继续其他来源；刷新或缓存读取接口整体失败时，Pipeline 会终止本次运行，不发布空报告。

## 依赖

- Python 3 与两个项目各自的 `.venv`。
- 已部署的公共资讯采集服务，默认 `http://127.0.0.1:4100`。
- Collector API Bearer Token。
- MiniMax API Key。
- 仅正式上传草稿时需要微信公众号 `app_id` 与 `app_secret`。

浏览器依赖属于 Collector 的少量公开列表页适配器；Pipeline 本身不启动浏览器、不抓详情页。

## Pipeline 配置

`config.yaml` 只放非敏感配置：

```yaml
public_source_api:
  base_url: "http://127.0.0.1:4100"
  request_timeout_seconds: 180
  page_size: 500
  source_slugs: [] # 空数组表示所有启用来源

runtime:
  report_window: "previous_and_current_calendar_day"
```

`secrets.yaml` 放 MiniMax 与发布凭证。Collector API Token 可放在 `public_source_api.token`，也可使用环境变量 `PUBLIC_SOURCE_API_TOKEN`；macOS 部署包装脚本使用后者，避免在多个配置文件复制 Token。

## 运行

```bash
# 只生成日报，不上传草稿。
python3 -m src.main --no-publish

# 使用指定配置。
python3 -m src.main --config config.yaml --secrets secrets.yaml --no-publish
```

`--no-publish` 仍会执行刷新、读取缓存、筛选、去重、LLM 调用、SQLite 审计、review 输出和 HTML 生成，只跳过公众号草稿箱上传。

## 本机部署

这台 Mac 的完整运行清单、私密环境文件、`launchd` 常驻 API 和每日 23:50 定时采集配置见 [本机运行清单](docs/LOCAL_MACOS_RUNBOOK.md)。人工日常操作、Agent 使用边界和 API 集成方式见 [统一使用说明](../../wechat-paper-collect/public-source-collector/docs/USAGE_GUIDE.md)。部署文件在 [deploy/macos](deploy/macos)，Doctor 命令为：

```bash
python3 scripts/macos/doctor.py --strict
```

## 测试

```bash
python3 -m unittest discover -s tests
```

测试覆盖公共资讯 API 的鉴权、刷新、分页、列表字段映射、日历时间窗，以及既有的过滤、去重、LLM JSON 解析和改写合规校验。

## 输出与审计

- Pipeline SQLite：`data/news.db`
- Pipeline 日志：`logs/app_YYYYMMDD.log`、`logs/llm_YYYYMMDD.jsonl`
- 输出：`output/raw_*.json`、`output/processed_*.json`、`output/review_*.json`
- 公众号 HTML：`wechat-publish-tool/output/news_*.html`
- Collector SQLite 与来源审计：`public-source-collector/data/collector.db`

仓库中的旧 Docker 微信 API、门户直抓、扫码授权与对应 smoke 脚本仅为历史代码，不属于当前生产链路。
# 资讯日报 Pipeline

## 本机控制台（第一版）

控制台提供本机网页入口，用于查看来源和日报运行记录、发起采集、生成日报，并追踪失败后的重试任务。它监听 `127.0.0.1:4102`。

首次安装后执行一次：

```sh
./scripts/macos/manage_services.sh install
```

完成一次运行目录部署后，每次登录 macOS，Collector API 与控制台都会自动启动；每天 23:50 自动采集。直接打开 `http://127.0.0.1:4102/console`，无需登录。控制台只监听本机回环地址，不能从局域网或公网访问。

macOS 不会默认允许 `launchd` 访问 `~/Documents`、`Desktop`、`Downloads`。因此开发目录若位于这些位置，不能直接安装 Job；请按运行清单将两个**实际运行副本**部署到 `~/Developer` 或 `~/Library/Application Support`，再执行安装脚本。脚本会主动拒绝受保护路径，避免产生后台失败重试。

应急时可手动运行 `./scripts/macos/manage_services.sh restart`，查看状态用 `./scripts/macos/manage_services.sh status`。首次版本支持“刷新来源后生成日报”“使用缓存重跑日报”“采集全部”“采集单个来源”“仅重试上次失败来源”。网页触发的日报一律以 `--no-publish` 逻辑运行，不会自动上传微信草稿。

首次部署或更新 Collector 后，执行一次 `./scripts/macos/manage_services.sh restart`。这样“仅重试失败来源”能获得上次批次中的准确来源标识。
