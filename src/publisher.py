from __future__ import annotations

import importlib.util
import json
import logging
from pathlib import Path

from .wechat_draft import (
    WeChatApiError,
    WeChatIpNotWhitelistedError,
    upsert_draft,
)

logger = logging.getLogger(__name__)

# The publish tool lives next to the package, not in the current working
# directory. Resolving it from the package location keeps the publisher
# independent of where the process was started, and -- more importantly -- keeps
# a caller (a test, an ad-hoc script) from writing its credentials into the
# production wechat-publish-tool/news-config.json by accident.
DEFAULT_TOOL_DIR = Path(__file__).resolve().parent.parent / "wechat-publish-tool"


def render_wechat_html(
    data: dict,
    title: str,
    sources: list[str],
    config: dict,
    tool_dir: str | Path = DEFAULT_TOOL_DIR,
) -> str:
    module = _load_publish_module(Path(tool_dir))
    author = config.get("wechat_publish", {}).get("author", "Valkyrie")
    compliance_contact = config.get("compliance", {}).get("contact", "")
    html_content = module.generate_html(
        data,
        title,
        sources=sources,
        author=author,
        compliance_contact=compliance_contact,
    )
    return str(module.save_html(html_content, title))


def publish_to_wechat_tool(
    data: dict,
    title: str,
    sources: list[str],
    config: dict,
    tool_dir: str | Path = DEFAULT_TOOL_DIR,
) -> bool:
    """Publish the report to the WeChat draft box.

    The upload is idempotent: a draft that already carries ``title`` is
    updated in place instead of a second copy being created, so re-running
    the publish step never leaves duplicates behind.

    A publish failure never propagates. The rendered HTML is already on disk,
    so the content is not lost and only the upload has to be retried.

    ``tool_dir`` defaults to the publish tool that ships next to this package.
    Pass an explicit path to write the credentials somewhere else -- a test
    must never overwrite the production ``news-config.json``.
    """
    tool_dir = Path(tool_dir)
    try:
        module = _load_publish_module(tool_dir)
        _write_tool_config(tool_dir, config)
        author = config.get("wechat_publish", {}).get("author", "Valkyrie")
        compliance_contact = config.get("compliance", {}).get("contact", "")

        html_content = module.generate_html(
            data,
            title,
            sources=sources,
            author=author,
            compliance_contact=compliance_contact,
        )
        module.save_html(html_content, title)
        html_content, fixed_links = module.normalize_wechat_links(html_content)
        if fixed_links:
            logger.info("Normalized %d WeChat short link(s) (~ -> _)", fixed_links)

        tool_config = module.get_config()
        thumb_media_id = module.ensure_thumb_media_id(tool_config)

        result = upsert_draft(
            title=title,
            html=html_content,
            thumb_media_id=thumb_media_id,
            app_id=tool_config["app_id"],
            app_secret=tool_config["app_secret"],
            author=author,
            cover_crop_percent_list=module.COVER_CROP_PERCENT_LIST,
        )
        logger.info(
            "WeChat draft %s: media_id=%s title=%s",
            result.action,
            result.media_id,
            title,
        )
        return True
    except WeChatIpNotWhitelistedError as exc:
        logger.error(
            "WeChat rejected the upload: the current egress IP %s is not in the "
            "account IP whitelist. Add it under 设置与开发 -> 基本配置 -> IP白名单, "
            "then retry the upload only; the HTML is already generated.",
            exc.ip or "unknown",
        )
        return False
    except WeChatApiError as exc:
        logger.error("WeChat draft upload failed: %s", exc)
        return False
    except Exception as exc:  # pragma: no cover - defensive, publish must not break the run
        logger.error("WeChat draft upload failed unexpectedly: %s", exc)
        return False


def _load_publish_module(tool_dir: Path):
    module_path = tool_dir / "publish_news.py"
    if not module_path.exists():
        raise FileNotFoundError(f"wechat-publish-tool not found: {module_path}")

    spec = importlib.util.spec_from_file_location("wechat_publish_tool", module_path)
    if spec is None or spec.loader is None:
        raise RuntimeError("Failed to load wechat-publish-tool")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


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
    current["compliance_contact"] = config.get("compliance", {}).get("contact", "")
    path.write_text(json.dumps(current, ensure_ascii=False, indent=2), encoding="utf-8")
