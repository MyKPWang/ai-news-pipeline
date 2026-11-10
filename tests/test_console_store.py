from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from fastapi.testclient import TestClient

from src.console.app import create_console_app
from src.console.store import ConsoleStore
from src.storage import Storage


class ConsoleStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = ConsoleStore(Path(self.temp_dir.name) / "news.db")

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_same_idempotency_key_reuses_the_original_job(self) -> None:
        first, reused = self.store.submit(
            job_type="generate_report",
            mode="refresh_and_report",
            request={"cache_only": False},
            idempotency_key="report:20260908:refresh",
        )
        second, second_reused = self.store.submit(
            job_type="generate_report",
            mode="refresh_and_report",
            request={"cache_only": False},
            idempotency_key="report:20260908:refresh",
        )

        self.assertFalse(reused)
        self.assertTrue(second_reused)
        self.assertEqual(first.id, second.id)
        self.assertEqual(first.status, "queued")

    def test_new_report_waits_until_current_report_has_finished(self) -> None:
        first, _ = self.store.submit(
            job_type="generate_report",
            mode="refresh_and_report",
            request={},
            idempotency_key="report:one",
        )
        with self.assertRaisesRegex(RuntimeError, "generate_report_already_running"):
            self.store.submit(
                job_type="generate_report",
                mode="cache_report",
                request={},
                idempotency_key="report:two",
            )

        claimed = self.store.claim_next()
        self.assertEqual(claimed.id, first.id)
        self.store.finish(first.id, status="failed", reason_code="LLM_UNAVAILABLE", message="selection failed")
        retried, reused = self.store.submit(
            job_type="generate_report",
            mode="cache_report",
            request={"cache_only": True},
            idempotency_key="report:retry",
        )

        self.assertFalse(reused)
        self.assertEqual(retried.status, "queued")
        self.assertEqual(self.store.get(first.id).reason_code, "LLM_UNAVAILABLE")

    def test_console_is_directly_available_on_the_local_address(self) -> None:
        config = {
            "output": {"database_path": str(Path(self.temp_dir.name) / "console.db")},
            "public_source_api": {"token": ""},
        }
        with TestClient(create_console_app(config)) as client:
            page = client.get("/console")
            overview = client.get("/api/v1/console/overview")

        self.assertEqual(page.status_code, 200)
        self.assertIn("资讯日报控制台", page.text)
        self.assertEqual(overview.status_code, 200)
        self.assertEqual(overview.json()["collector"]["status"], "unconfigured")

    def test_report_links_serve_latest_html_and_handle_deleted_files(self) -> None:
        root = Path(self.temp_dir.name)
        reports = root / "reports"
        reports.mkdir()
        report = reports / "news_20260908.html"
        report.write_text("<html><body>当日资讯</body></html>", encoding="utf-8")
        database = root / "reports.db"
        storage = Storage(str(database))
        storage.start_run("20260908")
        storage.start_run("20260907")
        config = {"output": {"database_path": str(database)}, "public_source_api": {"token": ""}}
        with TestClient(create_console_app(config, report_directory=reports)) as client:
            for endpoint in ("/api/v1/console/reports", "/api/v1/console/overview"):
                rows = {row["run_date"]: row for row in client.get(endpoint).json()["reports"]}
                self.assertIsNone(rows["20260907"]["html_url"])
                url = rows["20260908"]["html_url"]
                self.assertEqual(url, "/console/reports/20260908/html")
            response = client.get(url)
            self.assertEqual(response.status_code, 200)
            self.assertIn("当日资讯", response.text)
            self.assertTrue(response.headers["content-type"].startswith("text/html"))
            self.assertNotIn("attachment", response.headers.get("content-disposition", ""))
            self.assertEqual(response.headers["cache-control"], "no-store")
            report.write_text("<html>新版资讯</html>", encoding="utf-8")
            self.assertIn("新版资讯", client.get(url).text)
            report.unlink()
            self.assertEqual(client.get(url).status_code, 404)

    def test_report_route_rejects_invalid_dates_and_external_symlinks(self) -> None:
        root = Path(self.temp_dir.name)
        reports = root / "reports"
        reports.mkdir()
        private = root / "private.html"
        private.write_text("private data", encoding="utf-8")
        (reports / "news_20260908.html").symlink_to(private)
        config = {"output": {"database_path": str(root / "secure.db")}, "public_source_api": {"token": ""}}
        with TestClient(create_console_app(config, report_directory=reports)) as client:
            for date in ("20260908", "20260230", "invalid", "..%2Fprivate"):
                response = client.get(f"/console/reports/{date}/html")
                self.assertEqual(response.status_code, 404)
                self.assertNotIn("private data", response.text)


if __name__ == "__main__":
    unittest.main()
