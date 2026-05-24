from __future__ import annotations

import importlib.util
import json
from pathlib import Path


def publish_to_wechat_tool(
    data: dict,
    title: str,
    sources: list[str],
    config: dict,
) -> bool:
    tool_dir = Path("wechat-publish-tool")
    module_path = tool_dir / "publish_news.py"
    if not module_path.exists():
        raise FileNotFoundError(f"wechat-publish-tool not found: {module_path}")

    _write_tool_config(tool_dir, config)

    spec = importlib.util.spec_from_file_location("wechat_publish_tool", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load wechat-publish-tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    author = config.get("wechat_publish", {}).get("author", "Valkyrie")
    return bool(module.publish_news(data, title, sources=sources, author=author))


def _write_tool_config(tool_dir: Path, config: dict) -> None:
    publish_config = config.get("wechat_publish", {})
    app_id = publish_config.get("app_id", "")
    app_secret = publish_config.get("app_secret", "")
    if not app_id or not app_secret:
        raise RuntimeError("wechat_publish app_id/app_secret missing in secrets.yaml")

    path = tool_dir / "news-config.json"
    current = {}
    if path.exists():
        try:
            current = json.loads(path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            current = {}
    current["app_id"] = app_id
    current["app_secret"] = app_secret
    if publish_config.get("thumb_media_id"):
        current["thumb_media_id"] = publish_config["thumb_media_id"]
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
