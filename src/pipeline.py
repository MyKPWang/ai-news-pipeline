from __future__ import annotations

import json
import logging
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import requests

from .aggregator import build_article_title, build_publish_data, collect_sources
from .interceptors.bge_dedup import bge_dedup
from .interceptors.dedup import exact_dedup
from .interceptors.keyword_filter import keyword_filter
from .llm.minimax import LlmError, MiniMaxClient
from .logging_utils import setup_logging
from .models import NewsItem, PipelineResult
from .publisher import publish_to_wechat_tool, render_wechat_html
from .sources.github import GithubSource
from .sources.portals import PortalSource
from .sources.wechat import WechatApiSource
from .storage import Storage
from .time_utils import format_time_text, parse_publish_time
from .sources.wechat import WechatApiSource
from .storage import Storage
from .time_utils import format_time_text, parse_publish_time

logger = logging.getLogger(__name__)


def run_pipeline(config: dict, no_publish: bool = False) -> PipelineResult:
    storage = Storage(config.get("output", {}).get("database_path", "data/news.db"))
    run_date = datetime.now().strftime("%Y%m%d")
    run_id = storage.start_run(run_date)
    raw_items: list[NewsItem] = []
    selected_items: list[NewsItem] = []
    publishable_items: list[NewsItem] = []
    review_items: list[NewsItem] = []
    html_path: str | None = None
    published = False

    try:
        raw_items, github_items = collect_all_sources(config, storage, run_id)
        raw_id_map = storage.insert_raw_items(run_id, raw_items)
        _log(storage, run_id, "info", "pipeline", "collected", f"raw_items={len(raw_items)}")

        candidates, review_time = filter_recent(raw_items, config)
        review_items.extend(review_time)
        for item in review_time:
            storage.upsert_processed(run_id, item, "review", raw_id_map.get(item.id))
            storage.add_filter_event(
                run_id, "time_filter", "review", item, raw_id_map.get(item.id),
                "time_unparsed_or_out_of_range", "无法解析时间或不在24小时窗口内"
            )
        _log(storage, run_id, "info", "pipeline", "time_filter", f"kept={len(candidates)} review={len(review_time)}")

        candidates, review_quality = quality_filter(candidates)
        review_items.extend([item for item, _reason in review_quality])
        for item, reason in review_quality:
            storage.upsert_processed(run_id, item, "review", raw_id_map.get(item.id))
            storage.add_filter_event(run_id, "quality_filter", "review", item, raw_id_map.get(item.id), reason, reason)

        kw_result = keyword_filter(candidates)
        for item, reason in kw_result.removed:
            storage.add_filter_event(run_id, "keyword_filter", "removed", item, raw_id_map.get(item.id), "keyword", reason)
            storage.upsert_processed(run_id, item, "filtered", raw_id_map.get(item.id))
        for item, reason in kw_result.protected:
            storage.add_filter_event(run_id, "keyword_filter", "warning", item, raw_id_map.get(item.id), "positive_protected", reason)
        candidates = sort_by_source_priority(kw_result.kept)

        dedup_result = exact_dedup(candidates)
        for item, reason in dedup_result.removed:
            storage.add_filter_event(run_id, "exact_dedup", "removed", item, raw_id_map.get(item.id), reason, reason)
            storage.upsert_processed(run_id, item, "filtered", raw_id_map.get(item.id))
        candidates = dedup_result.kept

        bge_result = bge_dedup(candidates, config)
        for item, reason in bge_result.removed:
            storage.add_filter_event(run_id, "bge_dedup", "removed", item, raw_id_map.get(item.id), "semantic_duplicate", reason)
            storage.upsert_processed(run_id, item, "filtered", raw_id_map.get(item.id))
        candidates = bge_result.kept
        _log(storage, run_id, "info", "pipeline", "pre_llm", f"candidates={len(candidates)}")

        llm = MiniMaxClient(config, storage, run_id)
        global_info: dict = {"hot_topics": [], "insight": "", "selected": []}
        try:
            selected_items, global_info = llm.select_items(candidates)
        except LlmError as exc:
            _log(storage, run_id, "error", "llm", "global_select_failed", str(exc))
            raise

        for item in selected_items:
            storage.upsert_processed(run_id, item, "selected", raw_id_map.get(item.id))

        cached_rewritten: list[NewsItem] = []
        items_to_rewrite = selected_items
        if config.get("runtime", {}).get("reuse_rewrite_cache", True):
            cached_by_id = storage.get_latest_rewritten_items(item.id for item in selected_items)
            items_to_rewrite = []
            for item in selected_items:
                cached = cached_by_id.get(item.id)
                if cached:
                    item.rewritten_title = str(cached.get("rewritten_title") or "").strip()
                    item.summary = str(cached.get("summary") or "").strip()
                    item.risk_flags = [str(flag) for flag in cached.get("risk_flags", [])]
                    cached_rewritten.append(item)
                    storage.upsert_processed(run_id, item, "rewritten", raw_id_map.get(item.id))
                    storage.add_filter_event(
                        run_id,
                        "rewrite_cache",
                        "reused",
                        item,
                        raw_id_map.get(item.id),
                        "rewrite_cache_hit",
                        "Reused rewritten title and summary from previous run",
                    )
                else:
                    items_to_rewrite.append(item)
            if cached_rewritten:
                _log(
                    storage,
                    run_id,
                    "info",
                    "pipeline",
                    "rewrite_cache",
                    f"reused={len(cached_rewritten)} pending={len(items_to_rewrite)}",
                )

        rewritten = []
        if items_to_rewrite:
            rewritten = llm.rewrite_items(
                items_to_rewrite,
                on_item_rewritten=lambda item: storage.upsert_processed(
                    run_id, item, "rewritten", raw_id_map.get(item.id)
                ),
            )
        rewritten = cached_rewritten + rewritten
        rewritten_ids = {item.id for item in rewritten}
        for item in selected_items:
            if item.id not in rewritten_ids:
                item.risk_flags.append("rewrite_missing")
            stage = "rewritten" if item.id in rewritten_ids else "review"
            storage.upsert_processed(run_id, item, stage, raw_id_map.get(item.id))

        publishable_items, rewrite_review = validate_rewritten(
            selected_items,
            copy_threshold=int(config.get("output", {}).get("copy_check_threshold", 20)),
        )
        review_items.extend(rewrite_review)
        for item in rewrite_review:
            storage.upsert_processed(run_id, item, "review", raw_id_map.get(item.id))
            storage.add_filter_event(
                run_id, "rewrite_check", "review", item, raw_id_map.get(item.id),
                "rewrite_risk", ",".join(item.risk_flags)
            )
        for item in publishable_items:
            storage.upsert_processed(run_id, item, "publishable", raw_id_map.get(item.id))

        data = build_publish_data(publishable_items, global_info)
        # 生成 GitHub 趋势榜图片
        github_trending_image_url = None
        github_trending_data = None
        if github_items:
            from .generate_github_table import generate_github_trending_table, upload_image_to_wechat
            output_dir = Path(__file__).parent.parent / "wechat-publish-tool" / "output"
            output_dir.mkdir(parents=True, exist_ok=True)
            img_path = generate_github_trending_table(github_items, output_dir)
            if img_path:
                # 上传到微信素材库获取永久 URL
                publish_config = config.get("wechat_publish", {})
                github_trending_image_url = upload_image_to_wechat(img_path, publish_config)
                if not github_trending_image_url:
                    # 上传失败时用本地路径
                    github_trending_image_url = img_path
                github_trending_data = {
                    "image_url": github_trending_image_url,
                    "repos": [
                        {
                            "title": item.title,
                            "desc": item.desc,
                            "link": item.url,
                            "stars": item.extra.get("stars", "–"),
                            "language": item.extra.get("language", ""),
                        }
                        for item in github_items[:10]
                    ],
                }
                data["github_trending"] = github_trending_data
        title = build_article_title()
        sources = collect_sources(publishable_items)
        html_path = render_wechat_html(data, title, sources, config)

        should_publish = should_upload(config, no_publish, selected_items, publishable_items, review_items)
        if should_publish:
            published = publish_to_wechat_tool(data, title, sources, config)
            # 发完草稿箱后，发送 review 列表到飞书
            if published:
                _send_review_list_to_feishu(config, storage, run_id, len(publishable_items), run_date)

        export_outputs(config, storage, run_date, raw_items, publishable_items, review_items)
        status = "success" if published or no_publish else "partial"
        storage.finish_run(run_id, status, len(raw_items), len(selected_items), len(publishable_items))
        return PipelineResult(
            run_id=run_id,
            raw_items=raw_items,
            selected_items=selected_items,
            publishable_items=publishable_items,
            review_items=review_items,
            html_path=html_path,
            published=published,
        )
    except Exception as exc:
        storage.finish_run(
            run_id,
            "failed",
            len(raw_items),
            len(selected_items),
            len(publishable_items),
            str(exc),
        )
        raise


