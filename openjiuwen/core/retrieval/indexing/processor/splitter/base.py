# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Text Splitter Abstract Base Class

Provides unified interface for text splitting, subclasses need to implement specific splitting logic.
"""

from abc import ABC, abstractmethod
from typing import Callable, List, Optional, Tuple

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import Document, TextChunk


class Splitter(ABC):
    """Text splitter abstract base class"""

    def __init__(
        self,
        tokenizer: Optional[Callable] = None,
        chunk_size: int = 512,
        chunk_overlap: int = 50,
        **kwargs,
    ):
        """
        Initialize text splitter

        Args:
            tokenizer: Tokenizer, must have encode and decode methods
            chunk_size: Chunk size (number of tokens or characters)
            chunk_overlap: Chunk overlap size
            **kwargs: Other parameters
        """
        if chunk_size <= 0:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_CHUNK_SIZE_INVALID,
                error_msg=f"chunk_size must be greater than 0, current value: {chunk_size}",
            )
        if chunk_overlap < 0:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_CHUNK_OVERLAP_INVALID,
                error_msg=f"chunk_overlap must be greater than or equal to 0, current value: {chunk_overlap}",
            )
        if chunk_overlap >= chunk_size:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_CHUNK_OVERLAP_INVALID,
                error_msg=f"chunk_overlap ({chunk_overlap}) must be less than chunk_size ({chunk_size})",
            )

        self.chunk_size = chunk_size
        self.chunk_overlap = chunk_overlap

        if tokenizer is not None:
            self._validate_tokenizer(tokenizer)
            self.tokenizer = tokenizer
            if hasattr(tokenizer, "encode") and hasattr(tokenizer, "decode"):
                self.tokenizer_enc = tokenizer.encode
                self.tokenizer_dec = tokenizer.decode
            else:
                self.tokenizer_enc = tokenizer
                self.tokenizer_dec = None
        else:
            self.tokenizer = None
            self.tokenizer_enc = None
            self.tokenizer_dec = None

    def _validate_tokenizer(self, tokenizer: Callable) -> None:
        """
        Validate if tokenizer is valid

        Args:
            tokenizer: Tokenizer object

        Raises:
            ValueError: If tokenizer is invalid
        """
        if tokenizer is None:
            return

        # Check if has encode method or is callable
        if not (hasattr(tokenizer, "encode") or callable(tokenizer)):
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_TOKENIZER_PROCESS_ERROR,
                error_msg="Tokenizer must have encode method or be callable",
            )

    @abstractmethod
    def __call__(self, doc: str) -> List[Tuple[str, int, int]]:
        """
        Split document, return list of (text, start position, end position) tuples

        Args:
            doc: Document text to be split

        Returns:
            List of chunks, each element is (text, start char position, end char position)
        """

    def get_nodes_from_documents(self, docs: List[Document]) -> List[TextChunk]:
        """
        Get split nodes from document list

        Args:
            docs: List of documents

        Returns:
            List of split text chunks
        """
        returned_nodes = []
        for doc in docs:
            if not doc or not hasattr(doc, "text") or not doc.text:
                logger.warning(f"Skipping empty document: {doc}")
                continue

            chunk_tuples = self.__call__(doc.text)

            for chunk_text, start_idx, end_idx in chunk_tuples:
                _node = TextChunk.from_document(doc, chunk_text)
                returned_nodes.append(_node)

        logger.info(f"Generated {len(returned_nodes)} text chunks from {len(docs)} documents")
        return returned_nodes

    def split_text(self, text: str) -> List[str]:
        """
        Split text, return only text list (without position info)

        Args:
            text: Text to be split

        Returns:
            List of split texts
        """
        chunks = self.__call__(text)
        return [chunk[0] for chunk in chunks]

    def _get_token_count(self, text: str) -> int:
        """
        Get token count of text

        Args:
            text: Text content

        Returns:
            Token count, returns character count if no tokenizer
        """
        if self.tokenizer_enc is not None:
            tokens = self.tokenizer_enc(text)
            return len(tokens) if isinstance(tokens, (list, tuple)) else len(str(tokens))
        return len(text)
