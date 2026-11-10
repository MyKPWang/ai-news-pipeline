from __future__ import annotations

import os
import re
from contextlib import asynccontextmanager
from datetime import datetime
from pathlib import Path
from typing import Any

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from fastapi.templating import Jinja2Templates
from pydantic import BaseModel, Field

from ..config import load_config
from .service import ConsoleService
from .store import ConsoleStore


class ReportBody(BaseModel):
    cache_only: bool = False


class CollectionBody(BaseModel):
    source_slugs: list[str] = Field(default_factory=list, max_length=100)
    retry_failed: bool = False


def create_console_app(config: dict[str, Any], *, report_directory: Path | None = None) -> FastAPI:
    database_path = str(config.get("output", {}).get("database_path", "data/news.db"))
    store = ConsoleStore(database_path)
    service = ConsoleService(config, store)
    web_root = Path(__file__).parent / "web"
    report_root = (report_directory or Path("wechat-publish-tool/output")).resolve()

    def report_file(date: str) -> Path | None:
        if not re.fullmatch(r"[0-9]{8}", date):
            return None
        try:
            datetime.strptime(date, "%Y%m%d")
        except ValueError:
            return None
        path = (report_root / f"news_{date}.html").resolve()
        return path if path.parent == report_root and path.is_file() else None

    def report_rows(limit: int = 10) -> list[dict[str, Any]]:
        rows = store.overview_runs(limit)
        for row in rows:
            date = str(row["run_date"])
            row["html_url"] = f"/console/reports/{date}/html" if report_file(date) else None
        return rows

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        service.start()
        yield
        service.stop()

    app = FastAPI(title="AI News Daily Console", docs_url=None, redoc_url=None, lifespan=lifespan)
    app.mount("/console/static", StaticFiles(directory=str(web_root / "static")), name="console-static")
    templates = Jinja2Templates(directory=str(web_root / "templates"))

    @app.exception_handler(HTTPException)
    async def api_error(request: Request, error: HTTPException) -> JSONResponse | HTMLResponse:
        if request.url.path.startswith("/api/"):
            return JSONResponse(status_code=error.status_code, content={"error": {"code": "CONSOLE_REQUEST_REJECTED", "message": str(error.detail)}})
        return HTMLResponse(str(error.detail), status_code=error.status_code)

    @app.get("/console", response_class=HTMLResponse)
    @app.get("/console/{page_name}", response_class=HTMLResponse)
    def page(request: Request, page_name: str = "overview") -> HTMLResponse:
        valid = {"overview", "reports", "sources", "operations"}
        return templates.TemplateResponse(request, "console.html", {"page_name": page_name if page_name in valid else "overview"})

    @app.get("/console/reports/{date}/html", response_class=FileResponse)
    def report_html(date: str) -> FileResponse:
        path = report_file(date)
        if path is None:
            raise HTTPException(status_code=404, detail="未找到该日期的日报 HTML，请返回控制台确认是否已生成。")
        return FileResponse(path, media_type="text/html", headers={
            "Cache-Control": "no-store",
            "X-Content-Type-Options": "nosniff",
            "Content-Security-Policy": "sandbox allow-popups allow-popups-to-escape-sandbox; default-src 'none'; style-src 'unsafe-inline'; img-src data: https:; base-uri 'none'; form-action 'none'",
        })

    @app.get("/api/v1/console/overview")
    def overview() -> dict[str, Any]:
        collector = service.collector_overview()
        return {"counts": store.counts(), "reports": report_rows(), "jobs": [as_job(job) for job in store.list(8)], "collector": collector}

    @app.get("/api/v1/console/reports")
    def reports() -> dict[str, Any]:
        return {"reports": report_rows(50)}

    @app.get("/api/v1/console/sources")
    def sources() -> dict[str, Any]:
        return service.collector_overview()

    @app.get("/api/v1/console/jobs")
    def jobs() -> dict[str, Any]:
        return {"jobs": [as_job(job) for job in store.list()]}

    @app.get("/api/v1/console/events")
    def events() -> dict[str, Any]:
        return {"events": store.events()}

    @app.post("/api/v1/console/reports", status_code=202)
    def create_report(body: ReportBody, key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
        if not key:
            raise HTTPException(status_code=400, detail="缺少 Idempotency-Key")
        try:
            job, reused = service.submit_report(body.cache_only, key)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"job": as_job(job), "reused": reused}

    @app.post("/api/v1/console/collections", status_code=202)
    def create_collection(body: CollectionBody, key: str | None = Header(default=None, alias="Idempotency-Key")) -> dict[str, Any]:
        if not key:
            raise HTTPException(status_code=400, detail="缺少 Idempotency-Key")
        try:
            job, reused = service.submit_collection(body.source_slugs, body.retry_failed, key)
        except RuntimeError as error:
            raise HTTPException(status_code=409, detail=str(error)) from error
        return {"job": as_job(job), "reused": reused}

    return app


def as_job(job) -> dict[str, Any]:
    return {
        "id": job.id, "job_type": job.job_type, "mode": job.mode, "status": job.status,
        "request": job.request, "result": job.result, "reason_code": job.reason_code,
        "message": job.message, "pipeline_run_id": job.pipeline_run_id, "created_at": job.created_at,
        "started_at": job.started_at, "finished_at": job.finished_at,
    }


def main() -> int:
    import uvicorn

    config_path = os.getenv("AI_NEWS_PIPELINE_CONFIG", "config.yaml")
    secrets_path = os.getenv("AI_NEWS_PIPELINE_SECRETS", "secrets.yaml")
    config = load_config(config_path, secrets_path)
    host = os.getenv("AI_NEWS_CONSOLE_HOST", "127.0.0.1")
    port = int(os.getenv("AI_NEWS_CONSOLE_PORT", "4102"))
    uvicorn.run(create_console_app(config), host=host, port=port)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
