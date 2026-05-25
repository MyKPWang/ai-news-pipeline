from __future__ import annotations

from datetime import datetime
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape

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


def render_preview_html(data: dict, title: str, output_dir: str = "output") -> str:
    env = Environment(
        loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent / "templates")),
        autoescape=select_autoescape(["html"]),
        trim_blocks=True,
        lstrip_blocks=True,
    )
    template = env.get_template("preview.html")
    html = template.render(title=title, **data)
    out_dir = Path(output_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    path = out_dir / f"news_{datetime.now().strftime('%Y%m%d_%H%M%S')}.html"
    path.write_text(html, encoding="utf-8")
    return str(path)


def collect_sources(items: list[NewsItem]) -> list[str]:
    return sorted({item.source for item in items if item.source})
