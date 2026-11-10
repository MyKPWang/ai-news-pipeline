from __future__ import annotations

import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

from src.interceptors.bge_dedup import BgeModelUnavailable, resolve_bge_model_path
from src.llm.minimax import LlmResponseError, MiniMaxClient


class BgeRuntimeResilienceTests(unittest.TestCase):
    def test_requires_an_existing_local_model_directory_by_default(self) -> None:
        with self.assertRaises(BgeModelUnavailable) as raised:
            resolve_bge_model_path({"bge_model": {"path": "data/models/missing"}})
        self.assertEqual("BGE_LOCAL_MODEL_MISSING", raised.exception.reason_code)

    def test_uses_a_configured_local_model_directory(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            self.assertEqual(
                str(Path(directory)),
                resolve_bge_model_path({"bge_model": {"path": directory}}),
            )


class MiniMaxRuntimeResilienceTests(unittest.TestCase):
    def test_empty_choice_content_has_a_stable_reason_code_and_metadata(self) -> None:
        client = MiniMaxClient.__new__(MiniMaxClient)
        client.base_url = "https://example.invalid/v1"
        client.api_key = "test-key"
        client.api_config = {"timeout_seconds": 10}
        client._last_response_metadata = {}

        class Response:
            status_code = 200
            headers = {"x-request-id": "request-1"}

            @staticmethod
            def raise_for_status() -> None:
                return None

            @staticmethod
            def json() -> dict:
                return {"choices": [{"message": {"content": ""}, "finish_reason": "stop"}]}

        with patch("src.llm.minimax.requests.post", return_value=Response()):
            with self.assertRaises(LlmResponseError) as raised:
                client._post({"model": "test"})

        self.assertEqual("EMPTY_CHOICE_CONTENT", raised.exception.reason_code)
        self.assertEqual("request-1", raised.exception.response_metadata["request_id"])
        self.assertEqual(0, raised.exception.response_metadata["content_length"])

    def test_empty_choice_content_is_retried(self) -> None:
        client = MiniMaxClient.__new__(MiniMaxClient)
        client.api_key = "test-key"
        client.api_config = {"max_retries": 2}
        client.model = "test-model"
        client.run_id = 1
        client.llm_log_path = "tests/fixtures/llm.jsonl"
        client._last_response_metadata = {}
        client.storage = types.SimpleNamespace(add_llm_call=lambda **_kwargs: None)
        calls: list[int] = []
        writes: list[dict] = []

        def post_once_empty_then_success(_self, _payload):
            calls.append(1)
            if len(calls) == 1:
                raise LlmResponseError("EMPTY_CHOICE_CONTENT", "empty", response_metadata={"content_length": 0})
            return '{"items": []}'

        def write_log(_self, *args, **kwargs):
            writes.append({"args": args, "kwargs": kwargs})

        client._post = types.MethodType(post_once_empty_then_success, client)
        client._write_llm_log = types.MethodType(write_log, client)

        with patch("src.llm.minimax.time.sleep"):
            result = client._call_json("rewrite", "prompt", ["item-1"], 0.4, 100)

        self.assertEqual({"items": []}, result)
        self.assertEqual(2, len(calls))
        self.assertEqual("EMPTY_CHOICE_CONTENT", writes[0]["kwargs"]["reason_code"])
