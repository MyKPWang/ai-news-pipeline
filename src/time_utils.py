from __future__ import annotations

import re
from datetime import datetime, timedelta
from typing import Any


def parse_publish_time(item: Any, now: datetime | None = None) -> int | None:
    now = now or datetime.now()
    candidates: list[Any] = [
        getattr(item, "publish_time", None),
        (getattr(item, "extra", None) or {}).get("publish_time"),
        (getattr(item, "extra", None) or {}).get("collect_time"),
        getattr(item, "time_text", None),
    ]
    for candidate in candidates:
        parsed = parse_time_value(candidate, now=now)
        if parsed is not None:
            return parsed
    return None


def parse_time_value(value: Any, now: datetime | None = None) -> int | None:
    now = now or datetime.now()
    if value is None or value == "":
        return None
    if isinstance(value, (int, float)):
        return normalize_timestamp(value)
    s = str(value).strip()
    if not s:
        return None

    if re.fullmatch(r"\d{10,13}", s):
        return normalize_timestamp(int(s))

    if "刚刚" in s:
        return int(now.timestamp())

    m = re.search(r"(\d+)\s*分钟前", s)
    if m:
        return int((now - timedelta(minutes=int(m.group(1)))).timestamp())

    m = re.search(r"(\d+)\s*小时前", s)
    if m:
        return int((now - timedelta(hours=int(m.group(1)))).timestamp())

    m = re.search(r"(\d+)\s*天前", s)
    if m:
        return int((now - timedelta(days=int(m.group(1)))).timestamp())

    m = re.search(r"昨天\s*(\d{1,2}):(\d{1,2})", s)
    if m:
        dt = now.replace(
            hour=int(m.group(1)),
            minute=int(m.group(2)),
            second=0,
            microsecond=0,
        ) - timedelta(days=1)
        return int(dt.timestamp())

    m = re.search(r"前天\s*(\d{1,2}):(\d{1,2})", s)
    if m:
        dt = now.replace(
            hour=int(m.group(1)),
            minute=int(m.group(2)),
            second=0,
            microsecond=0,
        ) - timedelta(days=2)
        return int(dt.timestamp())

    for fmt in ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%d"):
        try:
            return int(datetime.strptime(s[:19], fmt).timestamp())
        except ValueError:
            continue

    return None


def normalize_timestamp(value: int | float) -> int:
    timestamp = float(value)
    if timestamp > 1e12:
        timestamp = timestamp / 1000
    return int(timestamp)


def format_time_text(timestamp: int | None, now: datetime | None = None) -> str:
    if not timestamp:
        return ""
    now = now or datetime.now()
    diff = now - datetime.fromtimestamp(timestamp)
    if diff.total_seconds() < 60:
        return "刚刚"
    if diff.total_seconds() < 3600:
        return f"{int(diff.total_seconds() // 60)}分钟前"
    if diff.total_seconds() < 86400:
        return f"{int(diff.total_seconds() // 3600)}小时前"
    if diff.days < 7:
        return f"{diff.days}天前"
    return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d")
