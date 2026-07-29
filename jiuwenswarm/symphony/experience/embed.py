from __future__ import annotations

import logging
from typing import Any

LOGGER = logging.getLogger(__name__)


class EmbeddingClient:
    """Minimal embedding client wrapping any OpenAI-compatible embeddings endpoint.

    Supports two backends:
      1. OpenAI-compatible API (base_url + api_key) — calls /v1/embeddings
      2. Local sentence-transformers model (model_name) — runs locally

    Pass exactly one of (base_url, api_key) or model_name.
    """

    def __init__(
        self,
        *,
        base_url: str | None = None,
        api_key: str = "",
        model: str = "",
        model_name: str = "",
        normalize: bool = True,
        dimension: int | None = None,
    ) -> None:
        self._backend: str = ""
        self._normalize = bool(normalize)
        self._dimension = dimension
        self._total_tokens: int = 0

        # Path 1: OpenAI-compatible API
        if base_url:
            try:
                from openai import OpenAI
                self._api_client = OpenAI(base_url=base_url, api_key=api_key or "dummy")
                self._api_model = str(model or "text-embedding-3-small")
                self._backend = "openai_api"
                LOGGER.info("EmbeddingClient: using OpenAI-compatible API, model=%s", self._api_model)
                return
            except ImportError:
                pass

        # Path 2: Local sentence-transformers
        if model_name:
            try:
                from sentence_transformers import SentenceTransformer
                self._st_model = SentenceTransformer(model_name)
                self._backend = "sentence_transformers"
                LOGGER.info("EmbeddingClient: using local sentence-transformers, model=%s", model_name)
                return
            except ImportError:
                pass

        raise RuntimeError(
            "EmbeddingClient: could not initialize any embedding backend.\n"
            "Option 1: install 'openai' and provide base_url.\n"
            "Option 2: install 'sentence-transformers' and provide model_name."
        )

    @property
    def dimension(self) -> int | None:
        """Return the configured embedding vector dimension, or None if unset."""
        return self._dimension

    def reset_token_counter(self) -> int:
        """Reset and return the accumulated token counter."""
        prev = self._total_tokens
        self._total_tokens = 0
        return prev

    def embed(self, text: str) -> list[float]:
        """Return a normalized embedding vector for the given text."""
        if self._backend == "openai_api":
            return self._embed_api(text)
        if self._backend == "sentence_transformers":
            return self._embed_local(text)
        raise RuntimeError("No embedding backend initialized")

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        """Return embeddings for multiple texts."""
        if self._backend == "openai_api":
            return [self._embed_api(t) for t in texts]
        if self._backend == "sentence_transformers":
            vecs = self._st_model.encode(texts, normalize_embeddings=self._normalize, show_progress_bar=False)
            return vecs.tolist()
        raise RuntimeError("No embedding backend initialized")

    # -- private --

    def _embed_api(self, text: str) -> list[float]:
        params: dict[str, Any] = {
            "input": text,
            "model": self._api_model,
        }
        if self._dimension is not None:
            params["dimensions"] = self._dimension
        resp = self._api_client.embeddings.create(**params)
        vec = resp.data[0].embedding
        usage = getattr(resp, "usage", None)
        if usage is not None:
            self._total_tokens += getattr(usage, "total_tokens", 0) or 0
        if self._normalize:
            vec = _l2_normalize(vec)
        return vec

    def _embed_local(self, text: str) -> list[float]:
        vec = self._st_model.encode(text, normalize_embeddings=self._normalize).tolist()
        return vec


def _l2_normalize(vec: list[float]) -> list[float]:
    norm = sum(x * x for x in vec) ** 0.5
    if norm == 0:
        return vec
    return [x / norm for x in vec]


__all__ = ["EmbeddingClient"]
