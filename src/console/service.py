from __future__ import annotations

import hashlib
import json
import logging
import threading
import time
from datetime import datetime
from typing import Any

import requests

from ..pipeline import run_pipeline
from .store import ConsoleJob, ConsoleStore

logger = logging.getLogger(__name__)


class ConsoleService:
    """Single-worker control plane for report and source collection commands."""

    def __init__(self, config: dict[str, Any], store: ConsoleStore):
        self.config = config
        self.store = store
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._work, name="news-console-worker", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread:
            self._thread.join(timeout=3)

    def submit_report(self, cache_only: bool, idempotency_key: str) -> tuple[ConsoleJob, bool]:
        return self.store.submit(
            job_type="generate_report",
            mode="cache_report" if cache_only else "refresh_and_report",
            request={"cache_only": cache_only},
            idempotency_key=idempotency_key,
        )

    def submit_collection(self, source_slugs: list[str], retry_failed: bool, idempotency_key: str) -> tuple[ConsoleJob, bool]:
        normalized = sorted({slug.strip().lower() for slug in source_slugs if slug.strip()})
        mode = "retry_failed_sources" if retry_failed else ("selected_sources" if normalized else "all_sources")
        return self.store.submit(
            job_type="collect_sources",
            mode=mode,
            request={"source_slugs": normalized, "retry_failed": retry_failed},
            idempotency_key=idempotency_key,
        )

    def collector_overview(self) -> dict[str, Any]:
        api = self.config.get("public_source_api", {})
        base_url = str(api.get("base_url", "http://127.0.0.1:4100")).rstrip("/")
        token = str(api.get("token", "")).strip()
        if not token:
            return {"status": "unconfigured", "sources": [], "latest_batch": None, "message": "Collector Token 未配置"}
        headers = {"Authorization": f"Bearer {token}"}
        try:
            accounts = requests.get(f"{base_url}/api/v1/pipeline/accounts", headers=headers, timeout=8).json().get("accounts", [])
            response = requests.get(f"{base_url}/api/v1/pipeline/batches/latest", headers=headers, timeout=8)
            latest = response.json() if response.ok else None
            return {"status": "ok", "sources": accounts if isinstance(accounts, list) else [], "latest_batch": latest, "message": ""}
        except (requests.RequestException, ValueError) as error:
            return {"status": "offline", "sources": [], "latest_batch": None, "message": str(error)}

    def _work(self) -> None:
        while not self._stop.is_set():
            job = self.store.claim_next()
            if job is None:
                self._stop.wait(1)
                continue
            try:
                if job.job_type == "generate_report":
                    self._run_report(job)
                elif job.job_type == "collect_sources":
                    self._run_collection(job)
                else:
                    self.store.finish(job.id, status="failed", reason_code="UNKNOWN_JOB_TYPE", message="未知任务类型")
            except Exception as error:
                logger.exception("console job failed: job_id=%s", job.id)
                self.store.finish(job.id, status="failed", reason_code="JOB_EXECUTION_FAILED", message=str(error))

    def _run_report(self, job: ConsoleJob) -> None:
        cache_only = bool(job.request.get("cache_only"))
        self.store.event(job.id, "info", "reading_cache" if cache_only else "refreshing_sources", "开始生成日报", {"cache_only": cache_only})
        result = run_pipeline(self.config, no_publish=True, refresh_sources=not cache_only)
        self.store.finish(
            job.id,
            status="completed",
            pipeline_run_id=result.run_id,
            message="日报已生成，等待审核",
            result={
                "pipeline_run_id": result.run_id,
                "raw_count": len(result.raw_items),
                "selected_count": len(result.selected_items),
                "publishable_count": len(result.publishable_items),
                "review_count": len(result.review_items),
                "html_path": result.html_path or "",
                "cache_only": cache_only,
            },
        )

    def _run_collection(self, job: ConsoleJob) -> None:
        requested = list(job.request.get("source_slugs") or [])
        if bool(job.request.get("retry_failed")):
            overview = self.collector_overview()
            batch = overview.get("latest_batch") if isinstance(overview.get("latest_batch"), dict) else {}
            degraded = batch.get("degraded_endpoints") if isinstance(batch, dict) else []
            requested = sorted({str(item.get("source_slug") or "").strip() for item in degraded if isinstance(item, dict) and item.get("source_slug")})
            if not requested:
                if degraded:
                    self.store.finish(
                        job.id,
                        status="failed",
                        reason_code="COLLECTOR_SOURCE_IDENTIFIERS_UNAVAILABLE",
                        message="Collector 未返回失败来源标识；重启 Collector API 后再试",
                        result={"degraded_endpoint_count": len(degraded)},
                    )
                    return
                self.store.finish(job.id, status="completed", message="没有可重试的失败来源", result={"source_slugs": []})
                return
        api = self.config.get("public_source_api", {})
        base_url = str(api.get("base_url", "http://127.0.0.1:4100")).rstrip("/")
        token = str(api.get("token", "")).strip()
        if not token:
            raise RuntimeError("PUBLIC_SOURCE_COLLECTOR_PIPELINE_TOKEN 未配置")
        scope = ",".join(requested) or "all"
        digest = hashlib.sha1(f"{job.id}:{scope}".encode("utf-8")).hexdigest()[:16]
        payload: dict[str, Any] = {"idempotency_key": f"console:{job.mode}:{digest}"}
        if requested:
            payload["source_slugs"] = requested
        self.store.event(job.id, "info", "refreshing_sources", "请求 Collector 刷新来源", {"source_slugs": requested})
        response = requests.post(f"{base_url}/api/v1/pipeline/refresh", headers={"Authorization": f"Bearer {token}"}, json=payload, timeout=300)
        if not response.ok:
            raise RuntimeError(f"Collector 刷新失败: HTTP {response.status_code}")
        data = response.json()
        refresh = data.get("refresh") if isinstance(data, dict) else {}
        status = str(refresh.get("status") or "completed").lower() if isinstance(refresh, dict) else "completed"
        self.store.finish(
            job.id,
            status="partial" if status == "partial" else "completed",
            reason_code="COLLECTOR_PARTIAL" if status == "partial" else "",
            message="来源采集完成" if status != "partial" else "来源采集部分完成，可查看失败来源后重试",
            result={"source_slugs": requested, "refresh": refresh},
        )
