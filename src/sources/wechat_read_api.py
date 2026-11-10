from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Callable
from zoneinfo import ZoneInfo

import requests

from ..models import NewsItem
from ..time_utils import format_time_text
from .base import Source

logger = logging.getLogger(__name__)

SHANGHAI = ZoneInfo("Asia/Shanghai")


class WechatReadApiError(RuntimeError):
    """The collector cannot provide a trustworthy WeChat article snapshot."""


class WechatReadApiSource(Source):
    """Read WeChat official-account articles from the shared collector.

    The collector owns WeChat access. Articles are fetched through the WeRead
    channel (the ``/cgi-bin/appmsgpublish`` backend path is permission-blocked
    for unverified accounts) and stored in the collector's ``articles`` table.

    This client only reads the collector's cached metadata; it never fetches an
    article detail page itself.
    """

    name = "wechat_read_api"

    def __init__(
        self,
        config: dict,
        *,
        session: requests.Session | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        super().__init__(config)
        api_config = config.get("wechat_read_api", {})
        self.base_url = str(api_config.get("base_url", "http://127.0.0.1:4100")).rstrip("/")
        self.token = str(api_config.get("token", "")).strip()
        self.timeout = int(api_config.get("request_timeout_seconds", 60))
        self.page_size = min(max(int(api_config.get("page_size", 200)), 1), 500)
        self.session = session or requests.Session()
        self.now = now or (lambda: datetime.now(SHANGHAI))

    def collect(self) -> list[NewsItem]:
        if not self.token:
            raise WechatReadApiError("wechat_read_api.token is required")

        window_start, window_end = self._report_window()
        try:
            records = self._read_articles(window_start, window_end)
        except WechatReadApiError:
            raise
        except (requests.RequestException, ValueError) as exc:
            raise WechatReadApiError(f"wechat article query failed: {exc}") from exc

        logger.info(
            "WeChat collector read complete: window=[%s, %s) items=%d",
            window_start.isoformat(),
            window_end.isoformat(),
            len(records),
        )
        return [self._to_news_item(record) for record in records]

    def _report_window(self) -> tuple[datetime, datetime]:
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=SHANGHAI)
        else:
            now = now.astimezone(SHANGHAI)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return today - timedelta(days=1), today + timedelta(days=1)

    def _read_articles(self, window_start: datetime, window_end: datetime) -> list[dict[str, Any]]:
        cursor: str | None = None
        records: list[dict[str, Any]] = []
        while True:
            params: list[tuple[str, str | int]] = [
                ("published_from", window_start.isoformat()),
                ("published_before", window_end.isoformat()),
                ("limit", self.page_size),
            ]
            if cursor:
                params.append(("cursor", cursor))
            response = self.session.get(
                f"{self.base_url}/api/v1/pipeline/wechat/articles",
                headers=self._headers(),
                params=params,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
            if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
                raise WechatReadApiError("collector wechat response is missing articles")
            records.extend(record for record in data["articles"] if isinstance(record, dict))
            page = data.get("page") or {}
            next_cursor = page.get("next_cursor") if isinstance(page, dict) else None
            if not next_cursor:
                break
            cursor = str(next_cursor)
        return records

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @staticmethod
    def _to_news_item(record: dict[str, Any]) -> NewsItem:
        account = record.get("account") if isinstance(record.get("account"), dict) else {}
        published_at = str(record.get("published_at") or "")
        publish_time = _parse_iso_timestamp(published_at)
        item = NewsItem(
            id=str(record.get("id") or ""),
            title=str(record.get("title") or "").strip(),
            desc=str(record.get("digest") or "").strip(),
            source=str(account.get("name") or "微信公众号"),
            source_type="wechat",
            url=str(record.get("url") or "").strip(),
            publish_time=publish_time,
            time_text=format_time_text(publish_time) if publish_time else "",
            cover_url=str(record.get("cover_url") or "").strip(),
            extra={
                "collector_article_id": record.get("id"),
                "source_article_id": record.get("source_article_id"),
                "account_id": account.get("id"),
                "account_name": account.get("name"),
                "published_at": published_at,
                "first_seen_at": record.get("first_seen_at"),
            },
        )
        item.ensure_id()
        return item


def _parse_iso_timestamp(value: str) -> int | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=SHANGHAI)
    return int(parsed.timestamp())
