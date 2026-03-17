# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Fixed Size Chunker Implementation

A simple text chunker based on character length.
"""

from typing import Any, List

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.chunker.text_splitter import IndexSentenceSplitter


class TokenizerChunker(Chunker):
    """Fixed size chunker based on tokens"""

    def __init__(
        self,
        chunk_size: int,
        chunk_overlap: int,
        tokenizer: Any,
        language: str = "auto",
        splitter_config: dict | None = None,
        **kwargs,
    ):
        """
        Initialize fixed size chunker

        Args:
            chunk_size: Chunk size (number of characters)
            chunk_overlap: Chunk overlap size (number of characters)
            length_function: Length calculation function (defaults to character count)
            language: Language code, defaults to "auto" (auto-detect)
            splitter_config: Other arguments to SentenceSplitter. Defaults to None.
        """
        super().__init__(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            **kwargs,
        )
        self.tokenizer = tokenizer
        self.splitter = IndexSentenceSplitter(
            tokenizer=self.tokenizer,
            chunk_size=self.chunk_size,
            chunk_overlap=self.chunk_overlap,
            splitter_config=splitter_config,
            language=language,
        )

    def chunk_text(self, text: str) -> List[str]:
        """
        Chunk text

        Args:
            text: Text to be chunked

        Returns:
            List of chunked texts
        """
        if not text:
            return []

        doc = Document(text=text, metadata={})
        text_nodes = self.splitter.split(doc)
        chunks = []
        for node in text_nodes:
            chunks.append(node.text)

        logger.info("Token chunking completed: generated %d chunks", len(chunks))
        return chunks