def collect_all_sources(config: dict, storage: Storage, run_id: int) -> tuple[list[NewsItem], list[NewsItem]]:
    items: list[NewsItem] = []
    github_items: list[NewsItem] = []
    for source in (WechatApiSource(config), PortalSource(config), GithubSource(config)):
        try:
            collected = source.collect()
            if source.name == "github":
                github_items = collected
            else:
                items.extend(collected)
            _log(storage, run_id, "info", "source", source.name, f"count={len(collected)}")
        except Exception as exc:
            _log(storage, run_id, "error", "source", source.name, str(exc))
            logger.warning("Source failed: %s %s", source.name, exc)
    return items, github_items


def sort_by_source_priority(items: list[NewsItem]) -> list[NewsItem]:
    return sorted(
        items,
        key=lambda item: (
            0 if item.source_type == "wechat_mp" else 1,
            -len(item.content or ""),
            -len(item.desc or ""),
        ),
    )


def filter_recent(items: list[NewsItem], config: dict) -> tuple[list[NewsItem], list[NewsItem]]:
    lookback = int(config.get("runtime", {}).get("lookback_hours", 24))
    threshold = datetime.now() - timedelta(hours=lookback)
    kept: list[NewsItem] = []
    review: list[NewsItem] = []
    for item in items:
        parsed = parse_publish_time(item)
        if parsed:
            item.publish_time = parsed
            if not item.time_text:
                item.time_text = format_time_text(parsed)
        if not parsed:
            review.append(item)
            item.risk_flags.append("time_unparsed")
            continue
        if datetime.fromtimestamp(parsed) < threshold:
            review.append(item)
            item.risk_flags.append("out_of_lookback")
            continue
        kept.append(item)
    return kept, review


