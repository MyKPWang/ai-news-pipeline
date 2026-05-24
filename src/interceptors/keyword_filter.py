from __future__ import annotations

from dataclasses import dataclass

from ..models import NewsItem


FILTER_KEYWORDS = [
    "中美",
    "外交",
    "制裁",
    "关税",
    "政策",
    "政府",
    "国会",
    "总统",
    "总理",
    "IPO",
    "上市",
    "股价",
    "市值",
    "并购",
    "收购",
    "融资",
    "债务",
    "投资",
    "股权",
    "估值",
    "贷款",
    "出售",
    "征稿",
    "报名",
    "参会",
    "展位",
    "博览会",
    "Meetup",
    "活动",
    "论坛",
    "医院",
    "药物",
    "捐赠",
    "捐款",
    "短剧",
    "奖学金",
    "AAAI",
    "议题",
    "拿地",
    "高校",
    "学院",
    "校友",
    "毕业",
    "开学",
    "招聘",
    "求职",
    "简历",
    "面试",
    "裁员",
    "就业",
    "招募",
    "年薪",
    "名创优品",
    "持股",
]


POSITIVE_PROTECTION_KEYWORDS = [
    "AI",
    "人工智能",
    "大模型",
    "模型",
    "Agent",
    "智能体",
    "端侧",
    "系统级",
    "操作系统",
    "客户端",
    "超级App",
    "小米",
    "苹果",
    "Apple",
    "华为",
    "豆包",
    "通义",
    "千问",
    "DeepSeek",
    "Kimi",
    "MiniMax",
    "NVIDIA",
    "机器人",
    "智能硬件",
    "芯片",
]


@dataclass
class FilterDecision:
    kept: list[NewsItem]
    removed: list[tuple[NewsItem, str]]
    protected: list[tuple[NewsItem, str]]


def keyword_filter(items: list[NewsItem]) -> FilterDecision:
    kept: list[NewsItem] = []
    removed: list[tuple[NewsItem, str]] = []
    protected: list[tuple[NewsItem, str]] = []

    for item in items:
        text = item.text_for_filter()
        matched = next((kw for kw in FILTER_KEYWORDS if kw.lower() in text.lower()), "")
        if not matched:
            kept.append(item)
            continue

        positive = next((kw for kw in POSITIVE_PROTECTION_KEYWORDS if kw.lower() in text.lower()), "")
        if positive:
            protected.append((item, f"{matched} protected_by {positive}"))
            kept.append(item)
        else:
            removed.append((item, matched))

    return FilterDecision(kept=kept, removed=removed, protected=protected)
