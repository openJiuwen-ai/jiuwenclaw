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
    # Qdrant/bge-small-zh-v1.5 等仓库用 HuggingFace Xet/CAS 存储；Xet 重组请求走
    # cas-server.xethub.hf.co，不经 HF_ENDPOINT 镜像代理，在受限网络下会超时/401
    # 导致 TextEmbedding() 加载失败、dense 检索整体不可用。关掉 Xet 回退到经典
    # HTTP 下载（走镜像）即可。镜像已缓存模型时为 no-op。
    _xet_was_unset = "HF_HUB_DISABLE_XET" not in os.environ
    os.environ.setdefault("HF_HUB_DISABLE_XET", "1")
    if _xet_was_unset:
        logger.info(
            "[tool_retrieval] HF_HUB_DISABLE_XET not set; defaulting to 1 (classic "
            "HTTP download via HF_ENDPOINT). Set it explicitly to override."
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
