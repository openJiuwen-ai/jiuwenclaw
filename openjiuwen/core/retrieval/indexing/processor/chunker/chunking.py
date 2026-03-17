# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import uuid
from typing import Any, Dict, List, Optional

import tiktoken

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker import CharChunker
from openjiuwen.core.retrieval.indexing.processor.chunker.text_preprocessor import (
    PreprocessingPipeline,
    URLEmailRemover,
    WhitespaceNormalizer,
)
from openjiuwen.core.retrieval.indexing.processor.chunker.tokenizer_chunker import TokenizerChunker


class TextChunker(Chunker):
    """Fixed size chunker based on character length"""

    def __init__(
        self,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        chunk_unit: str = "char",
        embed_model: Optional[Any] = None,
        preprocess_options: Optional[Dict] = None,
        **kwargs,
    ):
        """
        Initialize fixed size chunker

        Args:
            chunk_size: Chunk size (number of characters)
            chunk_overlap: Chunk overlap size (number of characters)
        """
        super().__init__(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap,
            **kwargs,
        )
        preprocessors = []
        preprocess_options = preprocess_options or {}
        if preprocess_options.get("normalize_whitespace"):
            preprocessors.append(WhitespaceNormalizer())
        if preprocess_options.get("remove_url_email"):
            preprocessors.append(URLEmailRemover())
        self.pipeline = PreprocessingPipeline(preprocessors)
        self.chunker = self.get_chunker(chunk_size, chunk_overlap, chunk_unit, embed_model)

    def get_chunker(self, chunk_size: int, chunk_overlap: int, chunk_unit: str, embed_model: Optional[Any]):
        if chunk_unit == "char":
            return CharChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap)
        else:
            tokenizer = None
            if embed_model is not None:
                tokenizer = getattr(embed_model, "tokenizer", None)

            # If embed_model doesn't have a tokenizer, try to use tiktoken
            if tokenizer is None:
                if tiktoken is None:
                    raise build_error(
                        StatusCode.RETRIEVAL_INDEXING_TOKENIZER_PROCESS_ERROR,
                        error_msg="chunk_unit='token' requires embed_model with tokenizer or tiktoken to be installed",
                    )
                try:
                    tokenizer = tiktoken.get_encoding("cl100k_base")
                    logger.info("Using tiktoken(cl100k_base) as tokenizer")
                except Exception as exc:
                    raise build_error(
                        StatusCode.RETRIEVAL_INDEXING_TOKENIZER_PROCESS_ERROR,
                        error_msg=f"Failed to load tokenizer for token-based chunking: {exc}",
                        cause=exc,
                    ) from exc

            # Check if chunk_size needs adjustment
            if (
                hasattr(tokenizer, "model_max_length")
                and tokenizer.model_max_length < float("inf")
                and chunk_size > tokenizer.model_max_length
            ):
                original_size = chunk_size
                chunk_size = tokenizer.model_max_length
                logger.warning(
                    "chunk_size (%d) exceeds tokenizer limit, automatically adjusted to: %d",
                    original_size,
                    chunk_size,
                )

            return TokenizerChunker(chunk_size=chunk_size, chunk_overlap=chunk_overlap, tokenizer=tokenizer)

    def chunk_documents(self, documents: List[Document]) -> List[TextChunk]:
        """
        Chunk document list

        Args:
            documents: List of documents

        Returns:
            List of document chunks
        """
        chunks = []
        for doc in documents:
            doc_text = self.pipeline(doc.text)
            texts = self.chunker.chunk_text(doc_text)
            for i, text in enumerate(texts):
                uid = str(uuid.uuid4())
                chunk = TextChunk(
                    id_=uid,
                    text=text,
                    doc_id=doc.id_,
                    metadata={
                        **doc.metadata,
                        "chunk_index": i,
                        "total_chunks": len(texts),
                        "chunk_id": uid,
                    },
                )
                chunks.append(chunk)
        return chunks