def quality_filter(items: list[NewsItem]) -> tuple[list[NewsItem], list[tuple[NewsItem, str]]]:
    kept: list[NewsItem] = []
    review: list[tuple[NewsItem, str]] = []
    for item in items:
        if not item.title.strip():
            item.risk_flags.append("missing_title")
            review.append((item, "missing_title"))
        elif not item.url.strip():
            item.risk_flags.append("missing_url")
            review.append((item, "missing_url"))
        elif not item.desc.strip() and not item.content.strip():
            item.risk_flags.append("missing_summary_and_content")
            review.append((item, "missing_summary_and_content"))
        else:
            kept.append(item)
    return kept, review


def validate_rewritten(items: list[NewsItem], copy_threshold: int = 20) -> tuple[list[NewsItem], list[NewsItem]]:
    publishable: list[NewsItem] = []
    review: list[NewsItem] = []
    for item in items:
        if not item.rewritten_title.strip():
            item.risk_flags.append("missing_rewritten_title")
        if not item.summary.strip():
            item.risk_flags.append("missing_summary")
        if has_long_copy(item, threshold=copy_threshold):
            item.risk_flags.append("possible_copying")
        # unverified_rumor 不作为发布障碍，只记录在 review 里供参考
        blocking_flags = [f for f in item.risk_flags if f not in ("unverified_rumor",)]
        if blocking_flags:
            item.risk_flags = blocking_flags
            review.append(item)
        else:
            publishable.append(item)
    return publishable, review


def has_long_copy(item: NewsItem, threshold: int = 10) -> bool:
    original = "".join(ch for ch in f"{item.title}{item.desc}{item.content}" if "\u4e00" <= ch <= "\u9fff")
    rewritten = f"{item.rewritten_title}{item.summary}"
    rewritten_chinese = "".join(ch for ch in rewritten if "\u4e00" <= ch <= "\u9fff")
    if len(original) < threshold or len(rewritten_chinese) < threshold:
        return False
    for start in range(0, len(original) - threshold + 1):
        chunk = original[start : start + threshold]
        if chunk in rewritten_chinese:
            return True
    return False


