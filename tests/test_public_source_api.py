from __future__ import annotations

import json
import threading
import unittest
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse
from zoneinfo import ZoneInfo

from src.models import NewsItem
from src.pipeline import filter_recent
from src.sources.public_source_api import PublicSourceApiSource

SHANGHAI = ZoneInfo("Asia/Shanghai")


class MockCollectorHandler(BaseHTTPRequestHandler):
    refresh_payload: dict = {}
    article_queries: list[dict[str, list[str]]] = []

    def do_POST(self):
        if self.path != "/api/v1/pipeline/refresh" or not self._authorized():
            return
        length = int(self.headers.get("Content-Length", "0"))
        MockCollectorHandler.refresh_payload = json.loads(self.rfile.read(length).decode("utf-8"))
        self._json({"refresh": {"id": "batch-1", "status": "partial", "failed_endpoint_count": 1}})

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path != "/api/v1/pipeline/articles" or not self._authorized():
            return
        query = parse_qs(parsed.query)
        MockCollectorHandler.article_queries.append(query)
        if query.get("cursor") == ["next-page"]:
            self._json({"batch": {"id": "batch-1"}, "articles": [self._article("article-2", "2026-07-09T01:00:00Z")], "page": {"next_cursor": None}})
            return
        self._json({"batch": {"id": "batch-1"}, "articles": [self._article("article-1", "2026-07-08T02:00:00Z")], "page": {"next_cursor": "next-page"}})

    def _authorized(self) -> bool:
        if self.headers.get("Authorization") == "Bearer test-token":
            return True
        self._json({"error": "unauthorized"}, status=401)
        return False

    @staticmethod
    def _article(article_id: str, published_at: str) -> dict:
        return {
            "id": article_id,
            "source_article_id": f"source-{article_id}",
            "source": {"slug": "infoq-ai-briefs", "name": "InfoQ AI Briefs"},
            "title": f"AI news {article_id}",
            "digest": "A concise list-level digest.",
            "url": f"https://example.test/{article_id}",
            "cover_url": "https://example.test/cover.jpg",
            "published_at": published_at,
            "first_seen_at": "2026-07-09T00:00:00Z",
        }

    def _json(self, payload: dict, status: int = 200):
        data = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        return


class PublicSourceApiTest(unittest.TestCase):
    def setUp(self):
        MockCollectorHandler.refresh_payload = {}
        MockCollectorHandler.article_queries = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockCollectorHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_refreshes_then_reads_paginated_calendar_window(self):
        host, port = self.server.server_address
        config = {
            "public_source_api": {
                "base_url": f"http://{host}:{port}",
                "token": "test-token",
                "page_size": 100,
                "source_slugs": ["infoq-ai-briefs"],
            }
        }
        source = PublicSourceApiSource(
            config,
            now=lambda: datetime(2026, 7, 9, 8, 23, tzinfo=SHANGHAI),
        )

        items = source.collect()

        self.assertEqual(2, len(items))
        self.assertEqual("pipeline-daily:2026-07-09:b1e5cbb9a6", MockCollectorHandler.refresh_payload["idempotency_key"])
        self.assertEqual(["infoq-ai-briefs"], MockCollectorHandler.refresh_payload["source_slugs"])
        self.assertEqual(2, len(MockCollectorHandler.article_queries))
        first_query = MockCollectorHandler.article_queries[0]
        self.assertEqual(["2026-07-08T00:00:00+08:00"], first_query["published_from"])
        self.assertEqual(["2026-07-10T00:00:00+08:00"], first_query["published_before"])
        self.assertEqual(["infoq-ai-briefs"], first_query["source_slugs"])
        self.assertEqual(["batch-1"], first_query["batch_id"])
        self.assertEqual("InfoQ AI Briefs", items[0].source)
        self.assertEqual("public_list", items[0].source_type)
        self.assertEqual("source-article-1", items[0].extra["source_article_id"])
        self.assertEqual("batch-1", items[0].extra["collector_batch_id"])
        self.assertEqual(int(datetime(2026, 7, 8, 2, tzinfo=ZoneInfo("UTC")).timestamp()), items[0].publish_time)

    def test_report_window_keeps_yesterday_and_today_only(self):
        now = datetime(2026, 7, 9, 8, 23, tzinfo=SHANGHAI)
        def item(title: str, value: datetime) -> NewsItem:
            return NewsItem(title=title, desc="digest", url=f"https://example.test/{title}", publish_time=int(value.timestamp()))

        kept, review = filter_recent(
            [
                item("yesterday-start", datetime(2026, 7, 8, 0, 0, tzinfo=SHANGHAI)),
                item("today", datetime(2026, 7, 9, 23, 59, tzinfo=SHANGHAI)),
                item("too-old", datetime(2026, 7, 7, 23, 59, tzinfo=SHANGHAI)),
                item("tomorrow", datetime(2026, 7, 10, 0, 0, tzinfo=SHANGHAI)),
            ],
            {},
            now=now,
        )

        self.assertEqual(["yesterday-start", "today"], [entry.title for entry in kept])
        self.assertEqual(["too-old", "tomorrow"], [entry.title for entry in review])
        self.assertTrue(all("out_of_report_window" in entry.risk_flags for entry in review))


if __name__ == "__main__":
    unittest.main()
