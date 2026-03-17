# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Standard Reranker Model Implementation

Reranker client implementation for vLLM-like services
"""

import ssl
from typing import Optional

import httpx

from openjiuwen import __version__
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.base_reranker import Document, Reranker, RerankerConfig
from openjiuwen.core.retrieval.common.document import MultimodalDocument
from openjiuwen.core.retrieval.utils.api_requests import async_request_with_retry, sync_request_with_retry


class StandardReranker(Reranker):
    """
    Standard reranker client, supports rerank API of vLLM-like services
    """

    end_point = "/rerank"
    query_template = "<Instruct>: {instruct}\n<Query>: {query}\n"
    default_instruct = "Given a search query, retrieve relevant candidates that answer the query."

    def __init__(
        self,
        config: RerankerConfig,
        max_retries: int = 3,
        retry_wait: float = 0.1,
        extra_headers: Optional[dict] = None,
        verify: bool | str | ssl.SSLContext = True,
        **kwargs,
    ):
        self.config = config
        self.model_name = config.model_name
        self.api_key = config.api_key
        self.api_url = (config.api_base or "").removesuffix("/").removesuffix(self.end_point)
        self.timeout = config.timeout
        self.max_retries = max_retries
        self._headers = {"Content-Type": "application/json"}
        if self.api_key:
            self._headers["Authorization"] = f"Bearer {self.api_key}"
        if extra_headers:
            self._headers.update(extra_headers)

        # Create clients
        client_kwargs = dict(verify=verify, timeout=self.timeout, base_url=self.api_url) | kwargs
        self.client = httpx.AsyncClient(**client_kwargs)
        self.sync_client = httpx.Client(**client_kwargs)

    async def rerank(
        self, query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs
    ) -> dict[str, float]:
        headers, params = self._assemble_params(query, doc, instruct, kwargs)
        result = await async_request_with_retry(
            self.client, max_retries=self.max_retries, task="Reranker", url=self.end_point, json=params, headers=headers
        )
        return self._parse_response(result, doc=doc)

    def rerank_sync(
        self, query: str, doc: list[str | Document], instruct: bool | str = True, **kwargs
    ) -> dict[str, float]:
        headers, params = self._assemble_params(query, doc, instruct, kwargs)
        result = sync_request_with_retry(
            self.sync_client,
            max_retries=self.max_retries,
            task="Reranker",
            url=self.end_point,
            json=params,
            headers=headers,
        )
        return self._parse_response(result, doc=doc)

    def _parse_response(self, response_data: dict, doc: list[str | Document]) -> dict[str, float]:
        doc_ids = [d if isinstance(d, str) else d.id_ for d in doc]
        result_dict = dict.fromkeys(doc_ids, 0.0)
        results = response_data.get("output", response_data)["results"]
        for rank_result in results:
            doc = doc_ids[rank_result["index"]]
            result_dict[doc] = rank_result["relevance_score"]
        return result_dict

    def _request_headers(self, **kwargs: dict) -> dict:
        return self._headers

    def _request_params(self, **kwargs: dict) -> dict:
        instruct = kwargs.pop("instruct", None)
        if instruct is True:
            query = self.query_template.format(query=kwargs.pop("query"), instruct=self.default_instruct)
        elif instruct:
            query = self.query_template.format(query=kwargs.pop("query"), instruct=instruct)
        else:
            query = kwargs.pop("query")
        return dict(model=self.model_name, return_documents=False, query=query) | self.config.extra_body | kwargs

    def _assemble_params(
        self, query: str, doc: list[str | Document], instruct: bool | str, kwargs: dict
    ) -> tuple[dict, dict]:
        documents = None
        if isinstance(doc, list):
            if all(isinstance(d, (str, Document)) for d in doc):
                documents = [d if isinstance(d, str) else d.text for d in doc]
                if any(isinstance(d, MultimodalDocument) for d in doc):
                    logger.warning(
                        "Reranker received a multimodal reranking request, not supported in openJiuwen %s", __version__
                    )
        if documents is None:
            raise build_error(
                StatusCode.RETRIEVAL_RERANKER_INPUT_INVALID,
                error_msg="input to reranker must be either list[str | Document]",
            )
        headers = self._request_headers()
        params = self._request_params(query=query, documents=documents, top_n=len(documents), instruct=instruct)
        return headers, params
