from __future__ import annotations

import argparse
import sys
from collections import Counter
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from src.config import load_config
from src.sources.portals import PortalSource


def main() -> int:
    parser = argparse.ArgumentParser(description="Smoke test live portal collection.")
    parser.add_argument("--config", default="config.yaml")
    parser.add_argument("--strict", action="store_true", help="Fail if any configured portal returns 0 items.")
    args = parser.parse_args()

    config = load_config(args.config, "secrets.yaml")
    enabled_sites = [
        site.get("name")
        for site in config.get("portal_sites", [])
        if site.get("enabled", True)
    ]

    items = PortalSource(config).collect()
    counts = Counter(item.source for item in items)

    print(f"enabled_sites={enabled_sites}")
    print(f"total_items={len(items)}")
    print(f"source_counts={dict(counts)}")
    for idx, item in enumerate(items[:12], 1):
        print(
            f"{idx}. [{item.source}] {item.title} | "
            f"time={item.time_text or item.publish_time or ''} | url={item.url}"
        )

    if not items:
        raise SystemExit("No portal items collected.")

    if args.strict:
        missing = []
        source_aliases = {
            "huxiu": ["虎嗅"],
            "qbitai": ["量子位"],
            "aibase": ["AIBase"],
        }
        for site in enabled_sites:
            aliases = source_aliases.get(site, [site])
            if not any(counts.get(alias, 0) > 0 for alias in aliases):
                missing.append(site)
        if missing:
            raise SystemExit(f"Configured portal returned 0 items: {missing}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