def should_upload(
    config: dict,
    no_publish: bool,
    selected_items: list[NewsItem],
    publishable_items: list[NewsItem],
    review_items: list[NewsItem],
) -> bool:
    if no_publish or not config.get("wechat_publish", {}).get("auto_publish", False):
        return False
    min_items = int(config.get("output", {}).get("min_publishable_items", 3))
    if len(publishable_items) < min_items:
        return False
    if selected_items:
        ratio = len([item for item in selected_items if item in review_items]) / len(selected_items)
        max_ratio = float(config.get("output", {}).get("max_review_ratio_for_publish", 0.3))
        if ratio > max_ratio:
            return False
    return True


def export_outputs(
    config: dict,
    storage: Storage,
    run_date: str,
    raw_items: list[NewsItem],
    publishable_items: list[NewsItem],
    review_items: list[NewsItem],
) -> None:
    output_cfg = config.get("output", {})
    if output_cfg.get("save_raw_json", True):
        storage.export_items_json(f"output/raw_{run_date}.json", raw_items)
    if output_cfg.get("save_processed_json", True):
        storage.export_items_json(f"output/processed_{run_date}.json", publishable_items)
    storage.export_items_json(f"output/review_{run_date}.json", review_items)


def _log(storage: Storage, run_id: int, level: str, module: str, event: str, message: str) -> None:
    getattr(logger, level if level in ("debug", "info", "warning", "error") else "info")(
        "%s %s", event, message
    )
    storage.add_app_log(run_id, level, module, event, message)


# ---------------------------------------------------------------
# Feishu review list notification
# ---------------------------------------------------------------

def _get_feishu_token(app_id: str, app_secret: str, timeout: int = 10) -> str | None:
    """获取 Feishu 应用_access_token。"""
    if not app_id or not app_secret:
        return None
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/auth/v3/tenant_access_token/internal",
            json={"app_id": app_id, "app_secret": app_secret},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("code") == 0:
            return data.get("tenant_access_token")
    except Exception as exc:
        logger.warning("Failed to get Feishu token: %s", exc)
    return None


def _send_feishu_text(user_open_id: str, text: str, token: str, timeout: int = 20) -> bool:
    """发送富文本消息给指定用户。"""
    try:
        resp = requests.post(
            "https://open.feishu.cn/open-apis/im/v1/messages",
            params={"receive_id_type": "open_id"},
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
            json={
                "receive_id": user_open_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}, ensure_ascii=False),
            },
            timeout=timeout,
        )
        resp.raise_for_status()
        return resp.json().get("code") == 0
    except Exception as exc:
        logger.warning("Failed to send Feishu message: %s", exc)
        return False


_FILTER_LABELS: dict[str, str] = {
    "time_filter": "【超时过滤】",
    "quality_filter": "【质量过滤】",
    "keyword_filter": "【关键词过滤】",
    "rewrite_check": "【重写校验】",
}


def _review_run_info_path() -> Path:
    return Path(__file__).parent.parent / ".latest_review_run.json"


def _save_review_run(run_id: int, run_date: str) -> None:
    path = _review_run_info_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as f:
        json.dump({"run_id": run_id, "run_date": run_date}, f)


def _load_latest_review_run() -> tuple[int, str] | None:
    path = _review_run_info_path()
    if not path.exists():
        return None
    try:
        with path.open("r", encoding="utf-8") as f:
            data = json.load(f)
        return int(data.get("run_id", 0)), str(data.get("run_date", ""))
    except Exception:
        return None


