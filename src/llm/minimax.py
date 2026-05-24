from __future__ import annotations

import hashlib
import json
import logging
import re
import time
from pathlib import Path
from typing import Any

import requests

from ..logging_utils import get_llm_log_path
from ..models import NewsItem
from ..storage import Storage, utc_now_iso
from .prompts import build_global_selection_prompt, build_rewrite_prompt

logger = logging.getLogger(__name__)


class LlmError(RuntimeError):
    pass


class MiniMaxClient:
    def __init__(self, config: dict, storage: Storage, run_id: int):
        self.config = config
        self.storage = storage
        self.run_id = run_id
        self.api_config = config.get("minimax_api", {})
        self.api_key = self.api_config.get("api_key", "")
        self.model = self.api_config.get("model", "MiniMax-M2.7")
        self.base_url = str(self.api_config.get("base_url", "https://api.minimax.chat/v1")).rstrip("/")
        self.llm_log_path = get_llm_log_path(config)

    def select_items(self, items: list[NewsItem]) -> tuple[list[NewsItem], dict[str, Any]]:
        if not items:
            return [], {"hot_topics": [], "insight": "", "selected": []}
        prompt = build_global_selection_prompt(items)
        parsed = self._call_json(
            task="global_select",
            prompt=prompt,
            item_ids=[item.id for item in items],
            temperature=float(self.api_config.get("selection_temperature", 0.2)),
            max_tokens=6000,
        )
        selected_rows = parsed.get("selected", [])
        index_map = {idx: item for idx, item in enumerate(items, 1)}
        selected: list[NewsItem] = []
        counts = {"AI资讯": 0, "智能硬件": 0}
        limits = {"AI资讯": 10, "智能硬件": 4}

        for row in selected_rows:
            try:
                idx = int(row.get("index"))
            except Exception:
                continue
            item = index_map.get(idx)
            category = row.get("category")
            if not item or category not in limits:
                continue
            if counts[category] >= limits[category]:
                continue
            counts[category] += 1
            item.selected = True
            item.category = category
            item.core_fact = str(row.get("core_fact", "") or "")
            item.selection_reason = str(row.get("reason", "") or "")
            selected.append(item)

        return selected, parsed

    def rewrite_items(self, items: list[NewsItem]) -> list[NewsItem]:
        if not items:
            return []
        batch_size = int(self.api_config.get("rewrite_batch_size", 10))
        max_content_chars = int(self.api_config.get("max_content_chars", 2000))
        rewritten: list[NewsItem] = []
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            prompt = build_rewrite_prompt(batch, max_content_chars=max_content_chars)
            parsed = self._call_json(
                task="rewrite",
                prompt=prompt,
                item_ids=[item.id for item in batch],
                temperature=float(self.api_config.get("rewrite_temperature", 0.4)),
                max_tokens=9000,
            )
            rows = parsed.get("items", [])
            index_map = {idx: item for idx, item in enumerate(batch, 1)}
            for row in rows:
                try:
                    idx = int(row.get("index"))
                except Exception:
                    continue
                item = index_map.get(idx)
                if not item:
                    continue
                item.rewritten_title = str(row.get("rewritten_title", "") or "").strip()
                item.summary = str(row.get("summary", "") or "").strip()
                item.risk_flags = [str(x) for x in (row.get("risk_flags") or [])]
                facts = row.get("facts") or []
                item.extra["rewrite_facts"] = facts
                rewritten.append(item)
            if start + batch_size < len(items):
                time.sleep(1)
        return rewritten

    def _call_json(
        self,
        task: str,
        prompt: str,
        item_ids: list[str],
        temperature: float,
        max_tokens: int,
    ) -> dict[str, Any]:
        if not self.api_key:
            raise LlmError("MiniMax API key missing in secrets.yaml")

        prompt_hash = hashlib.sha256(prompt.encode("utf-8")).hexdigest()[:16]
        payload = {
            "model": self.model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }

        last_error = ""
        for attempt in range(int(self.api_config.get("max_retries", 3))):
            raw_response = ""
            try:
                raw_response = self._post(payload)
                parsed = parse_json_response(raw_response)
                self._write_llm_log(task, prompt_hash, payload, raw_response, parsed, "success")
                self.storage.add_llm_call(
                    run_id=self.run_id,
                    task=task,
                    model=self.model,
                    input_item_ids=item_ids,
                    prompt_hash=prompt_hash,
                    request_preview=prompt[:1500],
                    response_preview=raw_response[:1500],
                    response_log_path=self.llm_log_path,
                    parsed=parsed,
                    status="success",
                )
                return parsed
            except Exception as exc:
                last_error = str(exc)
                self._write_llm_log(task, prompt_hash, payload, raw_response, None, "failed", last_error)
                if attempt < int(self.api_config.get("max_retries", 3)) - 1:
                    time.sleep(2**attempt)
                else:
                    self.storage.add_llm_call(
                        run_id=self.run_id,
                        task=task,
                        model=self.model,
                        input_item_ids=item_ids,
                        prompt_hash=prompt_hash,
                        request_preview=prompt[:1500],
                        response_preview=raw_response[:1500],
                        response_log_path=self.llm_log_path,
                        parsed=None,
                        status="failed",
                        error_message=last_error,
                    )
                    raise LlmError(last_error) from exc

        raise LlmError(last_error or "unknown LLM error")

    def _post(self, payload: dict[str, Any]) -> str:
        url = f"{self.base_url}/text/chatcompletion_v2"
        resp = requests.post(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            timeout=int(self.api_config.get("timeout_seconds", 120)),
        )
        resp.raise_for_status()
        data = resp.json()
        if data.get("choices"):
            return data["choices"][0]["message"]["content"].strip()
        if data.get("reply"):
            return str(data["reply"]).strip()
        raise LlmError(f"Unexpected MiniMax response: {data}")

    def _write_llm_log(
        self,
        task: str,
        prompt_hash: str,
        payload: dict[str, Any],
        raw_response: str,
        parsed: dict[str, Any] | None,
        status: str,
        error: str = "",
    ) -> None:
        record = {
            "created_at": utc_now_iso(),
            "run_id": self.run_id,
            "task": task,
            "model": self.model,
            "prompt_hash": prompt_hash,
            "payload": redact_payload(payload),
            "raw_response": raw_response,
            "parsed": parsed,
            "status": status,
            "error": error,
        }
        path = Path(self.llm_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def parse_json_response(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        match = re.search(r"\{.*\}", text, flags=re.DOTALL)
        if not match:
            raise
        parsed = json.loads(match.group(0))
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON response must be an object")
    return parsed


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    copy = dict(payload)
    return copy
