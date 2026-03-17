# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
OpenAI Embedding Model Implementation

Embedding client implementation for services following OpenAI standard.
"""

import ssl
from typing import List, Optional

import httpx
import openai
from openai.types import CreateEmbeddingResponse

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
from openjiuwen.core.retrieval.embedding.utils import parse_base64_embedding


class OpenAIEmbedding(APIEmbedding):
    """
    OpenAI embedding client, supports encoding_format="base64"
    """

    def __init__(
        self,
        config: EmbeddingConfig,
        timeout: int = 60,
        max_retries: int = 3,
        extra_headers: Optional[dict] = None,
        max_batch_size: int = 8,
        max_concurrent: int = 50,
        dimension: Optional[int] = None,
        verify: bool | str | ssl.SSLContext = True,
        **kwargs,
    ):
        """
        Initialize OpenAI embedder.

        Args:
            config: Embedding model configuration
            timeout: Request timeout in seconds
            max_retries: Maximum retry count
            extra_headers: Additional request headers
            max_batch_size: Maximum batch size for each query
            max_concurrent: Maximum number of concurrent requests
            dimension: Embedding dimension for Matryoshka models
            verify (bool/str/ssl.SSLContext): Decides SSL context to use for the httpx clients,
                bool: whether to use SSL context with default CA certificate (using EMBEDDING_SSL_CERT from
                https://gitcode.com/openJiuwen/agent-core/pull/180 if possible, otherwise using system default);
                str: path to custom CA certificate, this certificate is used to create the SSL context;
                ssl.SSLContext: custom SSL context to use.
            **kwargs: optional keyword arguments to pass into httpx clients
        """
        super().__init__(
            config,
            timeout=timeout,
            max_retries=max_retries,
            extra_headers=extra_headers,
            max_batch_size=max_batch_size,
            max_concurrent=max_concurrent,
        )
        self.matryoshka_dimension = False
        if isinstance(dimension, int):
            self._dimension = dimension
            self.matryoshka_dimension = True
        self.api_url = self.api_url.removesuffix("/").removesuffix("/embeddings")

        # Create OpenAI clients
        if verify is True and isinstance(self._verify_ssl, str):
            verify = self._verify_ssl
        client_kwargs = dict(verify=verify, timeout=self.timeout, base_url=self.api_url) | kwargs
        self.async_client = openai.AsyncOpenAI(
            api_key=self.api_key,
            base_url=self.api_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            default_headers=self._headers,
            http_client=httpx.AsyncClient(**client_kwargs),
        )
        self.client = openai.OpenAI(
            api_key=self.api_key,
            base_url=self.api_url,
            timeout=self.timeout,
            max_retries=self.max_retries,
            default_headers=self._headers,
            http_client=httpx.Client(**client_kwargs),
        )

    @staticmethod
    def parse_openai_response(resp: CreateEmbeddingResponse) -> List[List[float]]:
        """Parse OpenAI embedding response object and return embedding result"""

        # Check if any data is returned
        if not isinstance(getattr(resp, "data", None), (list, float, str)):
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_RESPONSE_INVALID, error_msg=f"No embeddings in response: {resp}"
            )

        # Get raw embedding results
        embeddings_raw = resp.data
        if not isinstance(embeddings_raw, list):
            embeddings_raw = [embeddings_raw]
        embeddings_raw.sort(key=lambda emb_obj: getattr(emb_obj, "index", -1))
        embeddings = (getattr(emb, "embedding", None) for emb in embeddings_raw)
        embeddings = [emb for emb in embeddings if emb is not None]
        if any(isinstance(emb, str) for emb in embeddings):
            try:
                embeddings = [parse_base64_embedding(emb) for emb in embeddings]
            except Exception as e:
                raise build_error(
                    StatusCode.RETRIEVAL_EMBEDDING_RESPONSE_INVALID,
                    error_msg=f"OpenAI service returned invalid base64 string embedding: {e}",
                    cause=e,
                ) from e

        # Check if valid embeddings are returned
        if not embeddings:
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_RESPONSE_INVALID,
                error_msg=f"No embedding field found in data items: {embeddings_raw}",
            )

        return embeddings

    async def _get_embeddings(self, text: str | List[str], **kwargs) -> List[List[float]]:
        """Get embedding vectors"""

        kwargs.pop("callback_cls", None)
        dimensions = kwargs.get("dimensions")
        if self.matryoshka_dimension and dimensions is None:
            kwargs["dimensions"] = self._dimension

        for attempt in range(self.max_retries):
            try:
                resp = await self.async_client.embeddings.create(
                    input=text,
                    model=self.model_name,
                    timeout=self.timeout,
                    **kwargs,
                )

                embeddings = self.parse_openai_response(resp)

                # If dimension not yet determined, get from result and cache
                if self._dimension is None and embeddings and embeddings[0]:
                    self._dimension = len(embeddings[0])
                    logger.debug("Determined embedding dimension: %d", self._dimension)

                return embeddings
            except openai.APIError as e:
                if attempt == self.max_retries - 1:
                    raise build_error(
                        StatusCode.RETRIEVAL_EMBEDDING_REQUEST_CALL_FAILED,
                        error_msg=f"{str(e)} (max_retries={self.max_retries})",
                        cause=e,
                    ) from e
                logger.warning(
                    "Embedding request failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.max_retries,
                    e,
                )
        raise build_error(
            StatusCode.RETRIEVAL_EMBEDDING_UNREACHABLE_CALL_FAILED, error_msg="Unreachable code in _get_embeddings"
        )

    def _get_embeddings_sync(self, text: str | List[str], **kwargs) -> List[List[float]]:
        """Get embedding vectors (sync version)."""

        kwargs.pop("callback_cls", None)
        dimensions = kwargs.get("dimensions")
        if self.matryoshka_dimension and dimensions is None:
            kwargs["dimensions"] = self._dimension

        for attempt in range(self.max_retries):
            try:
                resp = self.client.embeddings.create(input=text, model=self.model_name, timeout=self.timeout, **kwargs)

                embeddings = self.parse_openai_response(resp)

                # If dimension not yet determined, get from result and cache
                if self._dimension is None and embeddings and embeddings[0]:
                    self._dimension = len(embeddings[0])
                    logger.debug("Determined embedding dimension: %d", self._dimension)

                return embeddings
            except openai.APIError as e:
                if attempt == self.max_retries - 1:
                    raise build_error(
                        StatusCode.RETRIEVAL_EMBEDDING_REQUEST_CALL_FAILED,
                        error_msg=f"{str(e)} (max_retries={self.max_retries})",
                        cause=e,
                    ) from e
                logger.warning(
                    "Embedding request failed (attempt %s/%s): %s",
                    attempt + 1,
                    self.max_retries,
                    e,
                )
        raise build_error(
            StatusCode.RETRIEVAL_EMBEDDING_UNREACHABLE_CALL_FAILED, error_msg="Unreachable code in _get_embeddings"
        )
