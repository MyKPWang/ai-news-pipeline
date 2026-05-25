from __future__ import annotations

import logging
import os
import gc
from dataclasses import dataclass

from ..models import NewsItem

logger = logging.getLogger(__name__)


@dataclass
class BgeDedupResult:
    kept: list[NewsItem]
    removed: list[tuple[NewsItem, str]]
    used_bge: bool


def bge_dedup(items: list[NewsItem], config: dict) -> BgeDedupResult:
    if len(items) <= 1 or not config.get("bge_model", {}).get("enabled", True):
        return BgeDedupResult(kept=items, removed=[], used_bge=False)

    model = None
    embeddings = None
    try:
        os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
        import numpy as np
        from sentence_transformers import SentenceTransformer

        model_path = config.get("bge_model", {}).get("path") or config.get("bge_model", {}).get(
            "name", "BAAI/bge-small-zh-v1.5"
        )
        threshold = float(config.get("bge_model", {}).get("threshold", 0.85))
        model = SentenceTransformer(model_path)

        texts = [f"{item.title}。{item.desc}" for item in items]
        embeddings = model.encode(texts, convert_to_numpy=True)

        kept: list[NewsItem] = []
        kept_indices: list[int] = []
        removed: list[tuple[NewsItem, str]] = []

        for idx, item in enumerate(items):
            emb = embeddings[idx]
            duplicate_reason = ""
            for kept_idx in kept_indices:
                kept_emb = embeddings[kept_idx]
                sim = float(
                    np.dot(emb, kept_emb) / (np.linalg.norm(emb) * np.linalg.norm(kept_emb))
                )
                if sim > threshold:
                    duplicate_reason = f"semantic_duplicate similarity={sim:.3f}"
                    break
            if duplicate_reason:
                removed.append((item, duplicate_reason))
            else:
                kept.append(item)
                kept_indices.append(idx)

        return BgeDedupResult(kept=kept, removed=removed, used_bge=True)
    except Exception as exc:
        logger.warning("BGE dedup skipped: %s", exc)
        return BgeDedupResult(kept=items, removed=[], used_bge=False)
    finally:
        if config.get("bge_model", {}).get("force_gc_after_run", True):
            try:
                del embeddings
                del model
            except Exception:
                pass
            gc.collect()
            try:
                import torch

                if hasattr(torch, "mps") and hasattr(torch.mps, "empty_cache"):
                    torch.mps.empty_cache()
                if hasattr(torch, "cuda") and torch.cuda.is_available():
                    torch.cuda.empty_cache()
            except Exception:
                pass
