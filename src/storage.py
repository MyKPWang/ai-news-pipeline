from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

logger = logging.getLogger(__name__)

from .models import NewsItem


def utc_now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


class Storage:
    def __init__(self, database_path: str):
        self.database_path = Path(database_path)
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self.init_schema()

    @contextmanager
    def connect(self):
        conn = sqlite3.connect(self.database_path)
        conn.row_factory = sqlite3.Row
        try:
            yield conn
            conn.commit()
        except Exception:
            conn.rollback()
            raise
        finally:
            conn.close()

    def init_schema(self) -> None:
        with self.connect() as conn:
            conn.executescript(
                """
                create table if not exists runs (
                    id integer primary key,
                    run_date text not null,
                    started_at text not null,
                    finished_at text,
                    status text not null,
                    total_raw integer default 0,
                    total_selected integer default 0,
                    total_published integer default 0,
                    error_message text
                );

                create table if not exists raw_items (
                    id integer primary key,
                    item_id text not null,
                    run_id integer not null,
                    source text,
                    source_type text,
                    title text,
                    desc text,
                    content text,
                    html_content text,
                    url text,
                    publish_time integer,
                    time_text text,
                    cover_url text,
                    extra_json text,
                    created_at text not null,
                    unique(run_id, item_id)
                );

                create table if not exists processed_items (
                    id integer primary key,
                    run_id integer not null,
                    raw_item_id integer,
                    item_id text not null,
                    stage text not null,
                    category text,
                    selected integer default 0,
                    selection_reason text,
                    core_fact text,
                    rewritten_title text,
                    summary text,
                    risk_flags_json text,
                    updated_at text not null,
                    unique(run_id, item_id)
                );

                create table if not exists filter_events (
                    id integer primary key,
                    run_id integer not null,
                    raw_item_id integer,
                    item_id text,
                    stage text not null,
                    action text not null,
                    reason_code text,
                    reason_detail text,
                    created_at text not null
                );

                create table if not exists llm_calls (
                    id integer primary key,
                    run_id integer not null,
                    task text not null,
                    model text,
                    input_item_ids_json text,
                    prompt_hash text,
                    request_preview text,
                    response_preview text,
                    response_log_path text,
                    parsed_json text,
                    status text not null,
                    error_message text,
                    created_at text not null
                );

                create table if not exists app_logs (
                    id integer primary key,
                    run_id integer,
                    level text not null,
                    module text,
                    event text,
                    message text,
                    item_id text,
                    log_path text,
                    created_at text not null
                );

                create index if not exists idx_raw_items_run_id on raw_items(run_id);
                create index if not exists idx_raw_items_item_id on raw_items(item_id);
                create index if not exists idx_processed_items_run_id on processed_items(run_id);
                create index if not exists idx_filter_events_run_id on filter_events(run_id);
                create index if not exists idx_llm_calls_run_id on llm_calls(run_id);
                create index if not exists idx_app_logs_run_id on app_logs(run_id);
                """
            )

    def start_run(self, run_date: str) -> int:
        with self.connect() as conn:
            cur = conn.execute(
                "insert into runs(run_date, started_at, status) values(?, ?, ?)",
                (run_date, utc_now_iso(), "running"),
            )
            return int(cur.lastrowid)

    def finish_run(
        self,
        run_id: int,
        status: str,
        total_raw: int = 0,
        total_selected: int = 0,
        total_published: int = 0,
        error_message: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                update runs
                set finished_at = ?, status = ?, total_raw = ?, total_selected = ?,
                    total_published = ?, error_message = ?
                where id = ?
                """,
                (
                    utc_now_iso(),
                    status,
                    total_raw,
                    total_selected,
                    total_published,
                    error_message,
                    run_id,
                ),
            )

    def insert_raw_items(self, run_id: int, items: Iterable[NewsItem]) -> dict[str, int]:
        mapping: dict[str, int] = {}
        with self.connect() as conn:
            for item in items:
                item.ensure_id()
                try:
                    conn.execute(
                        """
                        insert into raw_items(
                            item_id, run_id, source, source_type, title, desc, content,
                            html_content, url, publish_time, time_text, cover_url,
                            extra_json, created_at
                        )
                        values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                        """,
                        (
                            item.id,
                            run_id,
                            item.source,
                            item.source_type,
                            item.title,
                            item.desc,
                            item.content,
                            item.html_content,
                            item.url,
                            item.publish_time,
                            item.time_text,
                            item.cover_url,
                            json.dumps(item.extra or {}, ensure_ascii=False),
                            utc_now_iso(),
                        ),
                    )
                except Exception:
                    logger.warning(
                        "insert_raw_items: item_id=%s run_id=%d title=%s skipped (constraint): %s",
                        item.id, run_id, (item.title or "")[:30], item.url[:50] if item.url else "",
                    )
            rows = conn.execute(
                "select id, item_id from raw_items where run_id = ?", (run_id,)
            ).fetchall()
            mapping = {str(row["item_id"]): int(row["id"]) for row in rows}
        return mapping

    def upsert_processed(
        self,
        run_id: int,
        item: NewsItem,
        stage: str,
        raw_item_id: int | None = None,
    ) -> None:
        item.ensure_id()
        with self.connect() as conn:
            conn.execute(
                """
                insert into processed_items(
                    run_id, raw_item_id, item_id, stage, category, selected,
                    selection_reason, core_fact, rewritten_title, summary,
                    risk_flags_json, updated_at
                )
                values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                on conflict(run_id, item_id) do update set
                    raw_item_id = excluded.raw_item_id,
                    stage = excluded.stage,
                    category = excluded.category,
                    selected = excluded.selected,
                    selection_reason = excluded.selection_reason,
                    core_fact = excluded.core_fact,
                    rewritten_title = excluded.rewritten_title,
                    summary = excluded.summary,
                    risk_flags_json = excluded.risk_flags_json,
                    updated_at = excluded.updated_at
                """,
                (
                    run_id,
                    raw_item_id,
                    item.id,
                    stage,
                    item.category,
                    1 if item.selected else 0,
                    item.selection_reason,
                    item.core_fact,
                    item.rewritten_title,
                    item.summary,
                    json.dumps(item.risk_flags or [], ensure_ascii=False),
                    utc_now_iso(),
                ),
            )

    def get_latest_rewritten_items(self, item_ids: Iterable[str]) -> dict[str, dict[str, Any]]:
        cached: dict[str, dict[str, Any]] = {}
        unique_item_ids = [item_id for item_id in dict.fromkeys(item_ids) if item_id]
        if not unique_item_ids:
            return cached
        with self.connect() as conn:
            for item_id in unique_item_ids:
                row = conn.execute(
                    """
                    select category, selection_reason, core_fact, rewritten_title,
                           summary, risk_flags_json
                    from processed_items
                    where item_id = ?
                      and stage in ('rewritten', 'publishable')
                      and coalesce(rewritten_title, '') != ''
                      and coalesce(summary, '') != ''
                    order by updated_at desc, id desc
                    limit 1
                    """,
                    (item_id,),
                ).fetchone()
                if not row:
                    continue
                try:
                    risk_flags = json.loads(row["risk_flags_json"] or "[]")
                except json.JSONDecodeError:
                    risk_flags = []
                cached[item_id] = {
                    "category": row["category"] or "",
                    "selection_reason": row["selection_reason"] or "",
                    "core_fact": row["core_fact"] or "",
                    "rewritten_title": row["rewritten_title"] or "",
                    "summary": row["summary"] or "",
                    "risk_flags": risk_flags if isinstance(risk_flags, list) else [],
                }
        return cached

    def add_filter_event(
        self,
        run_id: int,
        stage: str,
        action: str,
        item: NewsItem | None = None,
        raw_item_id: int | None = None,
        reason_code: str = "",
        reason_detail: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into filter_events(
                    run_id, raw_item_id, item_id, stage, action, reason_code,
                    reason_detail, created_at
                )
                values(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    raw_item_id,
                    item.id if item else None,
                    stage,
                    action,
                    reason_code,
                    reason_detail,
                    utc_now_iso(),
                ),
            )

    def add_llm_call(
        self,
        run_id: int,
        task: str,
        model: str,
        input_item_ids: list[str],
        prompt_hash: str,
        request_preview: str,
        response_preview: str,
        response_log_path: str,
        parsed: Any,
        status: str,
        error_message: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into llm_calls(
                    run_id, task, model, input_item_ids_json, prompt_hash,
                    request_preview, response_preview, response_log_path,
                    parsed_json, status, error_message, created_at
                )
                values(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run_id,
                    task,
                    model,
                    json.dumps(input_item_ids, ensure_ascii=False),
                    prompt_hash,
                    request_preview,
                    response_preview,
                    response_log_path,
                    json.dumps(parsed, ensure_ascii=False)[:4000] if parsed is not None else "",
                    status,
                    error_message,
                    utc_now_iso(),
                ),
            )

    def add_app_log(
        self,
        run_id: int | None,
        level: str,
        module: str,
        event: str,
        message: str,
        item_id: str = "",
        log_path: str = "",
    ) -> None:
        with self.connect() as conn:
            conn.execute(
                """
                insert into app_logs(
                    run_id, level, module, event, message, item_id, log_path, created_at
                )
                values(?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (run_id, level, module, event, message, item_id, log_path, utc_now_iso()),
            )

    def export_items_json(self, path: str, items: list[NewsItem]) -> str:
        output = Path(path)
        output.parent.mkdir(parents=True, exist_ok=True)
        with output.open("w", encoding="utf-8") as f:
            json.dump([item.to_dict() for item in items], f, ensure_ascii=False, indent=2)
        return str(output)

    def get_review_items_flat(
        self,
        run_id: int,
        filter_stages: list[str],
    ) -> list[NewsItem]:
        """获取 review 文章的扁平列表（按 publish_time 降序）。"""
        with self.connect() as conn:
            rows = conn.execute(
                """
                select
                    fe.raw_item_id,
                    ri.item_id,
                    ri.title,
                    ri.source,
                    ri.url,
                    ri.desc,
                    ri.publish_time,
                    ri.time_text,
                    ri.extra_json
                from filter_events fe
                join raw_items ri on ri.id = fe.raw_item_id
                where fe.run_id = ? and fe.action = 'review' and fe.stage in ({seq})
                order by ri.publish_time desc
                """.format(seq=",".join("?" for _ in filter_stages)),
                [run_id] + filter_stages,
            ).fetchall()

        items: list[NewsItem] = []
        for row in rows:
            extra = json.loads(row[8]) if row[8] else {}
            item = NewsItem(
                id=str(row[1]),
                title=row[2] or "",
                source=row[3] or "",
                url=row[4] or "",
                desc=row[5] or "",
                publish_time=row[6],
                time_text=row[7] or "",
                extra=extra,
            )
            item.ensure_id()
            items.append(item)
        return items

    def get_review_items_from_processed(self, run_id: int) -> list[NewsItem]:
        """直接从 processed_items 表查询 stage='review' 的文章（供 supplement 使用）。"""
        with self.connect() as conn:
            rows = conn.execute(
                """
                select
                    ri.id,
                    ri.item_id,
                    ri.title,
                    ri.source,
                    ri.url,
                    ri.desc,
                    ri.publish_time,
                    ri.time_text,
                    ri.extra_json,
                    pi.selection_reason,
                    pi.core_fact,
                    pi.rewritten_title,
                    pi.summary
                from processed_items pi
                join raw_items ri on ri.id = pi.raw_item_id
                where pi.run_id = ? and pi.stage = 'review'
                order by ri.publish_time desc
                """,
                (run_id,),
            ).fetchall()

        items: list[NewsItem] = []
        for row in rows:
            extra = json.loads(row[8]) if row[8] else {}
            item = NewsItem(
                id=str(row[1]),
                raw_id=row[0],
                title=row[2] or "",
                source=row[3] or "",
                url=row[4] or "",
                desc=row[5] or "",
                publish_time=row[6],
                time_text=row[7] or "",
                extra=extra,
            )
            item.ensure_id()
            items.append(item)
        return items

    def get_raw_item_by_id(self, raw_id: int) -> NewsItem | None:
        """通过 raw_items 表的主键 ID 获取单条记录。"""
        with self.connect() as conn:
            row = conn.execute(
                """SELECT id, item_id, title, source, source_type, url, desc,
                          content, html_content, publish_time, time_text,
                          cover_url, extra_json
                   FROM raw_items WHERE id = ?""",
                (raw_id,),
            ).fetchone()
        if not row:
            return None
        extra = json.loads(row[12]) if row[12] else {}
        item = NewsItem(
            id=str(row[1]),
            raw_id=row[0],
            title=row[2] or "",
            source=row[3] or "",
            source_type=row[4] or "",
            url=row[5] or "",
            desc=row[6] or "",
            content=row[7] or "",
            html_content=row[8] or "",
            publish_time=row[9],
            time_text=row[10] or "",
            cover_url=row[11] or "",
            extra=extra,
        )
        item.ensure_id()
        return item

    def get_review_items_by_filter(
        self,
        run_id: int,
        filter_stages: list[str],
    ) -> dict[str, list[NewsItem]]:
        """按 filter 类型分组获取 review 文章列表（兼容保留）。"""
        with self.connect() as conn:
            rows = conn.execute(
                """
                select
                    fe.stage,
                    ri.item_id,
                    ri.title,
                    ri.source,
                    ri.url,
                    ri.desc,
                    ri.publish_time,
                    ri.time_text,
                    ri.extra_json
                from filter_events fe
                join raw_items ri on ri.id = fe.raw_item_id
                where fe.run_id = ? and fe.action = 'review' and fe.stage in ({seq})
                order by fe.stage, ri.publish_time desc
                """.format(seq=",".join("?" for _ in filter_stages)),
                [run_id] + filter_stages,
            ).fetchall()

        result: dict[str, list[NewsItem]] = {s: [] for s in filter_stages}
        for row in rows:
            stage = row[0]
            extra = json.loads(row[8]) if row[8] else {}
            item = NewsItem(
                id=str(row[1]),
                title=row[2] or "",
                source=row[3] or "",
                url=row[4] or "",
                desc=row[5] or "",
                publish_time=row[6],
                time_text=row[7] or "",
                extra=extra,
            )
            item.ensure_id()
            result[stage].append(item)
        return result
    def get_published_items_for_run(
        self,
        run_id: int,
    ) -> list[NewsItem]:
        """获取指定 run 的所有 publishable 文章（含 rewrite 后的数据）。"""
        with self.connect() as conn:
            rows = conn.execute(
                """
                select
                    ri.item_id,
                    ri.title,
                    ri.source,
                    ri.url,
                    ri.desc,
                    ri.publish_time,
                    ri.time_text,
                    ri.extra_json,
                    pi.category,
                    pi.selection_reason,
                    pi.core_fact,
                    pi.rewritten_title,
                    pi.summary,
                    pi.risk_flags_json
                from processed_items pi
                join raw_items ri on ri.id = pi.raw_item_id
                where pi.run_id = ?
                  and pi.stage in ('publishable', 'rewritten')
                order by pi.updated_at asc
                """,
                (run_id,),
            ).fetchall()

        items: list[NewsItem] = []
        for row in rows:
            extra = json.loads(row[7]) or {} if row[7] else {}
            try:
                risk_flags = json.loads(row[13] or "[]")
            except json.JSONDecodeError:
                risk_flags = []
            item = NewsItem(
                id=str(row[0]),
                title=row[1] or "",
                source=row[2] or "",
                url=row[3] or "",
                desc=row[4] or "",
                publish_time=row[5],
                time_text=row[6] or "",
                extra=extra,
            )
            item.ensure_id()
            item.category = row[8] or ""
            item.selection_reason = row[9] or ""
            item.core_fact = row[10] or ""
            item.rewritten_title = row[11] or ""
            item.summary = row[12] or ""
            item.risk_flags = risk_flags if isinstance(risk_flags, list) else []
            items.append(item)
        return items



    def get_published_items_for_run(
        self,
        run_id: int,
    ) -> list[NewsItem]:
        """获取指定 run 的所有 publishable 文章（含 rewrite 后的数据）。"""
        with self.connect() as conn:
            rows = conn.execute(
                """
                select
                    ri.item_id,
                    ri.title,
                    ri.source,
                    ri.url,
                    ri.desc,
                    ri.publish_time,
                    ri.time_text,
                    ri.extra_json,
                    pi.category,
                    pi.selection_reason,
                    pi.core_fact,
                    pi.rewritten_title,
                    pi.summary,
                    pi.risk_flags_json
                from processed_items pi
                join raw_items ri on ri.id = pi.raw_item_id
                where pi.run_id = ?
                  and pi.stage in ('publishable', 'rewritten')
                order by pi.updated_at asc
                """,
                (run_id,),
            ).fetchall()

        items: list[NewsItem] = []
        for row in rows:
            extra = json.loads(row[7]) or {} if row[7] else {}
            try:
                risk_flags = json.loads(row[13] or "[]")
            except json.JSONDecodeError:
                risk_flags = []
            item = NewsItem(
                id=str(row[0]),
                title=row[1] or "",
                source=row[2] or "",
                url=row[3] or "",
                desc=row[4] or "",
                publish_time=row[5],
                time_text=row[6] or "",
                extra=extra,
            )
            item.ensure_id()
            item.category = row[8] or ""
            item.selection_reason = row[9] or ""
            item.core_fact = row[10] or ""
            item.rewritten_title = row[11] or ""
            item.summary = row[12] or ""
            item.risk_flags = risk_flags if isinstance(risk_flags, list) else []
            items.append(item)
        return items
