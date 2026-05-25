from __future__ import annotations

import logging
from datetime import datetime, timedelta

from .aggregator import build_article_title, build_publish_data, collect_sources, render_preview_html
from .interceptors.bge_dedup import bge_dedup
from .interceptors.dedup import exact_dedup
from .interceptors.keyword_filter import keyword_filter
from .llm.minimax import LlmError, MiniMaxClient
from .models import NewsItem, PipelineResult
from .publisher import publish_to_wechat_tool
from .sources.portals import PortalSource
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
        raw_items = collect_all_sources(config, storage, run_id)
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

        rewritten = llm.rewrite_items(
            selected_items,
            on_item_rewritten=lambda item: storage.upsert_processed(
                run_id, item, "rewritten", raw_id_map.get(item.id)
            ),
        )
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
        title = build_article_title()
        html_path = render_preview_html(data, title)

        should_publish = should_upload(config, no_publish, selected_items, publishable_items, review_items)
        if should_publish:
            published = publish_to_wechat_tool(data, title, collect_sources(publishable_items), config)

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


def collect_all_sources(config: dict, storage: Storage, run_id: int) -> list[NewsItem]:
    items: list[NewsItem] = []
    for source in (WechatApiSource(config), PortalSource(config)):
        try:
            collected = source.collect()
            items.extend(collected)
            _log(storage, run_id, "info", "source", source.name, f"count={len(collected)}")
        except Exception as exc:
            _log(storage, run_id, "error", "source", source.name, str(exc))
            logger.warning("Source failed: %s %s", source.name, exc)
    return items


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
        if item.risk_flags:
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
