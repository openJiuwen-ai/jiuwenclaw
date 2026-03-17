# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Extractor Abstract Base Class

Inherits from Processor, used for extracting triples, etc.
"""

from abc import abstractmethod
from typing import List

from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.common.triple import Triple
from openjiuwen.core.retrieval.indexing.processor.base import Processor


class Extractor(Processor):
    """Extractor abstract base class (inherits from Processor, used for extracting triples, etc.)"""

    @abstractmethod
    async def extract(
        self,
        chunks: List[TextChunk],
        **kwargs,
    ) -> List[Triple]:
        """
        Extract information (e.g., triples)

        Args:
            chunks: Text chunk list
            **kwargs: Additional parameters

        Returns:
            Extraction result list (e.g., triple list)
        """

    async def process(self, chunks: List[TextChunk], **kwargs) -> List[Triple]:
        """
        Process text chunks (implements Processor's process method)

        Args:
            chunks: Text chunk list
            **kwargs: Additional parameters

        Returns:
            Extraction result list
        """
        return await self.extract(chunks, **kwargs)
