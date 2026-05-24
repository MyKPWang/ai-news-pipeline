from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field
from typing import Any


@dataclass
class NewsItem:
    id: str = ""
    title: str = ""
    desc: str = ""
    content: str = ""
    html_content: str = ""
    source: str = ""
    source_type: str = ""
    url: str = ""
    publish_time: int | None = None
    time_text: str = ""
    cover_url: str = ""
    category: str = ""
    selected: bool = False
    selection_reason: str = ""
    core_fact: str = ""
    rewritten_title: str = ""
    summary: str = ""
    risk_flags: list[str] = field(default_factory=list)
    extra: dict[str, Any] = field(default_factory=dict)

    def ensure_id(self) -> str:
        if not self.id:
            self.id = make_item_id(self.source, self.url, self.title, self.publish_time)
        return self.id

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["extra"] = self.extra or {}
        data["risk_flags"] = self.risk_flags or []
        return data

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "NewsItem":
        item = cls(**{k: v for k, v in data.items() if k in cls.__dataclass_fields__})
        item.extra = item.extra or {}
        item.risk_flags = item.risk_flags or []
        item.ensure_id()
        return item

    def text_for_filter(self) -> str:
        return f"{self.title or ''} {self.desc or ''} {self.content or ''}".strip()

    def text_for_llm_selection(self, index: int) -> dict[str, Any]:
        return {
            "index": index,
            "source_type": "公众号" if self.source_type == "wechat_mp" else "网页",
            "title": self.title or "",
            "summary": self.desc or "",
        }

    def to_publish_dict(self) -> dict[str, Any]:
        return {
            "rewritten_title": self.rewritten_title,
            "summary": self.summary,
            "source": self.source,
            "link": self.url,
            "time_ago": self.time_text,
            "original_title": self.title,
            "original_desc": self.desc,
        }


def make_item_id(source: str, url: str, title: str, publish_time: int | None) -> str:
    raw = json.dumps(
        {
            "source": source or "",
            "url": url or "",
            "title": title or "",
            "publish_time": publish_time or "",
        },
        ensure_ascii=False,
        sort_keys=True,
    )
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()[:16]


@dataclass
class PipelineResult:
    run_id: int
    raw_items: list[NewsItem]
    selected_items: list[NewsItem]
    publishable_items: list[NewsItem]
    review_items: list[NewsItem]
    html_path: str | None = None
    published: bool = False
