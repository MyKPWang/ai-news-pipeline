from __future__ import annotations

import tempfile
import types
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader

from src.aggregator import build_publish_data, render_preview_html
from src.interceptors.dedup import exact_dedup
from src.interceptors.keyword_filter import keyword_filter
from src.llm.minimax import MiniMaxClient, parse_json_response
from src.llm.prompts import build_global_selection_prompt, build_rewrite_prompt
from src.models import NewsItem
from src.pipeline import run_pipeline, sort_by_source_priority, validate_rewritten
from src.time_utils import parse_time_value


class TimeParsingTest(unittest.TestCase):
    def test_parse_multiple_time_formats(self):
        now = datetime(2026, 5, 24, 10, 0, 0)

        self.assertEqual(1760000000, parse_time_value(1760000000000, now))
        self.assertEqual(1760000000, parse_time_value("1760000000", now))
        self.assertEqual(int(now.timestamp()), parse_time_value("刚刚", now))
        self.assertEqual(int((now - timedelta(minutes=5)).timestamp()), parse_time_value("5分钟前", now))
        self.assertEqual(int((now - timedelta(hours=3)).timestamp()), parse_time_value("3小时前", now))
        self.assertEqual(int((now - timedelta(days=2)).timestamp()), parse_time_value("2天前", now))
        self.assertEqual(
            int(datetime(2026, 5, 23, 18, 17).timestamp()),
            parse_time_value("昨天 18:17", now),
        )
        self.assertEqual(
            int(datetime(2026, 5, 22, 8, 5).timestamp()),
            parse_time_value("前天 08:05", now),
        )
        self.assertEqual(
            int(datetime(2026, 5, 24, 9, 30).timestamp()),
            parse_time_value("2026-05-24 09:30", now),
        )


class FilterAndDedupTest(unittest.TestCase):
    def test_keyword_filter_removes_plain_noise(self):
        items = [
            NewsItem(title="某公司开启招聘计划", desc="面向社会招募岗位", url="https://a"),
            NewsItem(title="苹果发布系统级 AI 能力", desc="新版本操作系统加入端侧模型", url="https://b"),
        ]

        result = keyword_filter(items)

        self.assertEqual([items[1]], result.kept)
        self.assertEqual(items[0], result.removed[0][0])

    def test_keyword_filter_positive_protection(self):
        item = NewsItem(title="小米 AI 团队招聘端侧大模型工程师", desc="系统级 AI 能力持续扩展")

        result = keyword_filter([item])

        self.assertEqual([item], result.kept)
        self.assertEqual(item, result.protected[0][0])

    def test_exact_dedup_by_title_and_url(self):
        first = NewsItem(title="同一条新闻", url="https://example.com/a")
        duplicate = NewsItem(title="同一条新闻", url="https://example.com/a")
        another = NewsItem(title="同一条新闻", url="https://example.com/b")

        result = exact_dedup([first, duplicate, another])

        self.assertEqual([first, another], result.kept)
        self.assertEqual(duplicate, result.removed[0][0])