def _send_review_list_to_feishu(
    config: dict,
    storage: Storage,
    run_id: int,
    published_count: int,
    run_date: str,
) -> None:
    """从 storage 查询 review 列表，统一编号后发送飞书。"""
    review_cfg = config.get("review_list", {})
    if not review_cfg.get("feishu_enabled", False):
        return

    secrets = config.get("_secrets", {})
    feishu_cfg = secrets.get("feishu", {})
    app_id = str(feishu_cfg.get("app_id", "")).strip()
    app_secret = str(feishu_cfg.get("app_secret", "")).strip()
    user_open_id = str(review_cfg.get("feishu_user_id", "")).strip()
    if not app_id or not app_secret or not user_open_id:
        logger.warning("Feishu review list: missing app_id/app_secret/user_id in config")
        return

    stages: list[str] = []
    if review_cfg.get("include_time_filter", False):
        stages.append("time_filter")
    if review_cfg.get("include_quality_filter", True):
        stages.append("quality_filter")
    if review_cfg.get("include_keyword_filter", True):
        stages.append("keyword_filter")
    stages.append("rewrite_check")
    if not stages:
        return

    # 保存 run_id，用户回复「补充 X」时我知道查哪批
    _save_review_run(run_id, run_date)

    # 扁平列表，统一编号
    items = storage.get_review_items_flat(run_id, stages)

    lines_out: list[str] = []
    lines_out.append(f"📋 **{run_date} 备选文章列表**")
    lines_out.append("")
    lines_out.append(f"已自动发布：{published_count} 条 | 以下为未被选用的备选文章（共 {len(items)} 条）：")
    lines_out.append("")

    if not items:
        lines_out.append("（无备选文章）")
        lines_out.append("")
    else:
        for i, item in enumerate(items, 1):
            title = item.title or "(无标题)"
            url = item.url or ""
            source = item.source or ""
            time_text = item.time_text or ""
            if len(title) > 45:
                title = title[:45] + "..."
            line = f"{i}. {title}"
            if source:
                line += f" - {source}"
            if time_text:
                line += f" ({time_text})"
            lines_out.append(line)
            if url:
                lines_out.append(f"   🔗 {url}")

    lines_out.append("")
    lines_out.append("---")
    lines_out.append("回复格式：如需补充，请直接回复「补充 + 编号」")
    lines_out.append("示例：补充 1、3、5")

    text = "\n".join(lines_out)

    token = _get_feishu_token(app_id, app_secret)
    if not token:
        logger.warning("Feishu review list: could not get access token")
        return

    sent = _send_feishu_text(user_open_id, text, token)
    if sent:
        logger.info("Feishu review list sent: %d items", len(items))
    else:
        logger.warning("Failed to send Feishu review list")



# ---------------------------------------------------------------
# Supplement handler: user picks items to add to draft
# ---------------------------------------------------------------

