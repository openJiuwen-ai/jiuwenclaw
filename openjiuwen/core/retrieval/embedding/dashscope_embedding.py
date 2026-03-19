# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
DashScope Multimodal Embedding Model Implementation

Multimodal embedding client using Alibaba DashScope (dashscope) library.
Supports text, image, and video embedding.

Reference resources:
- https://help.aliyun.com/zh/model-studio/multimodal-embedding-api-reference
- https://www.alibabacloud.com/help/en/model-studio/multimodal-embedding-api-reference
"""

import asyncio
import ssl
from concurrent.futures import as_completed
from itertools import chain
from typing import Optional

import aiohttp
import dashscope
import requests
from dashscope.api_entities.dashscope_response import DashScopeAPIResponse
from dashscope.common.constants import REQUEST_TIMEOUT_KEYWORD

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.ssl_utils import SslUtils
from openjiuwen.core.foundation.store.base_embedding import EmbeddingConfig
from openjiuwen.core.retrieval.common.callbacks import BaseCallback
from openjiuwen.core.retrieval.common.document import MultimodalDocument
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
from openjiuwen.core.retrieval.embedding.utils import SSLContextAdapter


class DashscopeEmbedding(APIEmbedding):
    """
    Dashscope embedding client, supports multimodal embedding in dashscope format
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
        Initialize Dashscope embedder.

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

        should_verify = bool(verify or self._verify_ssl)
        ssl_context = None
        self.req_session = requests.sessions.Session()
        if isinstance(verify, ssl.SSLContext):
            ssl_context = verify
            self.req_session.verify = True
            self.req_session.mount("https://", SSLContextAdapter(ssl_context))
        else:
            if isinstance(verify, str):
                ssl_context = SslUtils.create_strict_ssl_context(verify)
                self.req_session.verify = verify
            elif isinstance(self._verify_ssl, str):
                ssl_context = SslUtils.create_strict_ssl_context(self._verify_ssl)
                self.req_session.verify = self._verify_ssl
            self.req_session.verify = should_verify

        if ssl_context is not None:
            self.aio_connector = aiohttp.TCPConnector(verify_ssl=should_verify, ssl=ssl_context)
        else:
            self.aio_connector = None

        self._request_params = dict(model=self.model_name, api_key=self.api_key, base_address=self.api_url)
        self._request_params[REQUEST_TIMEOUT_KEYWORD] = self.timeout
        if isinstance(dimension, int):
            self.matryoshka_dimension = True
            self._dimension = dimension
            self._request_params["dimension"] = self._dimension

    def __del__(self):
        """Custom destructor to close at delete"""
        if self.aio_connector is not None:
            try:
                asyncio.create_task(self.aio_connector.close())
            except Exception:
                self.aio_connector = None
        if self.req_session is not None:
            try:
                self.req_session.close()
            except Exception:
                self.req_session = None
        super().__del__()

    async def embed_query(self, text: str | MultimodalDocument, **kwargs) -> list[float]:
        embeddings = await self.embed_documents([text], **kwargs)
        return embeddings[0]

    def embed_query_sync(self, text: str | MultimodalDocument, **kwargs) -> list[float]:
        embeddings = self.embed_documents_sync([text], **kwargs)
        return embeddings[0]

    async def embed_documents(
        self,
        texts: list[str | MultimodalDocument],
        batch_size: Optional[int] = None,
        **kwargs,
    ) -> list[list[float]]:
        non_empty = self.validate_embed_docs(texts, kwargs)
        non_empty = [doc.dashscope_input if isinstance(doc, MultimodalDocument) else doc for doc in non_empty]

        # Respect caller batch_size but never exceed configured max_batch_size
        bsz = batch_size or self.max_batch_size or 1
        if self.max_batch_size:
            bsz = min(bsz, self.max_batch_size)

        indices = list(range(0, len(non_empty), bsz))
        callback_obj = kwargs.get("callback_cls", BaseCallback)(seq=indices)

        async with aiohttp.ClientSession(connector=self.aio_connector) as session:

            async def process_batch(i: int) -> list[list[float]]:
                """Process a single batch with semaphore for concurrency control."""
                async with self.limiter:
                    j = i + bsz
                    batch = non_empty[i:j]
                    embeddings = await self._get_embeddings(batch, session=session, **kwargs)
                    callback_obj(start_idx=i, end_idx=j, batch=batch)
                    return embeddings

            # Create and run tasks for all batches concurrently
            tasks = [process_batch(i) for i in indices]
            results = await asyncio.gather(*tasks)

        return list(chain.from_iterable(results))

    def embed_documents_sync(
        self,
        texts: list[str | MultimodalDocument],
        batch_size: Optional[int] = None,
        **kwargs,
    ) -> list[list[float]]:
        """Embed document texts"""
        non_empty = self.validate_embed_docs(texts, kwargs)
        non_empty = [doc.dashscope_input if isinstance(doc, MultimodalDocument) else doc for doc in non_empty]

        # Respect caller batch_size but never exceed configured max_batch_size
        bsz = batch_size or self.max_batch_size or 1
        if self.max_batch_size:
            bsz = min(bsz, self.max_batch_size)

        indices = list(range(0, len(non_empty), bsz))
        callback_obj = kwargs.get("callback_cls", BaseCallback)(seq=indices)

        tasks = {}
        results = [None] * len(non_empty)

        for i in indices:
            j = i + bsz
            tasks[self.executor.submit(self._get_embeddings_sync, non_empty[i:j], **kwargs)] = i

        for task in as_completed(tasks):
            i = tasks.get(task)
            j = i + bsz
            results[i:j] = batch = task.result()
            callback_obj(start_idx=i, end_idx=j, batch=batch)

        return results

    async def embed_multimodal(self, doc: MultimodalDocument, **kwargs) -> list[float]:
        """Embed multimodal document"""
        if not isinstance(doc, MultimodalDocument):
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_INPUT_INVALID,
                error_msg="input provided for multimodal embedding is not a MultimodalDocument",
            )
        async with aiohttp.ClientSession(connector=self.aio_connector) as session:
            embeddings = await self._get_embeddings([doc.dashscope_input], session=session, **kwargs)
            return embeddings[0]

    def embed_multimodal_sync(self, doc: MultimodalDocument, **kwargs) -> list[float]:
        """Embed multimodal document"""
        if not isinstance(doc, MultimodalDocument):
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_INPUT_INVALID,
                error_msg="input provided for multimodal embedding is not a MultimodalDocument",
            )
        embeddings = self._get_embeddings_sync([doc.dashscope_input], **kwargs)
        return embeddings[0]

    def _handle_dashscope_api_resp(self, resp: DashScopeAPIResponse, attempt: int) -> Optional[list[list[float]]]:
        if resp.status_code != 200 and attempt >= self.max_retries - 1:
            error_msg = (
                f"DashscopeEmbedding request failed. "
                f"HTTP status: {resp.status_code}, "
                f"Error code: {resp.code}, "
                f"Error message: {resp.message}"
            )
            logger.warning(
                "Embedding request failed (attempt %s/%s): %s",
                attempt + 1,
                self.max_retries,
                error_msg,
            )
            if attempt >= self.max_retries - 1:
                raise build_error(
                    StatusCode.RETRIEVAL_EMBEDDING_REQUEST_CALL_FAILED,
                    error_msg=f"Failed to get embedding after {self.max_retries} attempts: {resp.message}",
                )
            return None
        result: dict = resp.output
        if "embeddings" in result:
            embeddings_response: list[dict] = result["embeddings"]
            if not embeddings_response:
                raise build_error(
                    StatusCode.RETRIEVAL_EMBEDDING_RESPONSE_INVALID,
                    error_msg=f"The embeddings field in response is empty: {result}",
                )
            embeddings_response.sort(key=lambda x: x.get("index", 0))
        else:
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_RESPONSE_INVALID,
                error_msg=f"No embeddings in response: {result}",
            )
        embeddings = [x.get("embedding") for x in embeddings_response]

        # If dimension not yet determined, get from result and cache
        if self._dimension is None and embeddings and embeddings[0]:
            self._dimension = len(embeddings[0])
            logger.debug(f"Determined embedding dimension: {self._dimension}")

        return embeddings

    async def _get_embeddings(self, text: list[str | dict], **kwargs) -> list[list[float]]:
        """Get embedding vectors"""
        kwargs.pop("callback_cls", None)
        payload_input = text if isinstance(text, list) else [text]
        payload = self._request_params | dict(input=payload_input) | kwargs

        for attempt in range(self.max_retries):
            resp = await dashscope.AioMultiModalEmbedding.call(**payload)
            embeddings = self._handle_dashscope_api_resp(resp, attempt)
            if embeddings is not None:
                return embeddings
        raise build_error(
            StatusCode.RETRIEVAL_EMBEDDING_UNREACHABLE_CALL_FAILED, error_msg="Unreachable code in _get_embeddings"
        )

    def _get_embeddings_sync(self, text: list[str | dict], **kwargs) -> list[list[float]]:
        """Get embedding vectors"""
        kwargs.pop("callback_cls", None)
        payload_input = text if isinstance(text, list) else [text]
        payload = self._request_params | dict(session=self.req_session, input=payload_input) | kwargs

        for attempt in range(self.max_retries):
            resp = dashscope.MultiModalEmbedding.call(**payload)
            embeddings = self._handle_dashscope_api_resp(resp, attempt)
            if embeddings is not None:
                return embeddings
        raise build_error(
            StatusCode.RETRIEVAL_EMBEDDING_UNREACHABLE_CALL_FAILED, error_msg="Unreachable code in _get_embeddings_sync"
        )