class LlmLogicTest(unittest.TestCase):
    def test_prompts_handle_unverified_rumors(self):
        item = NewsItem(title="OpenAI 疑似发布跨时代产品", desc="该消息未经证实。")
        global_prompt = build_global_selection_prompt([item])
        rewrite_prompt = build_rewrite_prompt([item], max_content_chars=200)

        self.assertIn("未经证实", global_prompt)
        self.assertIn("默认不选", global_prompt)
        self.assertIn("unverified_rumor", rewrite_prompt)
        self.assertIn("删除不确定内容", rewrite_prompt)
        self.assertIn("不要输出 possible_copying", rewrite_prompt)

    def test_rewrite_prompt_requires_fact_precision_and_restraint(self):
        item = NewsItem(
            title="DeepSeek-V4-Pro API永久降价",
            desc="API 将于6月1日起调整为原价的四分之一。",
        )
        prompt = build_rewrite_prompt([item], max_content_chars=200)

        self.assertIn("四分之一", prompt)
        self.assertIn("绝不能写成“四折”", prompt)
        self.assertIn("未来语气", prompt)
        self.assertIn("不得替原文拔高评价", prompt)
        self.assertIn("OS27", prompt)
        self.assertIn("苹果下一代系统", prompt)
        self.assertIn("除非素材明确写的是“iOS 27”", prompt)

    def test_selection_prompt_includes_source_priority_and_duplicate_policy(self):
        wechat = NewsItem(title="系统级 AI 能力发布", desc="官方公众号发布。", source_type="wechat_mp")
        portal = NewsItem(title="系统级 AI 能力发布", desc="网页报道同一事件。", source_type="portal")
        prompt = build_global_selection_prompt([wechat, portal])

        payload = wechat.text_for_llm_selection(1)
        self.assertEqual("公众号", payload["source_type"])
        self.assertIn('"source_type": "公众号"', prompt)
        self.assertIn('"source_type": "网页"', prompt)
        self.assertIn("公众号资讯优先级高于网页资讯", prompt)
        self.assertIn("只选择 1 条", prompt)

    def test_parse_json_response_from_plain_and_markdown_text(self):
        self.assertEqual({"items": []}, parse_json_response('{"items": []}'))
        text = '```json\n{"selected": [{"index": 1}]}\n```'
        self.assertEqual({"selected": [{"index": 1}]}, parse_json_response(text))

    def test_parse_json_response_from_nested_markdown_json(self):
        text = """```json
{
  "selected": [
    {
      "index": 1,
      "category": "AI资讯",
      "core_fact": "千问开放第三方接入"
    }
  ],
  "insight": "客户端 AI 生态更新"
}
```"""
        parsed = parse_json_response(text)

        self.assertEqual("AI资讯", parsed["selected"][0]["category"])
        self.assertEqual("客户端 AI 生态更新", parsed["insight"])

    def test_parse_json_response_from_prefixed_nested_json(self):
        text = """下面是结果：
{
  "items": [
    {
      "index": 1,
      "rewritten_title": "千问开放厂商接入能力",
      "summary": "千问围绕第三方厂商接入发布指南。",
      "risk_flags": []
    }
  ]
}
请查收。"""
        parsed = parse_json_response(text)

        self.assertEqual("千问开放厂商接入能力", parsed["items"][0]["rewritten_title"])

    def test_select_items_maps_batch_index_back_to_news_item(self):
        items = [
            NewsItem(title="普通资讯", desc="不重要"),
            NewsItem(title="千问开放第三方厂商接入指南", desc="超级 App + AI 生态更新"),
            NewsItem(title="智能眼镜发布 AI 功能", desc="新硬件能力"),
        ]
        for item in items:
            item.ensure_id()

        client = MiniMaxClient.__new__(MiniMaxClient)
        client.api_config = {"selection_temperature": 0.2}

        def fake_call_json(self, **_kwargs):
            return {
                "hot_topics": ["客户端 AI"],
                "insight": "客户端 AI 生态有新进展",
                "selected": [
                    {
                        "index": 2,
                        "category": "AI资讯",
                        "priority": 1,
                        "core_fact": "千问开放第三方厂商接入指南",
                        "reason": "客户端+AI方向",
                    },
                    {
                        "index": 3,
                        "category": "智能硬件",
                        "priority": 2,
                        "core_fact": "智能眼镜发布 AI 功能",
                        "reason": "硬件 AI 能力",
                    },
                ],
            }

        client._call_json = types.MethodType(fake_call_json, client)

        selected, info = MiniMaxClient.select_items(client, items)

        self.assertEqual([items[1], items[2]], selected)
        self.assertEqual("AI资讯", items[1].category)
        self.assertEqual("智能硬件", items[2].category)
        self.assertEqual("千问开放第三方厂商接入指南", items[1].core_fact)
        self.assertEqual(["客户端 AI"], info["hot_topics"])


class RewriteAndAggregationTest(unittest.TestCase):
    def test_validate_rewritten_sends_missing_fields_and_copy_to_review(self):
        ok = NewsItem(
            title="原文标题",
            desc="原始摘要讲述苹果系统加入智能能力",
            rewritten_title="苹果系统增加智能功能",
            summary="苹果在系统应用中增加新的智能处理能力，相关功能面向用户场景展开。",
        )
        missing = NewsItem(title="原文标题", desc="摘要", rewritten_title="", summary="")
        copied = NewsItem(
            title="苹果发布系统级人工智能功能并覆盖多个系统应用",
            desc="该功能会进入多个系统应用并覆盖多个使用场景",
            rewritten_title="苹果发布系统级人工智能功能并覆盖多个系统应用",
            summary="该功能会进入多个系统应用并覆盖多个使用场景。",
        )

        publishable, review = validate_rewritten([ok, missing, copied])

        self.assertEqual([ok], publishable)
        self.assertIn(missing, review)
        self.assertIn("missing_rewritten_title", missing.risk_flags)
        self.assertIn("missing_summary", missing.risk_flags)
        self.assertIn(copied, review)
        self.assertIn("possible_copying", copied.risk_flags)

    def test_copy_check_allows_required_factual_terms(self):
        item = NewsItem(
            title="清华联合腾讯混元斩获MLSys2026MoE推理挑战赛冠军，NPU推理提速4.1倍",
            desc=(
                "清华大学存储实验室与腾讯混元AI Infra团队在MLSys2026 MoE模型推理优化挑战赛中获全球冠军。"
                "针对万亿参数混合专家架构在异构NPU上的推理瓶颈，联合团队设计了全链路优化方案。"
            ),
            rewritten_title="清华与腾讯混元团队在MLSys2026 MoE挑战赛夺冠",
            summary=(
                "清华大学存储实验室与腾讯混元AI Infra团队共同参加MLSys2026 MoE推理优化挑战赛并获全球冠军。"
                "联合团队开发了E-Shard、PSUM三维张量批量读出、GEMV路径等优化技术，评测显示NPU推理速度提升4.1倍。"
            ),
        )

        publishable, review = validate_rewritten([item])

        self.assertEqual([item], publishable)
        self.assertEqual([], review)

    def test_preview_html_does_not_fallback_to_original_title_or_desc(self):
        item = NewsItem(
            title="不要展示的原标题",
            desc="不要展示的原摘要",
            source="测试源",
            url="https://example.com",
            time_text="1小时前",
            category="AI资讯",
            rewritten_title="改写后的标题",
            summary="改写后的摘要内容。",
        )

        with tempfile.TemporaryDirectory() as tmp:
            data = build_publish_data([item], {"hot_topics": [], "insight": ""})
            html_path = render_preview_html(data, "测试文章", tmp)
            html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn("改写后的标题", html)
        self.assertIn("改写后的摘要内容。", html)
        self.assertIn(';">改写后的标题</h3>', html)
        self.assertIn(';">来源：测试源 | 1小时前</p>', html)
        self.assertNotIn("来源：测试源  |", html)
        self.assertNotIn("不要展示的原标题", html)
        self.assertNotIn("不要展示的原摘要", html)

    def test_wechat_publish_template_does_not_indent_title_or_source(self):
        item = NewsItem(
            source="测试源",
            url="https://example.com",
            time_text="1小时前",
            category="AI资讯",
            rewritten_title="改写后的标题",
            summary="改写后的摘要内容。",
        )
        data = build_publish_data([item], {"hot_topics": [], "insight": ""})
        env = Environment(
            loader=FileSystemLoader(
                str(Path(__file__).resolve().parent.parent / "wechat-publish-tool" / "templates")
            )
        )
        template = env.get_template("news.html")

        html = template.render(title="测试文章", **data, sources_str="", author="", date="2026-05-25")

        self.assertIn(';">改写后的标题</h3>', html)
        self.assertIn(';">来源：测试源 | 1小时前</p>', html)
        self.assertNotIn("来源：测试源  |", html)
        self.assertNotIn("</h3>\n\n", html)
        self.assertNotIn("</p>\n\n", html)
        self.assertNotIn("\n            改写后的标题", html)
        self.assertNotIn("\n            来源：测试源", html)