def handle_supplement(
    user_input: str,
    config: dict,
    storage: Storage,
) -> tuple[bool, str]:
    """处理用户「补充 X、Y」请求。

    Returns (success, message).
    """
    # 1. 解析编号
    numbers = _parse_supplement_numbers(user_input)
    if not numbers:
        return False, "无法解析编号，请使用「补充 1、3、5」格式"

    # 2. 读取最近一次 review run
    run_info = _load_latest_review_run()
    if not run_info:
        return False, "未找到最近的 review 记录，请先触发一次采集任务"
    run_id, run_date = run_info

    # 3. 确认 run_id 对应的草稿箱已有内容（避免乱补充）
    existing = storage.get_published_items_for_run(run_id)
    if not existing:
        return False, f"run_id={run_id} 没有已发布的文章，请先触发采集生成初稿"

    # 4. 获取 review 列表
    review_cfg = config.get("review_list", {})
    stages: list[str] = []
    if review_cfg.get("include_time_filter", False):
        stages.append("time_filter")
    if review_cfg.get("include_quality_filter", True):
        stages.append("quality_filter")
    if review_cfg.get("include_keyword_filter", True):
        stages.append("keyword_filter")
    stages.append("rewrite_check")
    review_items = storage.get_review_items_flat(run_id, stages)

    # 5. 按编号选出文章（编号从 1 开始）
    selected_for_rewrite: list[NewsItem] = []
    invalid_nums: list[int] = []
    for num in numbers:
        idx = num - 1
        if idx < 0 or idx >= len(review_items):
            invalid_nums.append(num)
        else:
            selected_for_rewrite.append(review_items[idx])

    if invalid_nums:
        return False, f"编号 {invalid_nums} 超出范围，有效范围 1～{len(review_items)}"
    if not selected_for_rewrite:
        return False, "未选中任何文章"

    logger.info("Supplements: run_id=%s, numbers=%s, selected=%d", run_id, numbers, len(selected_for_rewrite))

    # 6. LLM 重写
    llm_config = config.get("llm", {})
    llm_client = MiniMaxClient(llm_config, storage, run_id)
    try:
        rewritten = llm_client.rewrite_items(selected_for_rewrite)
    except Exception as exc:
        logger.error("Supplement rewrite failed: %s", exc)
        return False, f"LLM 重写失败：{exc}"

    if not rewritten:
        return False, "LLM 重写返回空结果"

    # 7. 追加到已有发布列表
    combined_items = existing + rewritten
    logger.info("Supplements: existing=%d + rewritten=%d = combined=%d",
                 len(existing), len(rewritten), len(combined_items))

    # 8. 重新构建 HTML
    title = build_article_title()
    sources = collect_sources(combined_items)
    data = build_publish_data(combined_items)

    # GitHub 趋势榜（复用已有逻辑）
    github_trending_data = None
    github_items: list[NewsItem] = []
    try:
        from .generate_github_table import generate_github_trending_table, upload_image_to_wechat
        output_dir = Path(__file__).parent.parent / "wechat-publish-tool" / "output"
        output_dir.mkdir(parents=True, exist_ok=True)
        img_path = generate_github_trending_table(github_items, output_dir)
        if img_path:
            publish_config = config.get("wechat_publish", {})
            github_trending_image_url = upload_image_to_wechat(img_path, publish_config)
            if not github_trending_image_url:
                github_trending_image_url = img_path
            github_trending_data = {"image_url": github_trending_image_url, "repos": []}
    except Exception as exc:
        logger.warning("Supplement github table skipped: %s", exc)

    if github_trending_data:
        data["github_trending"] = github_trending_data

    html_path = render_wechat_html(data, title, sources, config)

    # 9. 推送到草稿箱（强制覆盖）
    publish_cfg = config.get("wechat_publish", {})
    is_dry = not publish_cfg.get("auto_publish", False)
    try:
        published = publish_to_wechat_tool(data, title, sources, config)
    except Exception as exc:
        logger.error("Supplement publish failed: %s", exc)
        return False, f"推送草稿箱失败：{exc}"

    if not published and not is_dry:
        return False, "推送草稿箱失败（auto_publish=true）"

    # 10. 发飞书通知
    _notify_supplement_done(config, len(rewritten), title, run_date)

    logger.info("Supplement done: added %d items, draft updated", len(rewritten))
    return True, f"已补充 {len(rewritten)} 条文章并更新草稿箱"


def _notify_supplement_done(
    config: dict,
    added_count: int,
    title: str,
    run_date: str,
) -> None:
    review_cfg = config.get("review_list", {})
    if not review_cfg.get("feishu_enabled", False):
        return
    secrets = config.get("_secrets", {})
    feishu_cfg = secrets.get("feishu", {})
    app_id = str(feishu_cfg.get("app_id", "")).strip()
    app_secret = str(feishu_cfg.get("app_secret", "")).strip()
    user_open_id = str(review_cfg.get("feishu_user_id", "")).strip()
    if not app_id or not app_secret or not user_open_id:
        return

    text = f"✅ 已补充 {added_count} 条文章，草稿箱已更新。\n\n📄 {title}"
    token = _get_feishu_token(app_id, app_secret)
    if token:
        _send_feishu_text(user_open_id, text, token)


def _parse_supplement_numbers(user_input: str) -> list[int]:
    """从「补充 1、3、5」类似文本解析出编号列表。"""
    import re
    user_input = user_input.strip()
    # 支持「补充1,3,5」「补充 1、3、5」「补充1 3 5」「补充1.3.5」等
    patterns = [
        r"补充\D*(\d+(?:\D+\d+)*)",
        r"补充\s*(\d+(?:\s+\d+)*)",
    ]
    for pattern in patterns:
        m = re.search(pattern, user_input)
        if not m:
            continue
        part = m.group(1)
        # 替换分隔符为空格，再用空格 split
        for sep in ["、", ",", "，", ".", "，"]:
            part = part.replace(sep, " ")
        nums = [int(x) for x in part.split() if x.isdigit()]
        return nums
    return []
