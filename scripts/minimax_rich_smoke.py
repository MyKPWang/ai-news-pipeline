from __future__ import annotations

import sys
from collections import Counter
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.llm.minimax import MiniMaxClient
from src.models import NewsItem
from src.pipeline import validate_rewritten
from src.storage import Storage


def build_mock_items() -> list[NewsItem]:
    now = int(datetime.now().timestamp())
    rows = [
        (
            "千问发布第三方厂商接入指南",
            "千问面向第三方厂商开放接入说明，帮助设备和应用接入 AI 能力。",
            "千问",
            "https://example.com/qwen-integration",
        ),
        (
            "苹果为新版系统加入本地 AI 写作工具",
            "苹果在系统应用中增加端侧文本处理能力，部分功能可在本地完成。",
            "Apple开发者",
            "https://example.com/apple-os-ai",
        ),
        (
            "小米公布系统级 AI 助手新功能",
            "小米展示面向手机和家居设备的系统级 AI 助手，强调跨设备任务执行。",
            "小米技术",
            "https://example.com/xiaomi-ai",
        ),
        (
            "豆包更新桌面端 AI 工作流能力",
            "豆包桌面客户端加入多步骤任务编排能力，可在文档和网页场景中调用。",
            "豆包",
            "https://example.com/doubao-desktop",
        ),
        (
            "DeepSeek 发布开发者工具调用示例",
            "DeepSeek 更新开发者文档，提供工具调用、函数参数和多轮任务示例。",
            "DeepSeek",
            "https://example.com/deepseek-tools",
        ),
        (
            "华为展示端侧大模型在系统应用中的落地",
            "华为介绍端侧模型在输入法、相册和办公应用中的处理流程。",
            "华为",
            "https://example.com/huawei-device-ai",
        ),
        (
            "AIBase 汇总新一代视频生成模型进展",
            "多家公司更新视频生成模型，重点提升一致性、动作控制和生成速度。",
            "AIBase",
            "https://example.com/video-models",
        ),
        (
            "机器之心报道多模态模型评测基准更新",
            "新的评测基准加入视觉推理、长上下文理解和工具使用任务。",
            "机器之心",
            "https://example.com/multimodal-benchmark",
        ),
        (
            "量子位关注开源语音模型新版本",
            "开源语音模型更新实时转写和多语言识别能力，降低部署门槛。",
            "量子位",
            "https://example.com/speech-model",
        ),
        (
            "虎嗅报道企业知识库 AI 搜索产品升级",
            "企业知识库产品加入混合检索和权限控制，面向办公场景提供问答能力。",
            "虎嗅",
            "https://example.com/enterprise-search",
        ),
        (
            "智能眼镜厂商发布实时翻译功能",
            "新款智能眼镜支持语音识别、翻译和字幕显示，面向出行和会议场景。",
            "AIBase",
            "https://example.com/ai-glasses",
        ),
        (
            "机器人公司推出家庭助理原型机",
            "该原型机支持语音交互、物体识别和简单家务任务演示。",
            "量子位",
            "https://example.com/home-robot",
        ),
        (
            "AI PC 厂商更新本地模型运行方案",
            "新方案优化 NPU 调度和内存占用，提升本地摘要和图片处理速度。",
            "虎嗅",
            "https://example.com/ai-pc",
        ),
        (
            "耳机新品加入离线降噪与语音摘要能力",
            "耳机通过本地芯片处理环境音，并可生成通话摘要。",
            "AIBase",
            "https://example.com/ai-earbuds",
        ),
        (
            "某 AI 公司完成新一轮融资",
            "公司宣布获得投资机构资金支持，计划扩大销售团队。",
            "门户",
            "https://example.com/funding",
        ),
        (
            "科技公司发布季度财报",
            "公司披露营收和利润数据，股价盘后上涨。",
            "门户",
            "https://example.com/earnings",
        ),
        (
            "开发者大会开启报名",
            "主办方公布会议议程和早鸟票价格。",
            "门户",
            "https://example.com/conference",
        ),
        (
            "某高校成立人工智能学院",
            "学校宣布新学院招生计划和课程方向。",
            "门户",
            "https://example.com/college",
        ),
        (
            "招聘平台发布 AI 岗位薪酬报告",
            "报告统计多个城市的算法工程师和产品经理薪资。",
            "门户",
            "https://example.com/jobs",
        ),
        (
            "AI 图像编辑 App 增加本地擦除功能",
            "移动端图像编辑应用增加本地目标擦除和背景替换能力。",
            "门户",
            "https://example.com/image-app",
        ),
    ]

    items: list[NewsItem] = []
    for title, desc, source, url in rows:
        item = NewsItem(
            title=title,
            desc=desc,
            source=source,
            source_type="mock",
            url=url,
            publish_time=now,
        )
        item.ensure_id()
        items.append(item)
    return items


def main() -> int:
    config = load_config("config.yaml", "secrets.yaml")
    config["output"]["database_path"] = "data/minimax_rich_smoke.db"
    config["output"]["llm_log_path"] = "logs/llm_rich_smoke_{date}.jsonl"
    config["minimax_api"]["max_retries"] = 1
    config["minimax_api"]["rewrite_batch_size"] = 8

    storage = Storage(config["output"]["database_path"])
    run_id = storage.start_run(datetime.now().strftime("%Y%m%d"))
    items = build_mock_items()

    try:
        client = MiniMaxClient(config, storage, run_id)
        selected, global_info = client.select_items(items)
        counts = Counter(item.category for item in selected)

        assert counts["AI资讯"] <= 10, counts
        assert counts["智能硬件"] <= 4, counts
        assert all(item in items for item in selected)
        assert all(item.category in {"AI资讯", "智能硬件"} for item in selected)

        rewritten = client.rewrite_items(selected)
        publishable, review = validate_rewritten(rewritten)

        print(f"input_count={len(items)}")
        print(f"selected_count={len(selected)}")
        print(f"category_counts={dict(counts)}")
        print(f"hot_topics={global_info.get('hot_topics', [])}")
        print(f"insight={global_info.get('insight', '')}")
        for idx, item in enumerate(selected, 1):
            print(f"selected_{idx}={item.category}|{item.title}|{item.selection_reason}")
        print(f"rewritten_count={len(rewritten)}")
        print(f"publishable_count={len(publishable)}")
        print(f"review_count={len(review)}")
        for item in review:
            print(f"review={item.title}|{item.risk_flags}")

        storage.finish_run(run_id, "success", len(items), len(selected), len(publishable))
        return 0
    except Exception as exc:
        storage.finish_run(run_id, "failed", len(items), 0, 0, str(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
