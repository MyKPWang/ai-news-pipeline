from __future__ import annotations

import re
from dataclasses import dataclass

from ..models import NewsItem


@dataclass
class DedupResult:
    kept: list[NewsItem]
    removed: list[tuple[NewsItem, str]]


def exact_dedup(items: list[NewsItem]) -> DedupResult:
    seen: set[tuple[str, str]] = set()
    kept: list[NewsItem] = []
    removed: list[tuple[NewsItem, str]] = []

    for item in items:
        title_key = normalize_text(item.title)
        url_key = item.url.strip()
        key = (url_key, title_key)
        if key in seen:
            removed.append((item, "duplicate_title_url"))
            continue
        seen.add(key)
        kept.append(item)

    return DedupResult(kept=kept, removed=removed)


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", "", (text or "").strip().lower())
