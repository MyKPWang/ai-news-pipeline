# AI News Pipeline

AI 资讯采集、筛选、改写和微信公众号草稿箱上传工具。

## 功能范围

- 从电脑 A 本机 Docker 服务读取微信公众号缓存文章。
- 使用 Playwright 采集虎嗅、量子位、AIBase 列表页资讯。
- 统一写入 SQLite。
- 只保留最近 24 小时内资讯。
- 基于 `ai-news-v11` 过滤词做规则过滤，并保留 AI 相关正向保护。
- 使用 MiniMax-M2.7 完成全局筛选、分类和逐篇改写。
- 生成预览 HTML，可选择上传到微信公众号草稿箱。

## 安装

电脑 A 使用全局 Python 环境运行，不要求创建虚拟环境：

```bash
python3 -m pip install -r requirements.txt
python3 -m playwright install chromium
cp config.example.yaml config.yaml
cp secrets.example.yaml secrets.yaml
```

本地开发如果想隔离依赖，可以自行创建虚拟环境；生产部署文档不以虚拟环境为前提。

## 配置

`config.yaml` 存放非敏感配置，默认 Docker API 地址是：

```yaml
docker_api:
  base_url: "http://localhost:4000"
```

`secrets.yaml` 存放真实密钥，不要提交 GitHub：

```yaml
docker_api:
  username: "你的 Docker API 用户名"
  password: "你的 Docker API 密码"

minimax_api:
  api_key: "你的 MiniMax API Key"

wechat_publish:
  app_id: "公众号 app_id"
  app_secret: "公众号 app_secret"
```

MiniMax 模型默认使用 `MiniMax-M2.7`。

## 运行

常规运行：

```bash
python3 -m src.main
```

手动测试模式，不上传公众号草稿：

```bash
python3 -m src.main --no-publish
```

指定配置文件：

```bash
python3 -m src.main --config config.yaml --secrets secrets.yaml
```

`--no-publish` 仍会执行采集、过滤、去重、LLM 筛选、LLM 改写、SQLite 写入、日志记录和预览 HTML 生成，只跳过微信公众号草稿箱上传。

## 测试

### 本地回归测试

运行全部本地测试，不依赖外网、不调用真实 LLM、不连接电脑 A：

```bash
python3 -m unittest discover -s tests
```

测试覆盖时间解析、规则过滤、去重、LLM JSON 解析和序号映射、改写合规校验、HTML 原文 fallback 防护、mock pipeline，以及微信公众号 Docker API 客户端 mock。

### 真实门户抓取 Smoke

验证虎嗅、量子位、AIBase 页面结构是否仍和采集代码适配：

```bash
python3 scripts/portal_smoke.py --strict
```

该命令会真实访问 3 个门户网站列表页，输出每个来源的采集数量和样例标题。`--strict` 表示任一启用门户返回 0 条时直接失败。

### 真实 MiniMax Smoke

验证 MiniMax-M2.7 的鉴权、JSON 输出、全局筛选和逐篇改写：

```bash
python3 scripts/minimax_smoke.py
python3 scripts/minimax_rich_smoke.py
```

这两个脚本会读取本地 `secrets.yaml` 中的 MiniMax API Key。`minimax_smoke.py` 是最小样例，`minimax_rich_smoke.py` 使用更接近真实资讯流的混合样本验证分类数量约束、噪声过滤和批量改写。

### 电脑 A Dry Run

部署到电脑 A 后，先不要上传公众号草稿，执行：

```bash
python3 -m src.main --no-publish
```

这会真实读取电脑 A 本机 Docker 微信缓存服务、真实抓取门户网站、真实调用 MiniMax，并生成 SQLite、日志、review JSON 和预览 HTML，但不会上传公众号草稿。

## OpenClaw 定时任务示例

```bash
cd /path/to/ai-news-pipeline
python3 -m src.main
```

建议上线前先把 `config.yaml` 中的 `runtime.default_no_publish` 设为 `true`，或在 OpenClaw 任务里执行 `python3 -m src.main --no-publish` 验证几轮。

## 输出

- SQLite: `data/news.db`
- 应用日志: `logs/app_YYYYMMDD.log`
- LLM 完整请求/响应日志: `logs/llm_YYYYMMDD.jsonl`
- 原始数据导出: `output/raw_YYYYMMDD.json`
- 处理后数据导出: `output/processed_YYYYMMDD.json`
- 人工检查列表: `output/review_YYYYMMDD.json`
- 预览 HTML: `output/news_YYYYMMDD_HHMMSS.html`

## 发布说明

默认不会自动上传公众号草稿，除非：

- 没有传 `--no-publish`
- `config.yaml` 中 `wechat_publish.auto_publish` 为 `true`
- `secrets.yaml` 已配置公众号 `app_id` 和 `app_secret`
- 最终可发布条目数达到阈值
- review 比例没有超过阈值

公众号 HTML 样式由 `wechat-publish-tool` 内部模板负责。本项目只传入已经改写后的 `rewritten_title` 和 `summary`。

## 常见问题

- Playwright 报找不到浏览器：执行 `python3 -m playwright install chromium`。
- Docker API 连接失败：确认电脑 A 上服务可通过 `http://localhost:4000` 访问。
- LLM 失败：确认 `secrets.yaml` 中 `minimax_api.api_key` 已填写。
- 没有公众号草稿：确认没有使用 `--no-publish`，且 `wechat_publish.auto_publish: true`。
- 当天输出为空：微信公众号可能 24 小时内没有新文章，门户网站也可能没有通过筛选的内容，这不是程序错误。
