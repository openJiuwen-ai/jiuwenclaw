# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Multimodal vLLM Embedding Model Implementation

Multimodal embedding client implementation for vLLM-like services.
"""

from typing import Any, List

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.retrieval.common.document import MultimodalDocument
from openjiuwen.core.retrieval.embedding.openai_embedding import OpenAIEmbedding


class VLLMEmbedding(OpenAIEmbedding):
    """
    vLLM embedding client, supports multimodal embedding models like Qwen3-VL-Embedding
    """

    @staticmethod
    def parse_multimodal_input(doc: MultimodalDocument, kwargs: dict[str, Any]) -> dict[str, Any]:
        """Parse multimodal input, mutate kwargs in-place and return kwargs"""
        instruction = kwargs.pop("instruction", "Represent the user's input.")
        messages = [{"role": "user", "content": doc.content}]
        if instruction is not None:
            messages.insert(0, {"role": "system", "content": [{"type": "text", "text": instruction}]})
        kwargs["extra_body"] = {"messages": messages}
        return kwargs

    async def embed_multimodal(self, doc: MultimodalDocument, **kwargs) -> List[float]:
        """Embed multimodal document"""
        if not isinstance(doc, MultimodalDocument):
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_INPUT_INVALID,
                error_msg="input provided for multimodal embedding is not a MultimodalDocument",
            )
        kwargs = self.parse_multimodal_input(doc, kwargs)
        return (await self._get_embeddings(None, **kwargs))[0]

    def embed_multimodal_sync(self, doc: MultimodalDocument, **kwargs) -> List[float]:
        """Embed multimodal document"""
        if not isinstance(doc, MultimodalDocument):
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_INPUT_INVALID,
                error_msg="input provided for multimodal embedding is not a MultimodalDocument",
            )
        kwargs = self.parse_multimodal_input(doc, kwargs)
        return self._get_embeddings_sync(None, **kwargs)[0]
