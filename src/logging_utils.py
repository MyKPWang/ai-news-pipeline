from __future__ import annotations

import logging
from datetime import datetime
from pathlib import Path


def setup_logging(config: dict) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    pattern = config.get("output", {}).get("app_log_path", "logs/app_{date}.log")
    log_path = Path(pattern.format(date=date_str))
    log_path.parent.mkdir(parents=True, exist_ok=True)

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)

    formatter = logging.Formatter("%(asctime)s %(levelname)s [%(name)s] %(message)s")

    file_handler = logging.FileHandler(log_path, encoding="utf-8")
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    console_handler = logging.StreamHandler()
    console_handler.setFormatter(formatter)
    root.addHandler(console_handler)

    return str(log_path)


def get_llm_log_path(config: dict) -> str:
    date_str = datetime.now().strftime("%Y%m%d")
    pattern = config.get("output", {}).get("llm_log_path", "logs/llm_{date}.jsonl")
    path = Path(pattern.format(date=date_str))
    path.parent.mkdir(parents=True, exist_ok=True)
    return str(path)
