from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from ..models import NewsItem
from ..time_utils import format_time_text, normalize_timestamp, parse_time_value
from .base import Source

logger = logging.getLogger(__name__)


class PortalSource(Source):
    name = "portals"

    def collect(self) -> list[NewsItem]:
        items: list[NewsItem] = []
        for site in self.config.get("portal_sites", []):
            if not site.get("enabled", True):
                continue
            name = site.get("name", "")
            try:
                if name == "huxiu":
                    items.extend(HuxiuPortal(site, self.config).collect())
                elif name == "qbitai":
                    items.extend(QbitaiPortal(site, self.config).collect())
                elif name == "aibase":
                    items.extend(AibasePortal(site, self.config).collect())
                else:
                    logger.warning("Unknown portal site skipped: %s", name)
            except Exception as exc:
                logger.warning("Portal source failed: %s %s", name, exc)
        return items


class BasePortal:
    def __init__(self, site_config: dict, config: dict):
        self.site_config = site_config
        self.config = config
        self.url = site_config.get("list_url", "")

    def fetch_html(self, wait_ms: int = 2500, selector: str = "") -> str:
        from playwright.sync_api import sync_playwright

        with sync_playwright() as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            context = browser.new_context(
                viewport={"width": 1920, "height": 1080},
                user_agent=(
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                extra_http_headers={"Accept-Language": "zh-CN,zh;q=0.9"},
            )
            page = context.new_page()
            page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
            if selector:
                try:
                    page.wait_for_selector(selector, timeout=15000)
                except Exception:
                    pass
            page.wait_for_timeout(wait_ms)
            html = page.content()
            browser.close()
            return html


class HuxiuPortal(BasePortal):
    def collect(self) -> list[NewsItem]:
        browser_config = self._browser_config()
        mode = browser_config.get("mode", "stealth")
        active_mode = mode

        if mode == "chrome_persistent":
            html = self._fetch_html_with_persistent_chrome(browser_config)
        elif mode == "stealth":
            html = self._fetch_html_with_stealth(browser_config)
        else:
            raise ValueError(f"Unsupported Huxiu browser mode: {mode}")

        reason = self._response_issue(html)
        if reason and browser_config.get("fallback_to_persistent_chrome", False) and mode != "chrome_persistent":
            logger.warning(
                "Huxiu %s response in %s mode; retrying with persistent Chrome",
                reason,
                mode,
            )
            html = self._fetch_html_with_persistent_chrome(browser_config)
            reason = self._response_issue(html)
            active_mode = "chrome_persistent"

        if reason:
            logger.warning("Huxiu collection returned %s in %s mode", reason, active_mode)
            return []

        items = self.parse(html)
        if not items:
            logger.warning("Huxiu collection parsed 0 items in %s mode", active_mode)
        return items

    def _browser_config(self) -> dict[str, Any]:
        portal_browser = self.config.get("portal_browser", {})
        huxiu_config = portal_browser.get("huxiu", {}) if isinstance(portal_browser, dict) else {}
        return huxiu_config if isinstance(huxiu_config, dict) else {}

    def _context_options(self, browser_config: dict[str, Any]) -> dict[str, Any]:
        return {
            "viewport": {"width": 1920, "height": 1080},
            "locale": browser_config.get("locale", "zh-CN"),
            "timezone_id": browser_config.get("timezone_id", "Asia/Shanghai"),
            "user_agent": browser_config.get(
                "user_agent",
                (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/140.0.0.0 Safari/537.36"
                ),
            ),
            "extra_http_headers": {"Accept-Language": "zh-CN,zh;q=0.9"},
        }

    def _fetch_html_with_stealth(self, browser_config: dict[str, Any]) -> str:
        from playwright.sync_api import sync_playwright
        from playwright_stealth import Stealth

        stealth = Stealth(
            navigator_languages_override=("zh-CN", "zh"),
            navigator_platform_override=browser_config.get("navigator_platform", "MacIntel"),
        )
        with stealth.use_sync(sync_playwright()) as p:
            browser = p.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled"],
            )
            try:
                context = browser.new_context(**self._context_options(browser_config))
                page = context.new_page()
                response = page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                html = page.content()
                logger.info(
                    "Huxiu browser fetch mode=stealth status=%s title=%r chars=%s",
                    response.status if response else None,
                    page.title(),
                    len(html),
                )
                return html
            finally:
                browser.close()

    def _fetch_html_with_persistent_chrome(self, browser_config: dict[str, Any]) -> str:
        from playwright.sync_api import sync_playwright

        profile_dir = Path(browser_config.get("profile_dir", "data/browser_profiles/huxiu")).expanduser()
        profile_dir.mkdir(parents=True, exist_ok=True)
        with sync_playwright() as p:
            context = p.chromium.launch_persistent_context(
                str(profile_dir),
                channel=browser_config.get("chrome_channel", "chrome"),
                headless=False,
                args=[
                    "--disable-blink-features=AutomationControlled",
                    "--no-first-run",
                    "--no-default-browser-check",
                ],
                **self._context_options(browser_config),
            )
            try:
                page = context.pages[0] if context.pages else context.new_page()
                response = page.goto(self.url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(2500)
                html = page.content()
                logger.info(
                    "Huxiu browser fetch mode=chrome_persistent status=%s title=%r chars=%s",
                    response.status if response else None,
                    page.title(),
                    len(html),
                )
                return html
            finally:
                context.close()

    @staticmethod
    def _response_issue(html: str) -> str | None:
        if not html:
            return "empty_response"
        lowered = html.lower()
        if "aliyun_waf" in lowered or "verification" in lowered or "滑动验证" in html:
            return "aliyun_waf"
        if "__NUXT_DATA__" not in html:
            return "missing_nuxt_data"
        return None

    def parse(self, html: str) -> list[NewsItem]:
        if self._response_issue(html):
            return []
        match = re.search(r'id="__NUXT_DATA__"[^>]*>([^<]+)<', html)
        if not match:
            return []
        try:
            nuxt_data = json.loads(match.group(1))
        except json.JSONDecodeError:
            return []

        ai_news_list = None
        obj_template = None
        for item in nuxt_data:
            if isinstance(item, dict) and "aiNewsList" in item:
                idx = item["aiNewsList"]
                if isinstance(idx, int) and idx < len(nuxt_data):
                    ai_news_list = nuxt_data[idx]
            if isinstance(item, dict) and "ainews_id" in item:
                obj_template = item

        if not ai_news_list:
            for item in nuxt_data:
                if isinstance(item, list) and item and isinstance(item[0], list):
                    marker = item[0]
                    if len(marker) >= 2 and marker[0] == "ShallowReactive":
                        idx = marker[1]
                        if isinstance(idx, int) and idx < len(nuxt_data):
                            candidate = nuxt_data[idx]
                            if isinstance(candidate, dict) and "ainews_id" in candidate:
                                obj_template = candidate
                                ai_news_list = item[1:]
                                break

        items: list[NewsItem] = []
        if not ai_news_list or not obj_template:
            return items

        for news_idx in ai_news_list:
            if not isinstance(news_idx, int) or news_idx >= len(nuxt_data):
                continue
            news_obj = nuxt_data[news_idx]
            if not isinstance(news_obj, dict):
                continue
            ainews_id = _nuxt_value(nuxt_data, news_obj.get("ainews_id"))
            title = _nuxt_value(nuxt_data, news_obj.get("title"))
            desc = _nuxt_value(nuxt_data, news_obj.get("desc"))
            publish_time = _nuxt_value(nuxt_data, news_obj.get("publish_time"))
            if not title or not ainews_id:
                continue
            ts = normalize_timestamp(publish_time) if publish_time else None
            item = NewsItem(
                title=str(title),
                desc=str(desc) if desc else "",
                source="虎嗅",
                source_type="portal",
                url=f"https://www.huxiu.com/ainews/{ainews_id}.html",
                publish_time=ts,
                time_text=format_time_text(ts) if ts else "",
                extra={"publish_time": publish_time},
            )
            item.ensure_id()
            items.append(item)
        return items


class QbitaiPortal(BasePortal):
    def collect(self) -> list[NewsItem]:
        html = self.fetch_html(wait_ms=2000)
        return self.parse(html)

    def parse(self, html: str) -> list[NewsItem]:
        soup = BeautifulSoup(html, "html.parser")
        items: list[NewsItem] = []
        for box in soup.select(".text_box"):
            title_elem = box.select_one("h4 a")
            if not title_elem:
                continue
            title = title_elem.get_text(strip=True)
            link = title_elem.get("href", "")
            if not title or not link:
                continue

            author_elem = box.select_one(".author a")
            author = author_elem.get_text(strip=True) if author_elem else ""
            if author != "量子位":
                continue

            desc = ""
            h4_parent = title_elem.find_parent("h4")
            if h4_parent:
                for sibling in h4_parent.find_next_siblings():
                    if sibling.name == "div" and "info" in (sibling.get("class") or []):
                        break
                    if sibling.name == "p":
                        text = sibling.get_text(strip=True)
                        if text:
                            desc = text
                            break

            time_elem = box.select_one(".time")
            time_text = time_elem.get_text(strip=True) if time_elem else ""
            ts = parse_time_value(time_text)
            item = NewsItem(
                title=title,
                desc=desc,
                source="量子位",
                source_type="portal",
                url=urljoin("https://www.qbitai.com", link),
                publish_time=ts,
                time_text=time_text or (format_time_text(ts) if ts else ""),
                extra={"author": author},
            )
            item.ensure_id()
            items.append(item)
        return items


class AibasePortal(BasePortal):
    def collect(self) -> list[NewsItem]:
        html = self.fetch_html(wait_ms=3000)
        return self.parse(html)

    def parse(self, html: str) -> list[NewsItem]:
        soup = BeautifulSoup(html, "html.parser")
        grid = soup.find("div", class_="grid")
        if not grid:
            return []
        links = grid.find_all("a", href=lambda h: h and "/zh/news/" in h)
        items: list[NewsItem] = []
        seen_titles: set[str] = set()

        for link_elem in links:
            href = link_elem.get("href", "")
            parts = [p.strip() for p in link_elem.get_text(separator="|||").split("|||")]
            if len(parts) < 2:
                continue
            title = parts[0].strip()
            if not title or title in seen_titles:
                continue
            seen_titles.add(title)
            desc = parts[1].strip()
            time_text = ""
            views = ""
            for part in parts[2:]:
                part = part.strip()
                if not part:
                    continue
                if re.search(r"(\d+)\s*(分钟|小时|天)前", part) or "刚刚" in part:
                    time_text = re.sub(r"\s+", "", part)
                elif re.search(r"([\d.]+)\s*(K|万)", part, re.IGNORECASE):
                    views = part
            ts = parse_time_value(time_text)
            item = NewsItem(
                title=title,
                desc=desc,
                source="AIBase",
                source_type="portal",
                url=urljoin("https://news.aibase.com", href),
                publish_time=ts,
                time_text=time_text or (format_time_text(ts) if ts else ""),
                extra={"views": views} if views else {},
            )
            item.ensure_id()
            items.append(item)
        return items


def _nuxt_value(data: list[Any], idx: Any) -> Any:
    if isinstance(idx, int) and idx < len(data):
        return data[idx]
    return idx
