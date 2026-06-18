from __future__ import annotations

import tempfile
import types
import unittest
import json
import shutil
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

from jinja2 import Environment, FileSystemLoader

from src.aggregator import build_publish_data
from src.interceptors.dedup import exact_dedup
from src.interceptors.keyword_filter import keyword_filter
from src.llm.minimax import MiniMaxClient, parse_json_response
from src.llm.prompts import build_global_selection_prompt, build_rewrite_prompt
from src.models import NewsItem
from src.pipeline import run_pipeline, sort_by_source_priority, validate_rewritten
from src.publisher import render_wechat_html
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

    def test_keyword_filter_keeps_mobile_and_edge_ai_signals(self):
        harmony = NewsItem(title="HarmonyOS 发布系统级 AI 新特性", desc="新版系统加入端侧模型能力")
        android = NewsItem(title="Android 端侧模型部署方案更新", desc="支持本地模型推理和 NPU 加速")
        honor = NewsItem(title="荣耀手机系统级 AI 功能升级", desc="客户端 AI 能力覆盖相册和语音场景")

        result = keyword_filter([harmony, android, honor])

        self.assertEqual([harmony, android, honor], result.kept)

    def test_keyword_filter_removes_hiring_and_layoff_even_when_ai_related(self):
        hiring = NewsItem(title="小米 AI 团队招聘端侧大模型工程师", desc="系统级 AI 能力持续扩展")
        layoff = NewsItem(title="某 AI 公司启动裁员", desc="涉及模型团队和客户端团队。")

        result = keyword_filter([hiring, layoff])

        self.assertEqual([], result.kept)
        self.assertEqual([hiring, layoff], [item for item, _reason in result.removed])

    def test_keyword_filter_removes_financing_main_events_even_when_ai_related(self):
        unitree = NewsItem(
            title="宇树科技科创板IPO将于6月1日上会，计划募资42亿元",
            desc="募集资金将用于机器人用人工智能模型研发、机器人本体升级和制造基地建设。",
        )
        openrouter = NewsItem(
            title="OpenRouter 完成 1.13 亿美元 B 轮融资",
            desc="该平台面向开发者提供多模型调用服务，由多家科技公司参投。",
        )
        valuation = NewsItem(
            title="AI公司拟融资10亿美元，估值升至110亿美元",
            desc="公司提供大模型推理基础设施服务，交易仍处于洽谈阶段。",
        )
        ipo = NewsItem(
            title="OpenAI冲刺9月IPO，奥特曼想快，CFO说再等等",
            desc="高盛和摩根士丹利已经在替OpenAI起草IPO招股书草案。",
        )
        debt = NewsItem(
            title="阿波罗携黑石筹资360亿美元，为Anthropic采购谷歌TPU",
            desc="该交易是面向AI芯片采购的债务融资安排。",
        )

        result = keyword_filter([unitree, openrouter, valuation, ipo, debt])

        self.assertEqual([], result.kept)
        self.assertEqual([unitree, openrouter, valuation, ipo, debt], [item for item, _reason in result.removed])

    def test_keyword_filter_keeps_technical_news_when_financing_is_title_noise(self):
        item = NewsItem(
            title="5篇AI生成数学论文被接收，00后创业者斩获巨额融资",
            desc="初创AI公司Axiom Math宣布，其AI系统提交的8篇数学论文中，5篇通过同行评审并被接收。",
        )

        result = keyword_filter([item])

        self.assertEqual([item], result.kept)
        self.assertEqual([], result.removed)

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

    def test_selection_prompt_rejects_financing_main_events(self):
        item = NewsItem(title="AI公司完成B轮融资", desc="投资方包括多家科技基金。", source_type="portal")
        prompt = build_global_selection_prompt([item])

        self.assertIn("融资、IPO、估值、股价、普通投资事件不得因涉及 AI 公司而入选", prompt)
        self.assertIn("融资不是核心事实", prompt)
        self.assertIn("明确技术、产品、论文或模型能力进展", prompt)

    def test_selection_prompt_prioritizes_mobile_edge_and_super_ai_apps(self):
        mobile = NewsItem(title="苹果发布 iOS 系统级 AI 新特性", desc="新能力覆盖端侧多模态和本地智能体。")
        edge_model = NewsItem(title="Android 端侧模型部署方案更新", desc="支持轻量模型在手机 NPU 本地运行。")
        super_app = NewsItem(title="千问 App 开放第三方智能体接入", desc="超级 AI App 扩展插件生态。")
        prompt = build_global_selection_prompt([mobile, edge_model, super_app])

        self.assertIn("移动端技术", prompt)
        self.assertIn("Apple、iOS、iPadOS、macOS、鸿蒙、HarmonyOS、安卓、Android、小米、荣耀、华为", prompt)
        self.assertIn("端侧模型", prompt)
        self.assertIn("端侧模型部署、端侧推理、本地运行、轻量模型", prompt)
        self.assertIn("端侧 AI 技术", prompt)
        self.assertIn("系统级 AI、客户端 AI、端云协同、本地智能体", prompt)
        self.assertIn("超级 AI App", prompt)
        self.assertIn("千问、豆包、DeepSeek、Kimi、MiniMax", prompt)
        self.assertIn("AI资讯：最多 15 条", prompt)
        self.assertIn("智能硬件：最多 6 条", prompt)

    def test_selection_prompt_only_keeps_policy_and_commercialization_when_core_direction_related(self):
        policy = NewsItem(title="上海发布AI产业扶持政策", desc="支持算力、模型和智能硬件生态建设。")
        commercial = NewsItem(title="AI产品开放付费API", desc="多家企业客户已接入，开发者生态合作扩大。")
        exam = NewsItem(title="高考期间AI平台禁用拍题", desc="平台发布考试期间功能限制。")
        prompt = build_global_selection_prompt([policy, commercial, exam])

        self.assertIn("政策、监管、标准、商业化、客户采用、生态合作", prompt)
        self.assertIn("只有直接关联移动端 AI、移动端技术、端侧模型、端侧 AI 技术或超级 AI App 时才可入选", prompt)
        self.assertIn("融资、IPO、估值、股价、普通投资", prompt)
        self.assertIn("上市", prompt)
        self.assertIn("招聘、裁员", prompt)
        self.assertIn("考试期间禁用拍题、平台自律/治理年报、单纯榜单得分", prompt)

    def test_select_items_uses_relaxed_category_limits(self):
        items = [NewsItem(title=f"AI资讯{i}", desc="移动端 AI 动态") for i in range(17)]
        items.extend(NewsItem(title=f"硬件{i}", desc="智能眼镜 AI 功能") for i in range(8))
        for item in items:
            item.ensure_id()

        client = MiniMaxClient.__new__(MiniMaxClient)
        client.api_config = {"selection_temperature": 0.2}

        def fake_call_json(self, **_kwargs):
            return {
                "hot_topics": [],
                "insight": "",
                "selected": [
                    {
                        "index": idx,
                        "category": "AI资讯" if idx <= 17 else "智能硬件",
                        "priority": idx,
                        "core_fact": "核心事实",
                        "reason": "相关",
                    }
                    for idx in range(1, 26)
                ],
            }

        client._call_json = types.MethodType(fake_call_json, client)

        selected, _info = MiniMaxClient.select_items(client, items)

        self.assertEqual(21, len(selected))
        self.assertEqual(15, len([item for item in selected if item.category == "AI资讯"]))
        self.assertEqual(6, len([item for item in selected if item.category == "智能硬件"]))

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

    def test_wechat_html_does_not_fallback_to_original_title_or_desc(self):
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
            tool_dir = Path(tmp) / "wechat-publish-tool"
            shutil.copytree(Path(__file__).resolve().parent.parent / "wechat-publish-tool" / "templates", tool_dir / "templates")
            shutil.copy(Path(__file__).resolve().parent.parent / "wechat-publish-tool" / "publish_news.py", tool_dir / "publish_news.py")
            data = build_publish_data([item], {"hot_topics": [], "insight": ""})
            html_path = render_wechat_html(data, "测试文章", ["测试源"], {"wechat_publish": {"author": ""}}, tool_dir=tool_dir)
            html = Path(html_path).read_text(encoding="utf-8")

        self.assertIn("wechat-publish-tool/output/news_", html_path)
        self.assertIn("改写后的标题", html)
        self.assertIn("改写后的摘要内容。", html)
        self.assertIn('"title": "不要展示的原标题"', json.dumps(item.to_publish_dict(), ensure_ascii=False))
        self.assertIn(';">改写后的标题</h3>', html)
        self.assertIn(';">来源：测试源 | 1小时前</p>', html)
        self.assertNotIn("来源：测试源  |", html)
        self.assertNotIn("不要展示的原标题", html)
        self.assertNotIn("不要展示的原摘要", html)

    def test_wechat_publish_template_uses_original_structure(self):
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
        data = build_publish_data([item], {"hot_topics": [], "insight": ""})
        env = Environment(
            loader=FileSystemLoader(
                str(Path(__file__).resolve().parent.parent / "wechat-publish-tool" / "templates")
            ),
            trim_blocks=True,
            lstrip_blocks=True,
        )
        template = env.get_template("news.html")

        html = template.render(title="测试文章", **data, sources_str="", author="", date="2026-05-25")
        template_source = (Path(__file__).resolve().parent.parent / "wechat-publish-tool" / "templates" / "news.html").read_text(
            encoding="utf-8"
        )

        self.assertIn("<h3", html)
        self.assertIn("{{ item.rewritten_title or item.title }}", template_source)
        self.assertIn(
            '<h3 style="color:#1890ff;font-weight:bold;font-size:17px;margin-top:15px;margin-bottom:5px;">改写后的标题</h3>',
            html,
        )
        self.assertIn('<p style="color:#666;font-size:15px;margin-bottom:5px;">改写后的摘要内容。</p>', html)
        self.assertIn('<p style="color:#666;font-size:13px;margin-top:0;">来源：测试源 | 1小时前</p>', html)
        self.assertIn('<p style="color:#666;font-size:13px;margin-top:0;">原文链接：<a href="https://example.com"', html)
        self.assertIn('"title": "不要展示的原标题"', json.dumps(data["categories"][0]["items"][0], ensure_ascii=False))
        self.assertIn('"desc": "不要展示的原摘要"', json.dumps(data["categories"][0]["items"][0], ensure_ascii=False))
        self.assertNotIn("来源：测试源  |", html)
        self.assertNotIn("\n            改写后的标题", html)
        self.assertNotIn("来源：测试源\n", html)


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

            with patch("src.pipeline.collect_all_sources", return_value=(source_items, [])), patch(
                "src.pipeline.MiniMaxClient", FakeMiniMaxClient
            ), patch("src.pipeline.render_wechat_html", return_value=str(Path(tmp) / "wechat.html")) as render_html:
                result = run_pipeline(config, no_publish=True)

            self.assertEqual(2, len(result.raw_items))
            self.assertEqual(1, len(result.selected_items))
            self.assertEqual(1, len(result.publishable_items))
            self.assertEqual("千问开放厂商接入能力", result.publishable_items[0].rewritten_title)
            self.assertEqual(str(Path(tmp) / "wechat.html"), result.html_path)
            render_html.assert_called_once()
            self.assertFalse(result.published)

    def test_pipeline_reuses_rewrite_cache_on_retry(self):
        now_ts = int(datetime.now().timestamp())

        def make_source_items():
            items = [
                NewsItem(
                    title="千问开放第三方厂商接入指南",
                    desc="千问面向第三方厂商开放智能体接入能力",
                    source="测试公众号",
                    source_type="wechat_mp",
                    url="https://example.com/qwen",
                    publish_time=now_ts,
                )
            ]
            for item in items:
                item.ensure_id()
            return items

        class FakeMiniMaxClient:
            rewrite_calls = 0

            def __init__(self, *_args, **_kwargs):
                pass

            def select_items(self, items):
                selected = [items[0]]
                selected[0].selected = True
                selected[0].category = "AI资讯"
                selected[0].core_fact = "千问开放第三方厂商接入指南"
                selected[0].selection_reason = "客户端+AI方向"
                return selected, {"hot_topics": [], "insight": ""}

            def rewrite_items(self, items, on_item_rewritten=None):
                type(self).rewrite_calls += 1
                for item in items:
                    item.rewritten_title = "千问开放厂商接入能力"
                    item.summary = "千问围绕第三方厂商接入发布指南，显示超级 App 与 AI 生态继续扩展。"
                    item.risk_flags = []
                    if on_item_rewritten:
                        on_item_rewritten(item)
                return items

        with tempfile.TemporaryDirectory() as tmp:
            config = {
                "output": {
                    "database_path": str(Path(tmp) / "news.db"),
                    "save_raw_json": False,
                    "save_processed_json": False,
                    "min_publishable_items": 1,
                    "max_review_ratio_for_publish": 0.3,
                },
                "runtime": {
                    "lookback_hours": 24,
                    "default_no_publish": True,
                    "reuse_rewrite_cache": True,
                },
                "bge_model": {"enabled": False},
                "wechat_publish": {"auto_publish": False},
            }

            with patch("src.pipeline.collect_all_sources", side_effect=lambda *_args: (make_source_items(), [])), patch(
                "src.pipeline.MiniMaxClient", FakeMiniMaxClient
            ), patch("src.pipeline.render_wechat_html", return_value=str(Path(tmp) / "wechat.html")):
                first = run_pipeline(config, no_publish=True)
                second = run_pipeline(config, no_publish=True)

        self.assertEqual(1, FakeMiniMaxClient.rewrite_calls)
        self.assertEqual("千问开放厂商接入能力", first.publishable_items[0].rewritten_title)
        self.assertEqual("千问开放厂商接入能力", second.publishable_items[0].rewritten_title)


if __name__ == "__main__":
    unittest.main()
