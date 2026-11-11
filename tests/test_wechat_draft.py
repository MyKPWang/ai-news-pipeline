"""Tests for the idempotent WeChat draft client.

The network is stubbed with a fake session so the tests assert the exact
request shapes the official API requires, especially the difference between
`draft/add` (an `articles` array) and `draft/update` (a single `articles`
object plus `media_id` and `index`).
"""

from __future__ import annotations

import unittest
from unittest.mock import patch

from src.wechat_draft import (
    WeChatApiError,
    WeChatIpNotWhitelistedError,
    add_draft,
    fetch_access_token,
    find_draft_by_title,
    list_drafts,
    update_draft,
    upsert_draft,
)


class FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload


class FakeSession:
    """Records every request and replays queued responses."""

    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    def _next(self, method, url, kwargs):
        self.calls.append((method, url, kwargs))
        if not self.responses:
            raise AssertionError(f"unexpected {method} {url}")
        return FakeResponse(self.responses.pop(0))

    def get(self, url, **kwargs):
        return self._next("GET", url, kwargs)

    def post(self, url, **kwargs):
        return self._next("POST", url, kwargs)

    def urls(self):
        return [url for _, url, _ in self.calls]


class WeChatDraftTests(unittest.TestCase):
    def test_access_token_success(self):
        session = FakeSession([{"access_token": "TOKEN", "expires_in": 7200}])
        token = fetch_access_token("app", "secret", session=session)
        self.assertEqual("TOKEN", token)
        self.assertEqual("GET", session.calls[0][0])

    def test_access_token_ip_not_whitelisted_reports_the_ip(self):
        session = FakeSession(
            [
                {
                    "errcode": 40164,
                    "errmsg": "invalid ip 220.196.12.5 ipv6 ::ffff:220.196.12.5, not in whitelist rid: abc",
                }
            ]
        )
        with self.assertRaises(WeChatIpNotWhitelistedError) as ctx:
            fetch_access_token("app", "secret", session=session)
        self.assertEqual("220.196.12.5", ctx.exception.ip)
        self.assertEqual(40164, ctx.exception.errcode)

    def test_access_token_other_error_is_generic(self):
        session = FakeSession([{"errcode": 40013, "errmsg": "invalid appid"}])
        with self.assertRaises(WeChatApiError) as ctx:
            fetch_access_token("app", "secret", session=session)
        self.assertEqual(40013, ctx.exception.errcode)
        self.assertNotIsInstance(ctx.exception, WeChatIpNotWhitelistedError)

    def test_list_drafts_paginates_until_total(self):
        session = FakeSession(
            [
                {
                    "total_count": 3,
                    "item": [
                        {"media_id": "m1", "content": {"news_item": [{"title": "a"}]}},
                        {"media_id": "m2", "content": {"news_item": [{"title": "b"}]}},
                    ],
                },
                {
                    "total_count": 3,
                    "item": [{"media_id": "m3", "content": {"news_item": [{"title": "c"}]}}],
                },
            ]
        )
        drafts = list_drafts("TOKEN", session=session)
        self.assertEqual(3, len(drafts))
        self.assertEqual(2, len(session.calls))
        self.assertEqual(0, session.calls[0][2]["json"]["offset"])
        self.assertEqual(2, session.calls[1][2]["json"]["offset"])

    def test_find_draft_by_title_matches_and_reports_index(self):
        session = FakeSession(
            [
                {
                    "total_count": 1,
                    "item": [
                        {
                            "media_id": "m1",
                            "content": {
                                "news_item": [
                                    {"title": "other"},
                                    {"title": "移动端AI资讯速览 | 2026-11-11"},
                                ]
                            },
                        }
                    ],
                }
            ]
        )
        found = find_draft_by_title("TOKEN", "移动端AI资讯速览 | 2026-11-11", session=session)
        self.assertIsNotNone(found)
        self.assertEqual("m1", found["media_id"])
        self.assertEqual(1, found["index"])

    def test_find_draft_by_title_returns_none_when_absent(self):
        session = FakeSession(
            [{"total_count": 1, "item": [{"media_id": "m1", "content": {"news_item": [{"title": "x"}]}}]}]
        )
        self.assertIsNone(find_draft_by_title("TOKEN", "wanted", session=session))

    def test_add_draft_sends_an_articles_array(self):
        session = FakeSession([{"media_id": "NEW"}])
        media_id = add_draft("TOKEN", {"title": "t", "content": "<p>x</p>"}, session=session)
        self.assertEqual("NEW", media_id)
        url, kwargs = session.calls[0][1], session.calls[0][2]
        self.assertIn("/draft/add", url)
        self.assertIsInstance(kwargs["json"]["articles"], list)
        self.assertEqual(1, len(kwargs["json"]["articles"]))

    def test_update_draft_sends_a_single_article_object(self):
        session = FakeSession([{"errcode": 0, "errmsg": "ok"}])
        media_id = update_draft("TOKEN", "m1", {"title": "t"}, index=0, session=session)
        self.assertEqual("m1", media_id)
        url, kwargs = session.calls[0][1], session.calls[0][2]
        self.assertIn("/draft/update", url)
        body = kwargs["json"]
        self.assertEqual("m1", body["media_id"])
        self.assertEqual(0, body["index"])
        self.assertIsInstance(body["articles"], dict)

    def test_upsert_creates_when_title_is_new(self):
        session = FakeSession(
            [
                {"access_token": "TOKEN"},
                {"total_count": 1, "item": [{"media_id": "m1", "content": {"news_item": [{"title": "old"}]}}]},
                {"media_id": "NEW"},
            ]
        )
        result = upsert_draft(
            title="new title",
            html="<p>body</p>",
            thumb_media_id="THUMB",
            app_id="app",
            app_secret="secret",
            session=session,
        )
        self.assertEqual("created", result.action)
        self.assertEqual("NEW", result.media_id)
        self.assertNotIn("/draft/update", " ".join(session.urls()))

    def test_upsert_updates_instead_of_duplicating(self):
        session = FakeSession(
            [
                {"access_token": "TOKEN"},
                {
                    "total_count": 1,
                    "item": [
                        {
                            "media_id": "m1",
                            "content": {"news_item": [{"title": "same title"}]},
                        }
                    ],
                },
                {"errcode": 0, "errmsg": "ok"},
            ]
        )
        result = upsert_draft(
            title="same title",
            html="<p>body</p>",
            thumb_media_id="THUMB",
            app_id="app",
            app_secret="secret",
            session=session,
        )
        self.assertEqual("updated", result.action)
        self.assertEqual("m1", result.media_id)
        self.assertIn("/draft/update", " ".join(session.urls()))
        self.assertNotIn("/draft/add", " ".join(session.urls()))

    def test_upsert_is_idempotent_across_repeated_runs(self):
        """A second upload of the same title updates; it never adds again."""
        # Each run performs: token -> batchget -> draft/update.
        session = FakeSession(
            [
                {"access_token": "TOKEN"},
                {
                    "total_count": 1,
                    "item": [{"media_id": "m1", "content": {"news_item": [{"title": "t"}]}}],
                },
                {"errcode": 0, "errmsg": "ok"},
                {"access_token": "TOKEN"},
                {
                    "total_count": 1,
                    "item": [{"media_id": "m1", "content": {"news_item": [{"title": "t"}]}}],
                },
                {"errcode": 0, "errmsg": "ok"},
            ]
        )
        for _ in range(2):
            result = upsert_draft(
                title="t",
                html="<p>body</p>",
                thumb_media_id="THUMB",
                app_id="app",
                app_secret="secret",
                session=session,
            )
            self.assertEqual("updated", result.action)
        self.assertNotIn("/draft/add", " ".join(session.urls()))

    def test_upsert_sends_cover_crop_and_thumb(self):
        session = FakeSession(
            [
                {"access_token": "TOKEN"},
                {"total_count": 0, "item": []},
                {"media_id": "NEW"},
            ]
        )
        crop = [{"ratio": "2.35_1", "x1": "0", "y1": "0", "x2": "1", "y2": "1"}]
        upsert_draft(
            title="t",
            html="<p>body</p>",
            thumb_media_id="THUMB",
            app_id="app",
            app_secret="secret",
            cover_crop_percent_list=crop,
            session=session,
        )
        article = session.calls[-1][2]["json"]["articles"][0]
        self.assertEqual("THUMB", article["thumb_media_id"])
        self.assertEqual(crop, article["cover_info"]["crop_percent_list"])