class MockPipelineTest(unittest.TestCase):
    def test_source_priority_sort_prefers_wechat_after_filtering(self):
        portal = NewsItem(title="同一事件", desc="网页摘要更短", source_type="portal", url="https://p")
        wechat = NewsItem(
            title="同一事件",
            desc="公众号摘要",
            content="公众号正文内容更完整",
            source_type="wechat_mp",
            url="https://w",
        )

        sorted_items = sort_by_source_priority([portal, wechat])

        self.assertEqual([wechat, portal], sorted_items)

    def test_end_to_end_pipeline_with_mock_sources_and_llm(self):
        now_ts = int(datetime.now().timestamp())
        source_items = [
            NewsItem(
                title="千问开放第三方厂商接入指南",
                desc="超级 App + AI 生态更新",
                source="测试源",
                source_type="portal",
                url="https://example.com/1",
                publish_time=now_ts,
            ),
            NewsItem(
                title="某公司招聘 AI 工程师",
                desc="岗位招聘信息",
                source="测试源",
                source_type="portal",
                url="https://example.com/2",
                publish_time=now_ts,
            ),
        ]
        for item in source_items:
            item.ensure_id()

        class FakeMiniMaxClient:
            def __init__(self, *_args, **_kwargs):
                pass

            def select_items(self, items):
                selected = [items[0]]
                selected[0].selected = True
                selected[0].category = "AI资讯"
                selected[0].core_fact = "千问开放第三方厂商接入指南"
                selected[0].selection_reason = "客户端+AI方向"
                return selected, {"hot_topics": ["客户端 AI"], "insight": "客户端 AI 生态更新"}

            def rewrite_items(self, items, on_item_rewritten=None):
                items[0].rewritten_title = "千问开放厂商接入能力"
                items[0].summary = "千问围绕第三方厂商接入发布指南，显示超级 App 与 AI 生态正在继续扩展。"
                items[0].risk_flags = []
                if on_item_rewritten:
                    on_item_rewritten(items[0])
                return items

        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "output": {
                    "database_path": str(Path(tmp) / "news.db"),
                    "save_raw_json": True,
                    "save_processed_json": True,
                    "min_publishable_items": 1,
                    "max_review_ratio_for_publish": 0.3,
                },
                "runtime": {"lookback_hours": 24, "default_no_publish": True},
                "bge_model": {"enabled": False},
                "wechat_publish": {"auto_publish": False},
            }

            with patch("src.pipeline.collect_all_sources", return_value=source_items), patch(
                "src.pipeline.MiniMaxClient", FakeMiniMaxClient
            ), patch("src.pipeline.render_preview_html", return_value=str(Path(tmp) / "preview.html")):
                result = run_pipeline(config, no_publish=True)

            self.assertEqual(2, len(result.raw_items))
            self.assertEqual(1, len(result.selected_items))
            self.assertEqual(1, len(result.publishable_items))
            self.assertEqual("千问开放厂商接入能力", result.publishable_items[0].rewritten_title)
            self.assertFalse(result.published)


if __name__ == "__main__":
    unittest.main()
