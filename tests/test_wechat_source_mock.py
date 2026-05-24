from __future__ import annotations

import json
import threading
import unittest
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from urllib.parse import parse_qs, urlparse

from src.sources.wechat import WechatApiSource


class MockWechatHandler(BaseHTTPRequestHandler):
    token_requests = 0
    mps_requests = 0
    articles_requests: list[str] = []

    def do_POST(self):
        if self.path == "/api/v1/wx/auth/token":
            MockWechatHandler.token_requests += 1
            length = int(self.headers.get("Content-Length", "0"))
            body = self.rfile.read(length).decode("utf-8")
            fields = parse_qs(body)
            if fields.get("username") != ["admin"] or fields.get("password") != ["secret"]:
                self._json({"error": "bad credentials"}, status=401)
                return
            self._json({"access_token": "mock-token"})
            return
        self._json({"error": "not found"}, status=404)

    def do_GET(self):
        parsed = urlparse(self.path)
        if parsed.path == "/api/v1/wx/mps":
            MockWechatHandler.mps_requests += 1
            if not self._authorized():
                return
            self._json(
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "list": [
                            {
                                "id": "MP_TEST_001",
                                "mp_name": "测试公众号",
                                "status": 1,
                            }
                        ]
                    },
                }
            )
            return

        if parsed.path == "/api/v1/wx/articles":
            if not self._authorized():
                return
            params = parse_qs(parsed.query)
            mp_id = params.get("mp_id", [""])[0]
            MockWechatHandler.articles_requests.append(mp_id)
            self._json(
                {
                    "code": 0,
                    "message": "success",
                    "data": {
                        "list": [
                            {
                                "id": "article-1",
                                "mp_id": mp_id,
                                "mp_name": "测试公众号",
                                "title": "苹果发布系统级 AI 能力",
                                "description": "苹果在系统应用中加入新的智能能力。",
                                "content": "<p>正文内容</p>",
                                "publish_time": 1760000000000,
                                "url": "https://mp.weixin.qq.com/s/example1",
                                "pic_url": "https://example.com/cover.jpg",
                                "has_content": 1,
                            }
                        ]
                    },
                }
            )
            return

        self._json({"error": "not found"}, status=404)

    def _authorized(self) -> bool:
        if self.headers.get("Authorization") != "Bearer mock-token":
            self._json({"error": "unauthorized"}, status=401)
            return False
        return True

    def _json(self, payload: dict, status: int = 200):
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *_args):
        return


class WechatApiSourceMockTest(unittest.TestCase):
    def setUp(self):
        MockWechatHandler.token_requests = 0
        MockWechatHandler.mps_requests = 0
        MockWechatHandler.articles_requests = []
        self.server = ThreadingHTTPServer(("127.0.0.1", 0), MockWechatHandler)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.server.server_close()
        self.thread.join(timeout=2)

    def test_collects_cached_wechat_articles(self):
        host, port = self.server.server_address
        config = {
            "docker_api": {
                "base_url": f"http://{host}:{port}",
                "username": "admin",
                "password": "secret",
                "page_size": 20,
                "request_timeout_seconds": 5,
                "fetch_article_detail": False,
            },
            "wechat_accounts": [],
        }

        items = WechatApiSource(config).collect()

        self.assertEqual(1, MockWechatHandler.token_requests)
        self.assertEqual(1, MockWechatHandler.mps_requests)
        self.assertEqual(["MP_TEST_001"], MockWechatHandler.articles_requests)
        self.assertEqual(1, len(items))

        item = items[0]
        self.assertTrue(item.id)
        self.assertEqual("苹果发布系统级 AI 能力", item.title)
        self.assertEqual("苹果在系统应用中加入新的智能能力。", item.desc)
        self.assertEqual("正文内容", item.content)
        self.assertEqual("测试公众号", item.source)
        self.assertEqual("wechat_mp", item.source_type)
        self.assertEqual("https://mp.weixin.qq.com/s/example1", item.url)
        self.assertEqual(1760000000, item.publish_time)
        self.assertEqual("https://example.com/cover.jpg", item.cover_url)
        self.assertEqual("article-1", item.extra["article_id"])


if __name__ == "__main__":
    unittest.main()
