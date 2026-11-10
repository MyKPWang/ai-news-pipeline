from __future__ import annotations

import hashlib
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


class PublicSourceApiError(RuntimeError):
    """The shared collector cannot provide a trustworthy cache snapshot."""


class PublicSourceApiSource(Source):
    """Refresh and read list metadata from the standalone public-source collector.

    The collector owns website access, source-level retries and de-duplication. This
    client deliberately consumes only its cached list metadata; it never fetches an
    article detail page.
    """

    name = "public_source_api"

    def __init__(
        self,
        config: dict,
        *,
        session: requests.Session | None = None,
        now: Callable[[], datetime] | None = None,
    ):
        super().__init__(config)
        api_config = config.get("public_source_api", {})
        self.base_url = str(api_config.get("base_url", "http://127.0.0.1:4100")).rstrip("/")
        self.token = str(api_config.get("token", "")).strip()
        self.timeout = int(api_config.get("request_timeout_seconds", 180))
        self.page_size = min(max(int(api_config.get("page_size", 500)), 1), 500)
        configured_slugs = api_config.get("source_slugs", [])
        self.source_slugs = [str(slug).strip() for slug in configured_slugs if str(slug).strip()]
        self.session = session or requests.Session()
        self.now = now or (lambda: datetime.now(SHANGHAI))

    def collect(self, *, refresh: bool = True) -> list[NewsItem]:
        if not self.token:
            raise PublicSourceApiError("public_source_api.token is required")

        window_start, window_end = self._report_window()
        refresh_metadata = self._refresh(window_start) if refresh else self._latest_batch()
        refresh_status = str(refresh_metadata.get("status", "")).lower()
        batch_id = str(refresh_metadata.get("id") or refresh_metadata.get("run_id") or "").strip()
        if not batch_id:
            raise PublicSourceApiError("collector refresh response is missing batch id")
        if refresh_status in {"failed", "error"}:
            raise PublicSourceApiError(f"collector refresh failed: status={refresh_status}")
        if refresh_status == "partial":
            logger.warning(
                "Public collector refresh degraded: failed_endpoints=%s succeeded_endpoints=%s",
                refresh_metadata.get("failed_endpoint_count", "unknown"),
                refresh_metadata.get("succeeded_endpoint_count", "unknown"),
            )

        items = self._read_articles(window_start, window_end, batch_id)
        logger.info(
            "Public collector read complete: batch_id=%s refresh_status=%s window=[%s, %s) items=%d",
            batch_id,
            refresh_status or "unknown",
            window_start.isoformat(),
            window_end.isoformat(),
            len(items),
        )
        return items

    def _report_window(self) -> tuple[datetime, datetime]:
        now = self.now()
        if now.tzinfo is None:
            now = now.replace(tzinfo=SHANGHAI)
        else:
            now = now.astimezone(SHANGHAI)
        today = now.replace(hour=0, minute=0, second=0, microsecond=0)
        return today - timedelta(days=1), today + timedelta(days=1)

    def _refresh(self, window_start: datetime) -> dict[str, Any]:
        scope = ",".join(sorted(self.source_slugs)) or "all"
        scope_digest = hashlib.sha1(scope.encode("utf-8")).hexdigest()[:10]
        payload: dict[str, Any] = {
            "idempotency_key": (
                f"pipeline-daily:{(window_start + timedelta(days=1)).date().isoformat()}:{scope_digest}"
            ),
        }
        if self.source_slugs:
            payload["source_slugs"] = self.source_slugs
        try:
            response = self.session.post(
                f"{self.base_url}/api/v1/pipeline/refresh",
                headers=self._headers(),
                json=payload,
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PublicSourceApiError(f"collector refresh request failed: {exc}") from exc

        refresh = data.get("refresh") if isinstance(data, dict) else None
        if not isinstance(refresh, dict):
            raise PublicSourceApiError("collector refresh response is missing refresh metadata")
        return refresh

    def _latest_batch(self) -> dict[str, Any]:
        try:
            response = self.session.get(
                f"{self.base_url}/api/v1/pipeline/batches/latest",
                headers=self._headers(),
                timeout=self.timeout,
            )
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise PublicSourceApiError(f"collector cache batch request failed: {exc}") from exc
        if not isinstance(data, dict) or not str(data.get("id") or "").strip():
            raise PublicSourceApiError("collector has no usable cache batch")
        if str(data.get("status") or "").lower() in {"failed", "running", "error"}:
            raise PublicSourceApiError(f"collector cache batch is not usable: status={data.get('status')}")
        return data

    def _read_articles(self, window_start: datetime, window_end: datetime, batch_id: str) -> list[NewsItem]:
        cursor: str | None = None
        records: list[dict[str, Any]] = []
        while True:
            params: list[tuple[str, str | int]] = [
                ("published_from", window_start.isoformat()),
                ("published_before", window_end.isoformat()),
                ("limit", self.page_size),
                ("batch_id", batch_id),
            ]
            if cursor:
                params.append(("cursor", cursor))
            for slug in self.source_slugs:
                params.append(("source_slugs", slug))
            try:
                response = self.session.get(
                    f"{self.base_url}/api/v1/pipeline/articles",
                    headers=self._headers(),
                    params=params,
                    timeout=self.timeout,
                )
                response.raise_for_status()
                data = response.json()
            except (requests.RequestException, ValueError) as exc:
                raise PublicSourceApiError(f"collector article query failed: {exc}") from exc
            if not isinstance(data, dict) or not isinstance(data.get("articles"), list):
                raise PublicSourceApiError("collector article response is missing articles")
            batch = data.get("batch")
            if not isinstance(batch, dict) or str(batch.get("id") or "") != batch_id:
                raise PublicSourceApiError("collector article response does not match the refreshed batch")

            records.extend(
                {**record, "_collector_batch_id": batch_id}
                for record in data["articles"]
                if isinstance(record, dict)
            )
            page = data.get("page") or {}
            next_cursor = page.get("next_cursor") if isinstance(page, dict) else None
            if not next_cursor:
                break
            cursor = str(next_cursor)

        return [self._to_news_item(record) for record in records]

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.token}"}

    @staticmethod
    def _to_news_item(record: dict[str, Any]) -> NewsItem:
        source_data = record.get("source") if isinstance(record.get("source"), dict) else {}
        published_at = str(record.get("published_at") or "")
        publish_time = _parse_iso_timestamp(published_at)
        item = NewsItem(
            id=str(record.get("id") or ""),
            title=str(record.get("title") or "").strip(),
            desc=str(record.get("digest") or "").strip(),
            source=str(source_data.get("name") or source_data.get("slug") or "公共资讯源"),
            source_type="public_list",
            url=str(record.get("url") or "").strip(),
            publish_time=publish_time,
            time_text=format_time_text(publish_time) if publish_time else "",
            cover_url=str(record.get("cover_url") or "").strip(),
            extra={
                "collector_article_id": record.get("id"),
                "source_article_id": record.get("source_article_id"),
                "source_slug": source_data.get("slug"),
                "published_at": published_at,
                "first_seen_at": record.get("first_seen_at"),
                "collector_batch_id": record.get("_collector_batch_id"),
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
