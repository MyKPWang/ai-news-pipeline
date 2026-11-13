"""Regression test: the WeChat Reading channel must emit the source_type the
downstream pipeline keys on.

The pipeline recognises the WeChat channel by the literal ``wechat_mp``:
``NewsItem.text_for_llm_selection`` labels it 公众号 (not 网页),
``sort_by_source_priority`` puts it first, ``_summary_limit_for_item`` gives
it the shorter 公众号 limit and ``_copy_threshold_for_item`` applies the
stricter long-copy threshold.

``WechatReadApiSource`` used to emit ``wechat``, which is none of those
literals. Every WeChat article therefore reached the LLM labelled 网页 and
lost every tie-break against its duplicate web copy -- which is why APPSO /
爱范儿 candidates were collected, survived filtering, and then never made the
final list.
"""

from __future__ import annotations

import unittest

from src.models import NewsItem
from src.pipeline import _copy_threshold_for_item, _summary_limit_for_item, sort_by_source_priority
from src.sources.wechat_read_api import WechatReadApiSource


def _wechat_record() -> dict:
    return {
        "id": "collector-article-id",
        "title": "黄仁勋，正在经历科技史上最凶险的逼宫",
        "digest": "我们正在目睹自乔布斯之后，科技史上最严重的一次逼宫。",
        "url": "https://mp.weixin.qq.com/s/abcdefgh",
        "published_at": "2026-11-13T00:01:19.000000Z",
        "account": {"id": "acct-1", "name": "APPSO"},
        "source_article_id": "MP_WXS_2392024520_abcdefgh",
    }


class WechatReadApiSourceTypeTest(unittest.TestCase):
    def test_emits_the_source_type_the_pipeline_keys_on(self) -> None:
        item = WechatReadApiSource._to_news_item(_wechat_record())
        self.assertEqual("wechat_mp", item.source_type)

    def test_prompt_labels_it_as_official_account(self) -> None:
        item = WechatReadApiSource._to_news_item(_wechat_record())
        self.assertEqual("公众号", item.text_for_llm_selection(1)["source_type"])

    def test_source_priority_puts_it_before_the_web_copy(self) -> None:
        wechat = WechatReadApiSource._to_news_item(_wechat_record())
        web = NewsItem(
            id="web-1",
            title="黄仁勋，正在经历科技史上最凶险的逼宫",
            desc="网页报道同一事件，摘要更长。",
            source="虎嗅 AI 资讯",
            source_type="public_list",
            url="https://example.com/huang",
        )
        ordered = sort_by_source_priority([web, wechat])
        self.assertEqual("wechat_mp", ordered[0].source_type)

    def test_uses_the_official_account_length_and_copy_rules(self) -> None:
        item = WechatReadApiSource._to_news_item(_wechat_record())
        self.assertEqual(90, _summary_limit_for_item(item))
        self.assertEqual(30, _copy_threshold_for_item(item, 120))


if __name__ == "__main__":
    unittest.main()
