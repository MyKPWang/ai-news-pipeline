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


class LlmResponseError(LlmError):
    def __init__(
        self,
        reason_code: str,
        message: str,
        *,
        response_metadata: dict[str, Any] | None = None,
        retryable: bool = False,
    ):
        super().__init__(message)
        self.reason_code = reason_code
        self.response_metadata = response_metadata or {}
        # retryable errors (peak-hour 529, 5xx, gateway errors) are worth
        # another endpoint or another attempt; content/param errors are not.
        self.retryable = retryable


class MiniMaxClient:
    def __init__(self, config: dict, storage: Storage, run_id: int):
        self.config = config
        self.storage = storage
        self.run_id = run_id
        self.api_config = config.get("minimax_api", {})
        self.api_key = self.api_config.get("api_key", "")
        self.model = self.api_config.get("model", "MiniMax-M2.7")
        self.base_url = str(self.api_config.get("base_url", "https://api.minimax.chat/v1")).rstrip("/")
        # MiniMax serves the same models on more than one endpoint. The primary
        # one intermittently answers HTTP 529 (overloaded_error) during peak
        # hours, so the client falls back to the next endpoint instead of
        # failing the whole run.
        self.endpoints = [
            str(item).strip()
            for item in (self.api_config.get("endpoints") or ["/text/chatcompletion_v2"])
            if str(item).strip()
        ]
        self.connect_timeout = float(self.api_config.get("connect_timeout_seconds", 30))
        self.read_timeout = float(
            self.api_config.get("read_timeout_seconds", self.api_config.get("timeout_seconds", 180))
        )
        self.llm_log_path = get_llm_log_path(config)
        self._last_response_metadata: dict[str, Any] = {}

    def select_items(self, items: list[NewsItem]) -> tuple[list[NewsItem], dict[str, Any]]:
        if not items:
            return [], {"hot_topics": [], "insight": "", "selected": []}
        prompt = build_global_selection_prompt(items)
        parsed = self._call_json(
            task="global_select",
            prompt=prompt,
            item_ids=[item.id for item in items],
            temperature=float(self.api_config.get("selection_temperature", 0.2)),
            max_tokens=int(self.api_config.get("selection_max_tokens", 5000)),
        )
        selected_rows = parsed.get("selected", [])
        index_map = {idx: item for idx, item in enumerate(items, 1)}
        selected: list[NewsItem] = []
        counts = {"AI资讯": 0, "智能硬件": 0}
        limits = {"AI资讯": 15, "智能硬件": 6}

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

    def rewrite_items(self, items: list[NewsItem], on_item_rewritten=None) -> list[NewsItem]:
        if not items:
            return []
        batch_size = int(self.api_config.get("rewrite_batch_size", 4))
        batch_size_cap = int(self.api_config.get("rewrite_batch_size_cap", 4))
        batch_size = max(1, min(batch_size, batch_size_cap))
        max_content_chars = int(self.api_config.get("max_content_chars", 2000))
        rewritten: list[NewsItem] = []
        for start in range(0, len(items), batch_size):
            batch = items[start : start + batch_size]
            batch_no = start // batch_size + 1
            total_batches = (len(items) + batch_size - 1) // batch_size
            logger.info(
                "LLM rewrite batch %s/%s start, items=%s",
                batch_no,
                total_batches,
                len(batch),
            )
            prompt = build_rewrite_prompt(batch, max_content_chars=max_content_chars)
            parsed = self._call_json(
                task="rewrite",
                prompt=prompt,
                item_ids=[item.id for item in batch],
                temperature=float(self.api_config.get("rewrite_temperature", 0.4)),
                max_tokens=int(self.api_config.get("rewrite_max_tokens", 5000)),
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
                if on_item_rewritten:
                    on_item_rewritten(item)
            logger.info(
                "LLM rewrite batch %s/%s done, parsed_items=%s",
                batch_no,
                total_batches,
                len(rows),
            )
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
            self._last_response_metadata = {}
            try:
                logger.info(
                    "LLM call start task=%s attempt=%s items=%s prompt_chars=%s max_tokens=%s",
                    task,
                    attempt + 1,
                    len(item_ids),
                    len(prompt),
                    max_tokens,
                )
                raw_response = self._post(payload)
                parsed = parse_json_response(raw_response)
                logger.info(
                    "LLM call success task=%s attempt=%s response_chars=%s",
                    task,
                    attempt + 1,
                    len(raw_response),
                )
                self._write_llm_log(task, prompt_hash, payload, raw_response, parsed, "success", response_metadata=self._last_response_metadata)
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
                reason_code = exc.reason_code if isinstance(exc, LlmResponseError) else "LLM_CALL_FAILED"
                response_metadata = exc.response_metadata if isinstance(exc, LlmResponseError) else self._last_response_metadata
                logger.warning(
                    "LLM call failed task=%s attempt=%s reason=%s error=%s",
                    task,
                    attempt + 1,
                    reason_code,
                    last_error,
                )
                self._write_llm_log(
                    task,
                    prompt_hash,
                    payload,
                    raw_response,
                    None,
                    "failed",
                    last_error,
                    response_metadata=response_metadata,
                    reason_code=reason_code,
                )
                if attempt < int(self.api_config.get("max_retries", 3)) - 1:
                    time.sleep(self._retry_backoff(attempt))
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

    def _retry_backoff(self, attempt: int) -> float:
        """Exponential backoff with a hard ceiling.

        The previous unbounded 2**attempt let a single stalled MiniMax request
        hold the daily job for hours.
        """
        base = float(self.api_config.get("retry_backoff_seconds", 5))
        cap = float(self.api_config.get("retry_backoff_max_seconds", 60))
        return min(base * (2 ** attempt), cap)

    def _connect_timeout(self) -> float:
        value = getattr(self, "connect_timeout", None)
        if value is None:
            value = float(self.api_config.get("connect_timeout_seconds", 30))
        return float(value)

    def _read_timeout(self) -> float:
        value = getattr(self, "read_timeout", None)
        if value is None:
            value = float(
                self.api_config.get("read_timeout_seconds", self.api_config.get("timeout_seconds", 180))
            )
        return float(value)

    def _endpoints(self) -> list[str]:
        """Configured endpoints, with the default when the attribute is absent.

        The client is also constructed directly in tests, so the endpoint list
        is resolved lazily instead of only in __init__.
        """
        endpoints = getattr(self, "endpoints", None)
        if not endpoints:
            endpoints = [
                str(item).strip()
                for item in (self.api_config.get("endpoints") or ["/text/chatcompletion_v2"])
                if str(item).strip()
            ]
        return list(endpoints)

    def _post(self, payload: dict[str, Any]) -> str:
        """Send one request, falling back across the configured endpoints.

        A 429/5xx/529 from one endpoint is a server-side condition, so the
        request is retried on the next endpoint with the same model. Content
        and parameter errors are raised immediately.
        """
        last_error: Exception | None = None
        for endpoint in self._endpoints():
            try:
                return self._post_to_endpoint(endpoint, payload)
            except LlmResponseError as exc:
                if not exc.retryable:
                    raise
                last_error = exc
                logger.warning(
                    "MiniMax endpoint failed endpoint=%s reason=%s error=%s",
                    endpoint,
                    exc.reason_code,
                    exc,
                )
        if last_error is not None:
            raise last_error
        raise LlmError("no MiniMax endpoint configured")

    def _post_to_endpoint(self, endpoint: str, payload: dict[str, Any]) -> str:
        url = f"{self.base_url}{endpoint}"
        resp = requests.post(
            url,
            data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {self.api_key}",
            },
            timeout=(self._connect_timeout(), self._read_timeout()),
        )
        self._last_response_metadata = {
            "http_status": resp.status_code,
            "endpoint": endpoint,
            "request_id": resp.headers.get("x-request-id") or resp.headers.get("request-id") or "",
        }
        if resp.status_code >= 500 or resp.status_code in (429, 529):
            body = getattr(resp, "text", "") or ""
            raise LlmResponseError(
                f"LLM_HTTP_{resp.status_code}",
                f"MiniMax HTTP {resp.status_code} from {endpoint}: {body[:400]}",
                response_metadata=dict(self._last_response_metadata),
                retryable=True,
            )
        resp.raise_for_status()
        data = resp.json()
        choices = data.get("choices") if isinstance(data, dict) else None
        first_choice = choices[0] if isinstance(choices, list) and choices and isinstance(choices[0], dict) else {}
        self._last_response_metadata.update(
            {
                "choice_count": len(choices) if isinstance(choices, list) else 0,
                "finish_reason": str(first_choice.get("finish_reason") or ""),
                "response_keys": sorted(str(key) for key in data.keys()) if isinstance(data, dict) else [],
            }
        )
        base_resp = data.get("base_resp") if isinstance(data, dict) else None
        if isinstance(base_resp, dict) and base_resp.get("status_code"):
            code = int(base_resp.get("status_code") or 0)
            message = str(base_resp.get("status_msg") or "")
            raise LlmResponseError(
                f"LLM_BASE_RESP_{code}",
                f"MiniMax base_resp {code}: {message}",
                response_metadata=dict(self._last_response_metadata),
                retryable=True,
            )
        if first_choice:
            message = first_choice.get("message") if isinstance(first_choice.get("message"), dict) else {}
            content = strip_reasoning(str(message.get("content") or "").strip())
            self._last_response_metadata["content_length"] = len(content)
            if content:
                return content
            raise LlmResponseError(
                "EMPTY_CHOICE_CONTENT",
                "MiniMax returned an empty choice content",
                response_metadata=dict(self._last_response_metadata),
            )
        reply = str(data.get("reply") or "").strip() if isinstance(data, dict) else ""
        reply = strip_reasoning(reply)
        self._last_response_metadata["content_length"] = len(reply)
        if reply:
            return reply
        raise LlmResponseError(
            "UNEXPECTED_RESPONSE_SHAPE",
            "MiniMax response has no usable choice content",
            response_metadata=dict(self._last_response_metadata),
        )

    def _write_llm_log(
        self,
        task: str,
        prompt_hash: str,
        payload: dict[str, Any],
        raw_response: str,
        parsed: dict[str, Any] | None,
        status: str,
        error: str = "",
        *,
        response_metadata: dict[str, Any] | None = None,
        reason_code: str = "",
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
            "reason_code": reason_code,
            "response_metadata": response_metadata or {},
        }
        path = Path(self.llm_log_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(record, ensure_ascii=False) + "\n")