class PublisherResilienceTests(unittest.TestCase):
    def test_publish_failure_does_not_raise(self):
        from src import publisher

        with patch.object(publisher, "_load_publish_module") as load:
            module = load.return_value
            module.generate_html.side_effect = RuntimeError("boom")
            with self.assertLogs("src.publisher", level="ERROR"):
                result = publisher.publish_to_wechat_tool({}, "title", [], {})
        self.assertFalse(result)

    def test_whitelist_error_logs_the_ip_to_whitelist(self):
        """The operator must be told which IP to whitelist, and nothing else."""
        from src import publisher

        with patch.object(publisher, "_load_publish_module") as load:
            module = load.return_value
            module.generate_html.return_value = "<p>x</p>"
            module.save_html.return_value = "saved"
            module.normalize_wechat_links.return_value = ("<p>x</p>", 0)
            module.get_config.return_value = {"app_id": "a", "app_secret": "b"}
            module.ensure_thumb_media_id.return_value = "THUMB"
            with patch.object(
                publisher, "upsert_draft", side_effect=WeChatIpNotWhitelistedError(
                    "220.196.12.5", "invalid ip 220.196.12.5, not in whitelist"
                )
            ):
                with self.assertLogs("src.publisher", level="ERROR") as captured:
                    result = publisher.publish_to_wechat_tool(
                        {},
                        "title",
                        [],
                        {"wechat_publish": {"app_id": "a", "app_secret": "b"}},
                    )
        self.assertFalse(result)
        message = "\n".join(captured.output)
        self.assertIn("220.196.12.5", message)
        self.assertIn("IP白名单", message)


if __name__ == "__main__":
    unittest.main()
