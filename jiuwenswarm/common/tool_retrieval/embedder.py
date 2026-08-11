# -*- coding: utf-8 -*-
"""Embedding model wrapper. No agent-core dependency."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.embedder")


def ensure_embedding_model(model_name: str = "BAAI/bge-small-zh-v1.5"):
    """Load the embedding model via fastembed; return model or None."""
    _was_unset = "HF_ENDPOINT" not in os.environ
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    if _was_unset:
        logger.info(
            "[tool_retrieval] HF_ENDPOINT not set; defaulting to CN mirror "
            "(https://hf-mirror.com). Set HF_ENDPOINT explicitly to override."
        )
    try:
        from fastembed import TextEmbedding

        return TextEmbedding(model_name)
    except Exception as exc:
        logger.warning("[tool_retrieval] embedding load failed: %s", exc)
        return None


def embed_texts(model, texts):
    if model is None:
        return []
    return list(model.embed(texts))
