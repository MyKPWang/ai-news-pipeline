from __future__ import annotations

import argparse
import sys

from .config import load_config
from .logging_utils import setup_logging
from .pipeline import run_pipeline


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="AI news pipeline")
    parser.add_argument("--config", default="config.yaml", help="Path to config YAML")
    parser.add_argument("--secrets", default="secrets.yaml", help="Path to secrets YAML")
    parser.add_argument("--no-publish", action="store_true", help="Do not upload to WeChat draft box")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    config = load_config(args.config, args.secrets)
    setup_logging(config)
    no_publish = args.no_publish or bool(config.get("runtime", {}).get("default_no_publish", False))
    result = run_pipeline(config, no_publish=no_publish)
    print(f"run_id={result.run_id}")
    print(f"raw_items={len(result.raw_items)}")
    print(f"selected_items={len(result.selected_items)}")
    print(f"publishable_items={len(result.publishable_items)}")
    print(f"review_items={len(result.review_items)}")
    if result.html_path:
        print(f"html_path={result.html_path}")
    print(f"published={result.published}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
