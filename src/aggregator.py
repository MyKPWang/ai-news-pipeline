from __future__ import annotations

from datetime import datetime

from .models import NewsItem


def build_publish_data(items: list[NewsItem], global_info: dict | None = None) -> dict:
    global_info = global_info or {}
    categories = []
    for name in ("AI资讯", "智能硬件"):
        category_items = [item.to_publish_dict() for item in items if item.category == name]
        if category_items:
            categories.append({"name": name, "items": category_items})

    # hot_items 直接取已发布文章的前5条 rewritten_title，与 HTML 正文标题保持一致
    hot_items = [item.rewritten_title for item in items[:5] if item.rewritten_title.strip()]

    return {
        "hot_items": hot_items,
        "insight": global_info.get("insight", "") if global_info else "",
        "categories": categories,
    }


def build_article_title(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"AI资讯精选 | {now.strftime('%Y-%m-%d')}"


def collect_sources(items: list[NewsItem]) -> list[str]:
    return sorted({item.source for item in items if item.source})
