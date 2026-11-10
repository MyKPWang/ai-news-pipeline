from __future__ import annotations

import logging
import gc
from dataclasses import dataclass
from pathlib import Path

from ..models import NewsItem

logger = logging.getLogger(__name__)


@dataclass
class BgeDedupResult:
    kept: list[NewsItem]
    removed: list[tuple[NewsItem, str]]
    used_bge: bool
    reason_code: str = ""


class BgeModelUnavailable(RuntimeError):
    def __init__(self, reason_code: str, message: str):
        super().__init__(message)
        self.reason_code = reason_code


def resolve_bge_model_path(config: dict) -> str:
    settings = config.get("bge_model", {})
    configured_path = str(settings.get("path", "")).strip()
    if configured_path:
        path = Path(configured_path).expanduser()
        if not path.is_dir():
            raise BgeModelUnavailable("BGE_LOCAL_MODEL_MISSING", f"local BGE model directory is missing: {path}")
        return str(path)
    if settings.get("allow_remote_download", False):
        return str(settings.get("name", "BAAI/bge-small-zh-v1.5"))
    raise BgeModelUnavailable(
        "BGE_LOCAL_MODEL_UNCONFIGURED",
        "BGE requires bge_model.path; run scripts/bootstrap_bge_model.py before enabling it",
    )


def bge_dedup(items: list[NewsItem], config: dict) -> BgeDedupResult:
    if len(items) <= 1 or not config.get("bge_model", {}).get("enabled", True):
        return BgeDedupResult(kept=items, removed=[], used_bge=False)

    settings = config.get("bge_model", {})
    model = None
    embeddings = None
    try:
        import numpy as np
        from sentence_transformers import SentenceTransformer

        model_path = resolve_bge_model_path(config)
        threshold = float(settings.get("threshold", 0.85))
        model = SentenceTransformer(model_path, local_files_only=not settings.get("allow_remote_download", False))

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
    except BgeModelUnavailable as exc:
        logger.warning("BGE dedup skipped reason=%s error=%s", exc.reason_code, exc)
        return BgeDedupResult(kept=items, removed=[], used_bge=False, reason_code=exc.reason_code)
    except Exception as exc:
        logger.warning("BGE dedup skipped reason=BGE_MODEL_LOAD_FAILED error=%s", exc)
        return BgeDedupResult(kept=items, removed=[], used_bge=False, reason_code="BGE_MODEL_LOAD_FAILED")
    finally:
        if settings.get("force_gc_after_run", True):
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
