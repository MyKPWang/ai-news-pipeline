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

    return {
        "hot_items": global_info.get("hot_topics", [])[:5],
        "insight": global_info.get("insight", ""),
        "categories": categories,
    }


def build_article_title(now: datetime | None = None) -> str:
    now = now or datetime.now()
    return f"AI资讯精选 | {now.strftime('%Y-%m-%d')}"


def collect_sources(items: list[NewsItem]) -> list[str]:
    return sorted({item.source for item in items if item.source})
