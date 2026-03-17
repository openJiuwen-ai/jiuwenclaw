# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Chat Reranker Model Implementation

Reranker client implementation for chat completion services that supply logprobs
"""

import math
import ssl
from typing import Optional, Sequence

from openjiuwen import __version__
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.base_reranker import Document, RerankerConfig
from openjiuwen.core.retrieval.reranker.standard_reranker import StandardReranker
from openjiuwen.core.retrieval.utils.api_requests import async_request_with_retry, sync_request_with_retry


class ChatReranker(StandardReranker):
    """
    Chat-based reranker client, supports any chat completion API that provide logprobs
    """

    end_point = "/chat/completions"
    doc_template = "<Document>: {doc}"
    system_instruct = "Judge whether the Document meets the requirements based on the Query and the Instruct provided. "
    'Note that the answer can only be "yes" or "no".'

    def __init__(
        self,
        config: RerankerConfig,
        max_retries: int = 3,
        retry_wait: float = 0.1,
        extra_headers: Optional[dict] = None,
        verify: bool | str | ssl.SSLContext = True,
        **kwargs,
    ):
        logger.warning("ChatReranker support is experimental in openJiuwen %s, you have been warned.", __version__)
        if isinstance(config.yes_no_ids, Sequence) and sum(isinstance(tid, int) for tid in config.yes_no_ids) == 2:
            self.yes_no_ids = tuple(config.yes_no_ids)
        else:
            raise build_error(
                StatusCode.RETRIEVAL_RERANKER_INPUT_INVALID,
                error_msg='chat reranker require "yes_no_ids" to be specified in RerankerConfig',
            )
        super().__init__(config, max_retries, retry_wait, extra_headers, verify, **kwargs)

    def test_compatibility(self) -> bool:
        """Test to see if selected service is compatible for chat-completion-based reranking"""
        try:
            self.rerank_sync("test", doc=["test"], instruct=False)
        except Exception as e:
            logger.error("The selected service does not support chat-completion-based reranking: %r", e)
            return False
        return True

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
        yes_scores, no_scores = [0], [0]

        # Gather the logprob of returned tokens
        response_data = response_data.get("choices", [{}])[0]
        logprobs = response_data.get("logprobs")
        if not (logprobs and len(logprobs) > 0):
            raise build_error(
                StatusCode.RETRIEVAL_RERANKER_REQUEST_CALL_FAILED,
                error_msg="the service does not support logprobs for chat reranker to function",
            )
        for token in logprobs.get("content", logprobs)[0].get("top_logprobs", []):
            token_text = token["token"].strip().casefold()
            if token_text.startswith("yes"):
                yes_scores.append(math.exp(token["logprob"]))
            elif token_text.startswith("no"):
                no_scores.append(math.exp(token["logprob"]))

        confidence = max(yes_scores)
        total_prob = confidence + max(no_scores)
        doc_id = doc[0].id_ if isinstance(doc[0], Document) else doc[0]

        if total_prob == 0:
            return {doc_id: 0.0}
        return {doc_id: confidence / total_prob}

    def _assemble_params(
        self, query: str, doc: list[str | Document], instruct: bool | str, kwargs: dict
    ) -> tuple[dict, dict]:
        documents = None
        if isinstance(doc, list):
            if len(doc) == 1 and all(isinstance(d, (str, Document)) for d in doc):
                documents = [d if isinstance(d, str) else d.text for d in doc]
        if documents is None:
            raise build_error(
                StatusCode.RETRIEVAL_RERANKER_INPUT_INVALID,
                error_msg="input to chat reranker must be a list[str | Document] of size 1",
            )
        headers = self._request_headers()
        params = self._request_params(query=query, documents=documents, top_n=len(documents), instruct=instruct)
        return headers, params

    def _request_params(self, **kwargs) -> dict:
        doc = kwargs.pop("documents")[0]
        instruct = kwargs.pop("instruct", None)
        if isinstance(instruct, str) and instruct:
            content = self.query_template.format(query=kwargs.pop("query"), instruct=instruct)
        else:
            content = self.query_template.format(query=kwargs.pop("query"), instruct=self.default_instruct)
        content += self.doc_template.format(doc=doc)
        messages = [dict(role="system", content=self.system_instruct), dict(role="user", content=content)]
        params = {
            "model": self.model_name,
            "messages": messages,
            "temperature": 0,
            "max_tokens": 1,
            "logprobs": True,
            "top_logprobs": 5,
            "logit_bias": dict.fromkeys(self.yes_no_ids, 5),
            **self.config.extra_body,
        }
        return params
