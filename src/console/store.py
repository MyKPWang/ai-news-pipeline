from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any


def now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


@dataclass(frozen=True)
class ConsoleJob:
    id: str
    job_type: str
    mode: str
    status: str
    request: dict[str, Any]
    result: dict[str, Any]
    reason_code: str
    message: str
    pipeline_run_id: int | None
    created_at: str
    started_at: str | None
    finished_at: str | None


class ConsoleStore:
    """SQLite projection and durable task queue used by the local console."""

    def __init__(self, database_path: str | Path):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.database_path, timeout=20)
        connection.row_factory = sqlite3.Row
        try:
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def _init_schema(self) -> None:
        with self.connect() as connection:
            connection.executescript(
                """
                create table if not exists console_jobs (
                    id text primary key,
                    job_type text not null,
                    mode text not null,
                    status text not null,
                    idempotency_key text not null,
                    request_json text not null,
                    result_json text not null default '{}',
                    reason_code text not null default '',
                    message text not null default '',
                    pipeline_run_id integer,
                    created_at text not null,
                    started_at text,
                    finished_at text,
                    unique(job_type, idempotency_key)
                );
                create index if not exists idx_console_jobs_status_created
                    on console_jobs(status, created_at);
                create table if not exists console_events (
                    id integer primary key,
                    job_id text,
                    level text not null,
                    event text not null,
                    message text not null,
                    details_json text not null default '{}',
                    created_at text not null
                );
                create index if not exists idx_console_events_job_created
                    on console_events(job_id, created_at desc);
                """
            )

    def submit(self, *, job_type: str, mode: str, request: dict[str, Any], idempotency_key: str) -> tuple[ConsoleJob, bool]:
        with self.connect() as connection:
            row = connection.execute(
                "select * from console_jobs where job_type = ? and idempotency_key = ?",
                (job_type, idempotency_key),
            ).fetchone()
            if row:
                return self._job(row), True
            active = connection.execute(
                "select id from console_jobs where job_type = ? and status in ('queued', 'running', 'cancelling') limit 1",
                (job_type,),
            ).fetchone()
            if active:
                raise RuntimeError(f"{job_type}_already_running:{active['id']}")
            job_id = str(uuid.uuid4())
            created_at = now_iso()
            connection.execute(
                """
                insert into console_jobs(id, job_type, mode, status, idempotency_key, request_json, created_at)
                values(?, ?, ?, 'queued', ?, ?, ?)
                """,
                (job_id, job_type, mode, idempotency_key, json.dumps(request, ensure_ascii=False), created_at),
            )
            self._event(connection, job_id, "info", "accepted", "任务已入队", {"mode": mode})
            row = connection.execute("select * from console_jobs where id = ?", (job_id,)).fetchone()
            return self._job(row), False

    def claim_next(self) -> ConsoleJob | None:
        with self.connect() as connection:
            row = connection.execute(
                "select * from console_jobs where status = 'queued' order by created_at asc limit 1"
            ).fetchone()
            if not row:
                return None
            changed = connection.execute(
                "update console_jobs set status = 'running', started_at = ? where id = ? and status = 'queued'",
                (now_iso(), row["id"]),
            ).rowcount
            if not changed:
                return None
            self._event(connection, str(row["id"]), "info", "started", "任务开始执行", {})
            row = connection.execute("select * from console_jobs where id = ?", (row["id"],)).fetchone()
            return self._job(row)

    def finish(self, job_id: str, *, status: str, result: dict[str, Any] | None = None, reason_code: str = "", message: str = "", pipeline_run_id: int | None = None) -> None:
        with self.connect() as connection:
            connection.execute(
                """
                update console_jobs
                set status = ?, result_json = ?, reason_code = ?, message = ?, pipeline_run_id = coalesce(?, pipeline_run_id), finished_at = ?
                where id = ?
                """,
                (status, json.dumps(result or {}, ensure_ascii=False), reason_code, message[:1200], pipeline_run_id, now_iso(), job_id),
            )
            level = "error" if status == "failed" else "warning" if status == "partial" else "info"
            self._event(connection, job_id, level, status, message or "任务完成", result or {})

    def event(self, job_id: str, level: str, event: str, message: str, details: dict[str, Any] | None = None) -> None:
        with self.connect() as connection:
            self._event(connection, job_id, level, event, message, details or {})

    def _event(self, connection: sqlite3.Connection, job_id: str, level: str, event: str, message: str, details: dict[str, Any]) -> None:
        connection.execute(
            "insert into console_events(job_id, level, event, message, details_json, created_at) values(?, ?, ?, ?, ?, ?)",
            (job_id, level, event, message[:1200], json.dumps(details, ensure_ascii=False), now_iso()),
        )

    def get(self, job_id: str) -> ConsoleJob | None:
        with self.connect() as connection:
            row = connection.execute("select * from console_jobs where id = ?", (job_id,)).fetchone()
        return self._job(row) if row else None

    def list(self, limit: int = 50) -> list[ConsoleJob]:
        with self.connect() as connection:
            rows = connection.execute("select * from console_jobs order by created_at desc limit ?", (min(max(limit, 1), 200),)).fetchall()
        return [self._job(row) for row in rows]

    def events(self, limit: int = 100) -> list[dict[str, Any]]:
        with self.connect() as connection:
            rows = connection.execute(
                "select * from console_events order by created_at desc, id desc limit ?", (min(max(limit, 1), 200),)
            ).fetchall()
        return [dict(row) | {"details": self._json(row["details_json"])} for row in rows]

    def overview_runs(self, limit: int = 10) -> list[dict[str, Any]]:
        with self.connect() as connection:
            if not self._table_exists(connection, "runs"):
                return []
            rows = connection.execute(
                """
                select id, run_date, started_at, finished_at, status, total_raw, total_selected, total_published, error_message
                from runs order by started_at desc, id desc limit ?
                """,
                (min(max(limit, 1), 50),),
            ).fetchall()
        return [dict(row) for row in rows]

    def counts(self) -> dict[str, int]:
        with self.connect() as connection:
            today_raw = 0
            if self._table_exists(connection, "raw_items"):
                today_raw = connection.execute(
                    "select count(*) from raw_items where date(created_at, 'localtime') = date('now', 'localtime')"
                ).fetchone()[0]
            active_jobs = connection.execute(
                "select count(*) from console_jobs where status in ('queued', 'running', 'cancelling')"
            ).fetchone()[0]
        return {"today_raw_items": int(today_raw), "active_jobs": int(active_jobs)}

    @staticmethod
    def _table_exists(connection: sqlite3.Connection, table_name: str) -> bool:
        return connection.execute(
            "select 1 from sqlite_master where type = 'table' and name = ?", (table_name,)
        ).fetchone() is not None

    @staticmethod
    def _json(value: str | None) -> dict[str, Any]:
        try:
            parsed = json.loads(value or "{}")
            return parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            return {}

    def _job(self, row: sqlite3.Row) -> ConsoleJob:
        return ConsoleJob(
            id=str(row["id"]), job_type=str(row["job_type"]), mode=str(row["mode"]), status=str(row["status"]),
            request=self._json(row["request_json"]), result=self._json(row["result_json"]),
            reason_code=str(row["reason_code"] or ""), message=str(row["message"] or ""),
            pipeline_run_id=int(row["pipeline_run_id"]) if row["pipeline_run_id"] is not None else None,
            created_at=str(row["created_at"]), started_at=str(row["started_at"]) if row["started_at"] else None,
            finished_at=str(row["finished_at"]) if row["finished_at"] else None,
        )
