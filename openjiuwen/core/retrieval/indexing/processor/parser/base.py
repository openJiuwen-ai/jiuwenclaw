# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Document Parser Abstract Base Class

Inherits from Processor, provides document parsing interface.
"""

from typing import Any, AsyncIterator, List, Optional

from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.base import Processor
from openjiuwen.core.foundation.llm.model import Model


class Parser(Processor):
    """Document parser abstract base class (inherits from Processor)"""

    async def parse(
        self, doc: str, doc_id: str = "", llm_client: Optional[Model] = None, **kwargs
    ) -> List[Document]:
        """
        Parse document

        Args:
            doc: Document source (file path, URL, etc.)
            doc_id: Document ID
            llm_client: Optional LLM client for captioning or other LLM-based processing
            **kwargs: Additional parameters

        Returns:
            Document list
        """
        content = await self._parse(doc, llm_client=llm_client)
        if content:
            return [Document(id_=doc_id, text=content, metadata={})]
        return []

    async def _parse(self, file_path: str, llm_client: Optional[Model] = None) -> Optional[str]:
        pass

    async def lazy_parse(self, doc: str, doc_id: str = "", **kwargs) -> AsyncIterator[Document]:
        """Default lazy loading implementation based on parse."""
        docs = await self.parse(doc, doc_id=doc_id, **kwargs)
        for d in docs:
            yield d

    async def process(self, *args: Any, **kwargs) -> Any:
        """Compatible with Processor abstract method, defaults to calling parse."""
        return await self.parse(*args, **kwargs)

    def supports(self, doc: str) -> bool:
        """
        Check if the document source is supported

        Args:
            doc: Document source

        Returns:
            Whether it is supported
        """
        return False
