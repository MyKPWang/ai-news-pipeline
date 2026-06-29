from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any

import requests
from bs4 import BeautifulSoup

from ..models import NewsItem
from ..time_utils import format_time_text, normalize_timestamp
from .base import Source

logger = logging.getLogger(__name__)


class WechatApiSource(Source):
    name = "wechat"

    def collect(self) -> list[NewsItem]:
        api_config = self.config.get("docker_api", {})
        base_url = str(api_config.get("base_url", "http://localhost:4000")).rstrip("/")
        username = api_config.get("username", "")
        password = api_config.get("password", "")
        timeout = int(api_config.get("request_timeout_seconds", 20))
        page_size = int(api_config.get("page_size", 20))
        fetch_detail = bool(api_config.get("fetch_article_detail", False))

        token = self._get_token(base_url, username, password, timeout)
        mp_ids = self._get_mp_ids(base_url, token, timeout)
        configured = [x.get("mp_id") for x in self.config.get("wechat_accounts", []) if x.get("mp_id")]
        if configured:
            mp_ids = sorted(set(mp_ids) | set(configured))

        # 计算时间窗口
        lookback_hours = int(self.config.get("runtime", {}).get("lookback_hours", 24))
        now = datetime.now()
        threshold = now - timedelta(hours=lookback_hours)
        threshold_ts = int(threshold.timestamp())

        # 只查一次所有文章（按 publish_time 排序）
        # 注意：per-mp_id 查询返回的顺序是按采集时间而非发布时间，
        # 会导致最近采集的老文章被优先返回，造成 time_filter 失效。
        # 因此只使用全局查询（按 publish_time 降序）。
        all_items: list[NewsItem] = []
        try:
            all_items.extend(self._get_articles(base_url, token, None, 100, timeout, fetch_detail, threshold_ts))
        except Exception as exc:
            logger.warning("Wechat all-articles query failed: %s", exc)

        # 按 publish_time 降序排序（最新的在前）
        all_items.sort(key=lambda x: x.publish_time or 0, reverse=True)
        return all_items

    def _get_token(self, base_url: str, username: str, password: str, timeout: int) -> str:
        if not username or not password:
            raise RuntimeError("docker_api username/password missing in secrets.yaml")
        resp = requests.post(
            f"{base_url}/api/v1/wx/auth/token",
            data={"username": username, "password": password},
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        token = data.get("access_token") or data.get("data", {}).get("access_token")
        if not token:
            raise RuntimeError(f"Failed to get docker API token: {data}")
        return token

    def _headers(self, token: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {token}"}

    def _get_mp_ids(self, base_url: str, token: str, timeout: int) -> list[str]:
        resp = requests.get(
            f"{base_url}/api/v1/wx/mps",
            headers=self._headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", {}).get("list") or data.get("list") or []
        return [row.get("id") for row in rows if row.get("id")]

    def _get_articles(
        self,
        base_url: str,
        token: str,
        mp_id: str | None,
        page_size: int,
        timeout: int,
        fetch_detail: bool,
        threshold_ts: int | None = None,
    ) -> list[NewsItem]:
        params: dict[str, Any] = {"offset": 0, "limit": page_size}
        if mp_id:
            params["mp_id"] = mp_id
        resp = requests.get(
            f"{base_url}/api/v1/wx/articles",
            params=params,
            headers=self._headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        rows = data.get("data", {}).get("list") or data.get("list") or data.get("data") or []
        if isinstance(rows, dict):
            rows = rows.get("list", [])

        items: list[NewsItem] = []
        for row in rows:
            # 按 publish_time 过滤，只保留 lookback 窗口内的文章
            row_pt = row.get("publish_time")
            if row_pt:
                row_ts = normalize_timestamp(row_pt)
            else:
                row_ts = None

            if threshold_ts is not None and row_ts is not None and row_ts < threshold_ts:
                continue

            item = self._row_to_item(row)
            if fetch_detail and row.get("id") and row.get("has_content"):
                item.html_content = self._get_article_detail(base_url, token, str(row["id"]), timeout)
                item.content = html_to_text(item.html_content) or item.content
            item.ensure_id()
            items.append(item)
        return items

    def _get_article_detail(self, base_url: str, token: str, article_id: str, timeout: int) -> str:
        resp = requests.get(
            f"{base_url}/api/v1/wx/articles/{article_id}",
            headers=self._headers(token),
            timeout=timeout,
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data.get("data"), dict):
            return data["data"].get("content", "") or data["data"].get("html_content", "")
        return data.get("content", "") if isinstance(data, dict) else ""

    def _row_to_item(self, row: dict[str, Any]) -> NewsItem:
        publish_time = row.get("publish_time")
        timestamp = normalize_timestamp(publish_time) if publish_time else None
        html_content = row.get("content", "") or ""
        content = html_to_text(html_content)
        source = row.get("mp_name") or row.get("source") or row.get("author") or "微信公众号"
        item = NewsItem(
            title=row.get("title", "") or "",
            desc=row.get("description", "") or row.get("desc", "") or "",
            content=content,
            html_content=html_content,
            source=source,
            source_type="wechat_mp",
            url=row.get("url", "") or row.get("link", "") or "",
            publish_time=timestamp,
            time_text=format_time_text(timestamp) if timestamp else "",
            cover_url=row.get("pic_url", "") or row.get("cover_url", "") or "",
            extra={
                "article_id": row.get("id"),
                "mp_id": row.get("mp_id"),
                "has_content": row.get("has_content"),
            },
        )
        item.ensure_id()
        return item


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for tag in soup(["script", "style"]):
        tag.decompose()
    return soup.get_text("\n", strip=True)