def strip_reasoning(text: str) -> str:
    """Remove the <think> reasoning blocks some endpoints inline in content."""
    if not text:
        return ""
    cleaned = re.sub(r"<think>.*?</think>", "", text, flags=re.S)
    if "<think>" in cleaned:
        cleaned = cleaned.split("<think>", 1)[0]
    return cleaned.strip()


def parse_json_response(text: str) -> dict[str, Any]:
    text = (text or "").strip()
    if not text:
        raise ValueError("empty LLM response")
    fenced = re.search(r"```(?:json)?\s*(.*?)```", text, flags=re.S)
    if fenced and fenced.group(1).strip():
        text = fenced.group(1).strip()
    try:
        parsed = json.loads(text)
    except json.JSONDecodeError:
        decoder = json.JSONDecoder()
        parsed = None
        for idx, ch in enumerate(text):
            if ch != "{":
                continue
            try:
                candidate, _end = decoder.raw_decode(text[idx:])
            except json.JSONDecodeError:
                continue
            if isinstance(candidate, dict):
                parsed = candidate
                break
        if parsed is None:
            raise
    if not isinstance(parsed, dict):
        raise ValueError("LLM JSON response must be an object")
    return parsed


def redact_payload(payload: dict[str, Any]) -> dict[str, Any]:
    copy = dict(payload)
    return copy
