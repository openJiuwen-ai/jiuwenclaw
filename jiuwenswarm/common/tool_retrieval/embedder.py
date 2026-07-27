# -*- coding: utf-8 -*-
"""Embedding model wrapper. No agent-core dependency."""
from __future__ import annotations

import logging
import os

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.embedder")


def ensure_embedding_model(model_name: str = "BAAI/bge-small-zh-v1.5"):
    """Load the embedding model via fastembed; return model or None."""
    if not os.environ.get("HF_ENDPOINT"):
        os.environ["HF_ENDPOINT"] = "https://hf-mirror.com"
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
