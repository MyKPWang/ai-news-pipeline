from __future__ import annotations

import sys
from datetime import datetime
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.llm.minimax import MiniMaxClient
from src.models import NewsItem
from src.storage import Storage


def main() -> int:
    config = load_config("config.yaml", "secrets.yaml")
    config["output"]["database_path"] = "data/minimax_smoke.db"
    config["output"]["llm_log_path"] = "logs/llm_smoke_{date}.jsonl"
    config["minimax_api"]["max_retries"] = 1

    storage = Storage(config["output"]["database_path"])
    run_id = storage.start_run(datetime.now().strftime("%Y%m%d"))

    items = [
        NewsItem(
            title="千问发布第三方厂商接入指南",
            desc="千问面向第三方厂商开放接入说明，帮助设备和应用接入 AI 能力。",
            source="Smoke",
            source_type="portal",
            url="https://example.com/qwen",
            publish_time=int(datetime.now().timestamp()),
        ),
        NewsItem(
            title="某科技公司发布季度财报",
            desc="公司披露营收和利润数据，股价盘后上涨。",
            source="Smoke",
            source_type="portal",
            url="https://example.com/earnings",
            publish_time=int(datetime.now().timestamp()),
        ),
    ]
    for item in items:
        item.ensure_id()

    try:
        client = MiniMaxClient(config, storage, run_id)
        selected, global_info = client.select_items(items)
        rewritten = client.rewrite_items(selected[:1])

        print(f"selected_count={len(selected)}")
        print(f"hot_topics={global_info.get('hot_topics', [])}")
        if selected:
            print(f"selected_title={selected[0].title}")
            print(f"selected_category={selected[0].category}")
            print(f"selected_core_fact={selected[0].core_fact}")
        if rewritten:
            print(f"rewritten_title={rewritten[0].rewritten_title}")
            print(f"summary_length={len(rewritten[0].summary)}")
            print(f"risk_flags={rewritten[0].risk_flags}")
        storage.finish_run(run_id, "success", len(items), len(selected), len(rewritten))
        return 0
    except Exception as exc:
        storage.finish_run(run_id, "failed", len(items), 0, 0, str(exc))
        raise


if __name__ == "__main__":
    raise SystemExit(main())
