# -*- coding: utf-8 -*-
"""Embedding model wrapper. No agent-core dependency."""
from __future__ import annotations

import logging
import os
import shutil

logger = logging.getLogger("jiuwenswarm.common.tool_retrieval.embedder")

# Standard HF hub cache (managed by huggingface_hub tools, survives OS temp
# cleanup). fastembed's default is a TEMP dir which is fragile — the OS clears
# it, and an interrupted download leaves 0-byte shells that fastembed treats as
# "downloaded" and then fails to load (ONNX NO_SUCHFILE) without self-healing.
# Pin to the standard location so a once-downloaded model stays put.
#
# Overridable via FASTEMBED_CACHE_DIR.
#
# NOTE: this is a ROBUSTNESS improvement, NOT the root-cause fix. The actual
# blocker for dense is network reachability to HuggingFace (TLS handshake to
# huggingface.co / hf-mirror.com blocked). cache_dir only helps once a download
# can succeed — it can't conjure a model out of thin air.
_DEFAULT_CACHE_DIR = os.path.join(
    os.path.expanduser("~"), ".cache", "huggingface", "hub"
)


def _resolve_cache_dir() -> str:
    return os.environ.get("FASTEMBED_CACHE_DIR", _DEFAULT_CACHE_DIR)


def ensure_embedding_model(model_name: str = "BAAI/bge-small-zh-v1.5"):
    """Load the embedding model via fastembed; return model or None.

    Robustness (NOT the root-cause fix — network reachability to HF is the
    actual blocker for dense): pins ``cache_dir`` to the standard HF hub cache
    (not fastembed's fragile temp default) and self-rescues from
    interrupted-download residue by clearing + retrying once.
    """
    _was_unset = "HF_ENDPOINT" not in os.environ
    os.environ.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    if _was_unset:
        logger.info(
            "[tool_retrieval] HF_ENDPOINT not set; defaulting to CN mirror "
            "(https://hf-mirror.com). Set HF_ENDPOINT explicitly to override."
        )
    cache_dir = _resolve_cache_dir()
    try:
        from fastembed import TextEmbedding
    except ImportError:
        logger.error(
            "[tool_retrieval] dense DISABLED: fastembed is not installed. "
            "Tool dense search needs it — run `pip install fastembed`. "
            "Falling back to BM25 + name-lookup (Chinese recall will be poor)."
        )
        return None
    try:
        return TextEmbedding(model_name, cache_dir=cache_dir)
    except Exception as exc:
        # Self-rescue: an interrupted download can leave 0-byte shells that
        # fastembed treats as "downloaded" then fails to load
        # (ONNXRuntimeError NO_SUCHFILE); fastembed does NOT self-verify.
        # Clear the residue + retry once. Only helps when the network can
        # re-download; if the network is the blocker (HF unreachable), the
        # retry fails the same way and we give up cleanly.
        logger.warning(
            "[tool_retrieval] embedding load failed (cache_dir=%s): %s — "
            "attempting one cache-clear + retry to recover from possible "
            "interrupted-download residue.", cache_dir, exc,
        )
        if _clear_model_cache(cache_dir, model_name):
            try:
                return TextEmbedding(model_name, cache_dir=cache_dir)
            except Exception as exc2:
                logger.error(
                    "[tool_retrieval] dense DISABLED: model %r still failed "
                    "after cache clear (likely network can't reach HF to "
                    "download). cache_dir=%s, Error: %s. Falling back to "
                    "BM25 + name-lookup; retry on next agent restart once "
                    "network/cache is available.",
                    model_name, cache_dir, exc2,
                )
                return None
        logger.error(
            "[tool_retrieval] dense DISABLED: model %r failed to load, no "
            "residue cache to clear (likely first-run download blocked / "
            "network issue). cache_dir=%s, Error: %s. Falling back to BM25 + "
            "name-lookup; retry on next restart once the model is cached.",
            model_name, cache_dir, exc,
        )
        return None


def _clear_model_cache(cache_dir: str, model_name: str) -> bool:
    """Best-effort remove residue so fastembed re-downloads. Never raises.

    fastembed may mirror the model under a different org (e.g. a BAAI model
    fetched as ``Qdrant/<name>``), so glob by the repo-name suffix rather than
    the literal org. Also clears fastembed's temp default cache (residue from
    prior runs that didn't pass ``cache_dir``).
    """
    removed = False
    suffix = model_name.split("/", 1)[-1] if "/" in model_name else model_name
    if os.path.isdir(cache_dir):
        try:
            for entry in os.listdir(cache_dir):
                if entry.startswith("models--") and entry.endswith(f"--{suffix}"):
                    shutil.rmtree(os.path.join(cache_dir, entry), ignore_errors=True)
                    removed = True
        except OSError:
            pass
    temp_cache = os.path.join(
        os.environ.get("LOCALAPPDATA", ""), "Temp", "fastembed_cache"
    )
    if os.path.isdir(temp_cache):
        shutil.rmtree(temp_cache, ignore_errors=True)
        removed = True
    if removed:
        logger.info(
            "[tool_retrieval] cleared residue cache for retry (suffix=%s).", suffix
        )
    return removed


def embed_texts(model, texts):
    if model is None:
        return []
    return list(model.embed(texts))
