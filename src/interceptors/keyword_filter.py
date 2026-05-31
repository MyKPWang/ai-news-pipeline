from __future__ import annotations

from dataclasses import dataclass
import re

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


FINANCING_MAIN_EVENT_PATTERNS = [
    r"完成.{0,20}融资",
    r"获.{0,20}融资",
    r"拟融资",
    r"洽谈.{0,10}融资",
    r"冲刺.{0,10}IPO",
    r"IPO.{0,20}(招股|草案|上会|申请|计划|冲刺)",
    r"IPO.{0,20}募资",
    r"上市.{0,20}募资",
    r"募资.{0,20}(亿元|亿美元|人民币|元)",
    r"募集资金",
    r"资金将用于",
    r"估值.{0,20}(美元|亿元|人民币|升至|达)",
    r"领投",
    r"参投",
    r"投资方",
    r"债务融资",
    r"筹资.{0,20}(美元|亿元|人民币|元)",
    r"筹集.{0,20}债务",
    r"股权融资",
    r"收购",
    r"并购",
]


TECH_PROGRESS_EXCEPTION_PATTERNS = [
    r"论文.{0,20}(通过同行评审|被接收|接收)",
    r"(通过同行评审|被接收)",
    r"(发布|推出|上线|开源|接入|适配|升级).{0,30}(模型|系统|产品|功能|能力|工具|平台|芯片|机器人)",
    r"(模型|系统|产品|功能|能力|工具|平台|芯片|机器人).{0,30}(发布|推出|上线|开源|接入|适配|升级)",
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
        financing_match = _financing_main_event_match(item)
        if financing_match:
            removed.append((item, financing_match))
            continue

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


def _financing_main_event_match(item: NewsItem) -> str:
    title = item.title or ""
    desc = item.desc or ""
    content = item.content or ""
    title_match = _first_pattern_match(title, FINANCING_MAIN_EVENT_PATTERNS)
    desc_match = _first_pattern_match(f"{desc} {content}", FINANCING_MAIN_EVENT_PATTERNS)
    if not title_match and not desc_match:
        return ""
    if title_match and not desc_match and _has_tech_progress_exception(desc):
        return ""
    return f"financing_main_event:{title_match or desc_match}"


def _first_pattern_match(text: str, patterns: list[str]) -> str:
    for pattern in patterns:
        if re.search(pattern, text, flags=re.IGNORECASE):
            return pattern
    return ""


def _has_tech_progress_exception(text: str) -> bool:
    return any(re.search(pattern, text, flags=re.IGNORECASE) for pattern in TECH_PROGRESS_EXCEPTION_PATTERNS)
