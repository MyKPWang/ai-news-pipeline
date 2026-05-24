from __future__ import annotations

import copy
import os
from pathlib import Path
from typing import Any

import yaml


DEFAULT_CONFIG: dict[str, Any] = {
    "docker_api": {
        "base_url": "http://localhost:4000",
        "page_size": 20,
        "request_timeout_seconds": 20,
        "fetch_article_detail": False,
    },
    "wechat_accounts": [],
    "portal_sites": [
        {
            "name": "huxiu",
            "enabled": True,
            "list_url": "https://www.huxiu.com/ainews/",
            "max_pages": 1,
            "fetch_detail": False,
        },
        {
            "name": "qbitai",
            "enabled": True,
            "list_url": "https://www.qbitai.com/category/%E8%B5%84%E8%AE%AF",
            "max_pages": 1,
            "fetch_detail": False,
        },
        {
            "name": "aibase",
            "enabled": True,
            "list_url": "https://news.aibase.com/zh/news",
            "max_pages": 1,
            "fetch_detail": False,
        },
    ],
    "minimax_api": {
        "model": "MiniMax-M2.7",
        "base_url": "https://api.minimax.chat/v1",
        "timeout_seconds": 120,
        "max_retries": 3,
        "selection_temperature": 0.2,
        "rewrite_temperature": 0.4,
        "selection_batch_size": 40,
        "rewrite_batch_size": 10,
        "use_content_if_available": True,
        "fetch_article_detail_for_rewrite": False,
        "max_content_chars": 2000,
    },
    "bge_model": {
        "path": "",
        "name": "BAAI/bge-small-zh-v1.5",
        "threshold": 0.85,
        "enabled": True,
    },
    "runtime": {
        "lookback_hours": 24,
        "default_no_publish": False,
    },
    "wechat_publish": {
        "thumb_media_id": "",
        "auto_publish": False,
        "author": "Valkyrie",
    },
    "output": {
        "database_path": "data/news.db",
        "save_raw_json": True,
        "save_processed_json": True,
        "save_html": True,
        "app_log_path": "logs/app_{date}.log",
        "llm_log_path": "logs/llm_{date}.jsonl",
        "stop_publish_on_llm_error": True,
        "stop_publish_on_rewrite_warning": True,
        "require_rewritten_title_and_summary": True,
        "max_review_ratio_for_publish": 0.3,
        "min_publishable_items": 3,
    },
}


def load_config(config_path: str = "config.yaml", secrets_path: str = "secrets.yaml") -> dict[str, Any]:
    config = copy.deepcopy(DEFAULT_CONFIG)
    _deep_update(config, _load_yaml_if_exists(config_path))
    _deep_update(config, _load_yaml_if_exists(secrets_path))
    _apply_env_overrides(config)
    _normalize_paths(config)
    return config


def _load_yaml_if_exists(path: str) -> dict[str, Any]:
    p = Path(path)
    if not p.exists():
        return {}
    with p.open("r", encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ValueError(f"YAML root must be a mapping: {path}")
    return data


def _deep_update(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(base.get(key), dict):
            _deep_update(base[key], value)
        else:
            base[key] = value
    return base


def _apply_env_overrides(config: dict[str, Any]) -> None:
    env_map = {
        "MINIMAX_API_KEY": ("minimax_api", "api_key"),
        "MINIMAX_MODEL": ("minimax_api", "model"),
        "WECHAT_DOCKER_API_BASE_URL": ("docker_api", "base_url"),
        "WECHAT_DOCKER_API_USERNAME": ("docker_api", "username"),
        "WECHAT_DOCKER_API_PASSWORD": ("docker_api", "password"),
        "WECHAT_APP_ID": ("wechat_publish", "app_id"),
        "WECHAT_APP_SECRET": ("wechat_publish", "app_secret"),
    }
    for env_name, path in env_map.items():
        value = os.getenv(env_name)
        if value:
            section, key = path
            config.setdefault(section, {})[key] = value


def _normalize_paths(config: dict[str, Any]) -> None:
    for key in ("database_path", "app_log_path", "llm_log_path"):
        value = config.get("output", {}).get(key)
        if value:
            Path(value.format(date="19700101")).parent.mkdir(parents=True, exist_ok=True)
