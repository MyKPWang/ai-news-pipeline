#!/usr/bin/env python3
"""Download and smoke-test the BGE model before a scheduled Pipeline run."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    parser = argparse.ArgumentParser(description="Bootstrap a local BGE model for AI News Pipeline")
    parser.add_argument("--provider", choices=("modelscope", "huggingface"), default="modelscope")
    parser.add_argument("--repo-id", default="BAAI/bge-small-zh-v1.5")
    parser.add_argument("--modelscope-repo-id", default="AI-ModelScope/bge-small-zh-v1.5")
    parser.add_argument("--destination", type=Path, default=Path("data/models/bge-small-zh-v1.5"))
    args = parser.parse_args()

    from sentence_transformers import SentenceTransformer

    destination = args.destination.resolve()
    if args.provider == "modelscope":
        from modelscope_hub.compat import snapshot_download

        snapshot_download(model_id=args.modelscope_repo_id, local_dir=str(destination), max_workers=2)
    else:
        from huggingface_hub import snapshot_download

        snapshot_download(repo_id=args.repo_id, local_dir=str(destination))
    SentenceTransformer(str(destination), local_files_only=True)
    print(f"BGE model ready: {destination}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
