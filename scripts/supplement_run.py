#!/usr/bin/env python3
"""手动执行补充文章任务"""
from __future__ import annotations
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.storage import Storage
from src.pipeline import handle_supplement


def main() -> int:
    config = load_config("config.yaml", "secrets.yaml")
    storage = Storage(config.get("output", {}).get("database_path", "data/news.db"))

    user_input = sys.argv[1] if len(sys.argv) > 1 else "补充 1、2、3"
    print(f"执行补充: {user_input}")

    success, msg = handle_supplement(user_input, config, storage)
    print(f"结果: {msg}")
    return 0 if success else 1


if __name__ == "__main__":
    raise SystemExit(main())