#!/usr/bin/env python3
"""Re-upload an already-generated daily report to the WeChat draft box.

Why this exists
---------------
The daily job renders the report HTML first and uploads afterwards. When only
the upload step fails -- typically because the WeChat API rejects the
machine's current egress IP with `errcode 40164 invalid ip ... not in
whitelist` -- re-running the whole pipeline would repeat the expensive
MiniMax rewrite for nothing.

This script reuses the HTML the failed run already wrote to
`wechat-publish-tool/output/news_YYYYMMDD.html` and only re-attempts the
upload, so a publish-only retry costs one API call.

It is idempotent: if a draft with the same title already exists it is
updated in place (`draft/update`) instead of a second copy being created
(`draft/add`). Running it any number of times leaves exactly one draft per
day.

Usage
-----
    python scripts/macos/republish_draft.py                  # today's HTML
    python scripts/macos/republish_draft.py --date 20261111  # a specific day
    python scripts/macos/republish_draft.py --date 20261111 --dry-run

The script prints the resulting media_id on success. It never prints the
AppSecret or the access token.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import yaml

PIPELINE_HOME = Path(__file__).resolve().parents[2]
TOOL_DIR = PIPELINE_HOME / "wechat-publish-tool"
OUTPUT_DIR = TOOL_DIR / "output"
CONFIG_PATH = TOOL_DIR / "news-config.json"
SECRETS_PATH = PIPELINE_HOME / "secrets.yaml"
PIPELINE_CONFIG_PATH = PIPELINE_HOME / "config.yaml"
PUBLISH_MODULE_PATH = TOOL_DIR / "publish_news.py"

TITLE_RE = re.compile(r"移动端AI资讯速览\s*\|\s*(\d{4}-\d{2}-\d{2})")


def _load_publish_module():
    """Load publish_news.py so the cover crop and link rules stay in sync."""
    spec = importlib.util.spec_from_file_location("wechat_publish_tool", PUBLISH_MODULE_PATH)
    if spec is None or spec.loader is None:
        raise SystemExit(f"cannot load {PUBLISH_MODULE_PATH}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_config() -> dict:
    """Resolve the AppID, AppSecret and thumb media id.

    ``secrets.yaml`` holds the credentials and ``config.yaml`` the standing
    cover; both are the sources of truth. ``news-config.json`` is only a
    runtime copy the publisher writes, so it is used as a fallback for the
    thumb media id when the pipeline has not run yet.
    """
    app_id = app_secret = None
    if SECRETS_PATH.exists():
        secrets = yaml.safe_load(SECRETS_PATH.read_text(encoding="utf-8")) or {}
        publish = secrets.get("wechat_publish") or {}
        app_id = publish.get("app_id")
        app_secret = publish.get("app_secret")

    thumb_media_id = None
    if PIPELINE_CONFIG_PATH.exists():
        pipeline_config = yaml.safe_load(PIPELINE_CONFIG_PATH.read_text(encoding="utf-8")) or {}
        thumb_media_id = (pipeline_config.get("wechat_publish") or {}).get("thumb_media_id")

    if not app_id or not app_secret:
        if not CONFIG_PATH.exists():
            raise SystemExit(f"missing publish config: {SECRETS_PATH}")
        current = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
        app_id = app_id or current.get("app_id")
        app_secret = app_secret or current.get("app_secret")
        thumb_media_id = thumb_media_id or current.get("thumb_media_id")

    if not app_id or not app_secret:
        raise SystemExit(f"no app_id/app_secret in {SECRETS_PATH}")
    if not thumb_media_id:
        raise SystemExit(
            "no thumb_media_id; run the full pipeline once first "
            f"({PIPELINE_CONFIG_PATH} or {CONFIG_PATH})"
        )
    return {
        "app_id": app_id,
        "app_secret": app_secret,
        "thumb_media_id": thumb_media_id,
    }


def find_html(date: str | None) -> Path:
    if date:
        compact = date.replace("-", "")
        path = OUTPUT_DIR / f"news_{compact}.html"
        if not path.exists():
            raise SystemExit(f"no HTML for {date}: {path}")
        return path
    candidates = sorted(OUTPUT_DIR.glob("news_*.html"))
    if not candidates:
        raise SystemExit(f"no HTML found in {OUTPUT_DIR}")
    return candidates[-1]


def derive_title(html_path: Path) -> str:
    """Recover the report title from the rendered HTML."""
    html = html_path.read_text(encoding="utf-8")
    match = TITLE_RE.search(html)
    if match:
        return f"移动端AI资讯速览 | {match.group(1)}"
    # Fall back to the file date when the title is not embedded in the markup.
    stem = html_path.stem.replace("news_", "")
    try:
        parsed = datetime.strptime(stem, "%Y%m%d")
    except ValueError:
        parsed = datetime.now()
    return f"移动端AI资讯速览 | {parsed.strftime('%Y-%m-%d')}"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--date", help="YYYY-MM-DD or YYYYMMDD; default = latest HTML")
    parser.add_argument("--dry-run", action="store_true", help="do not call the API")
    args = parser.parse_args()

    html_path = find_html(args.date)
    title = derive_title(html_path)
    html = html_path.read_text(encoding="utf-8")
    module = _load_publish_module()
    html, fixed = module.normalize_wechat_links(html)
    config = load_config()
    media_id = config.get("thumb_media_id")
    if not media_id:
        raise SystemExit("config has no thumb_media_id; run the full pipeline once first")

    print(f"html   : {html_path}")
    print(f"title  : {title}")
    print(f"length : {len(html)} chars")
    print(f"links  : {fixed} wechat link(s) normalized (~ -> _)")

    if args.dry_run:
        print("dry-run: no API call made")
        return 0

    sys.path.insert(0, str(PIPELINE_HOME))
    from src.wechat_draft import (  # noqa: PLC0415
        WeChatApiError,
        WeChatIpNotWhitelistedError,
        upsert_draft,
    )

    try:
        result = upsert_draft(
            title=title,
            html=html,
            thumb_media_id=media_id,
            app_id=config["app_id"],
            app_secret=config["app_secret"],
            author="Valkyrie",
            cover_crop_percent_list=module.COVER_CROP_PERCENT_LIST,
        )
    except WeChatIpNotWhitelistedError as exc:
        print("")
        print("=" * 68)
        print(f"WeChat refused the upload: the current egress IP is not in the account whitelist.")
        print(f"  errcode : 40164")
        print(f"  ip      : {exc.ip or 'unknown'}   <- add this one to the whitelist")
        print(f"  errmsg  : {exc.errmsg}")
        print("")
        print("Next steps:")
        print("  1. In the WeChat admin console open 设置与开发 -> 基本配置 -> IP白名单")
        print(f"  2. Add {exc.ip or 'the IP above'}")
        print("  3. Re-run this same command; it updates the existing draft instead of")
        print("     creating a duplicate.")
        print("=" * 68)
        return 2
    except WeChatApiError as exc:
        print(f"WeChat API error: {exc}")
        return 3

    print(f"action   : {result.action}")
    print(f"media_id: {result.media_id}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
