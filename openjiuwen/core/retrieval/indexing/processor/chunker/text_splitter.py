# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from abc import ABCMeta, abstractmethod
from typing import TYPE_CHECKING, Any, Callable, Tuple, Union

from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.indexing.processor.splitter.splitter import SentenceSplitter

if TYPE_CHECKING:
    from transformers import PreTrainedTokenizerBase

DEFAULT_CHUNK_SIZE = 200
DEFAULT_CHAR_CHUNK_SIZE = 200
DEFAULT_CHAR_CHUNK_OVERLAP = 40


class TextSplitter(metaclass=ABCMeta):
    """Abstract base class for text splitters"""

    @abstractmethod
    def split(self, doc: TextChunk | Document) -> list[TextChunk]:
        """Split document into text chunks"""


class CharSplitter(TextSplitter):
    """Simple text splitter based on character length, no dependency on tokenizer."""

    def __init__(self, chunk_size: int | None = None, chunk_overlap: int | None = None) -> None:
        super().__init__()
        size = chunk_size or DEFAULT_CHAR_CHUNK_SIZE
        overlap = chunk_overlap if chunk_overlap is not None else DEFAULT_CHAR_CHUNK_OVERLAP
        # Limit range to avoid step becoming 0 or negative
        overlap = max(0, min(overlap, size - 1))
        self.chunk_size = max(1, size)
        self.chunk_overlap = overlap

    def split(self, doc: TextChunk | Document) -> list[TextChunk]:
        text = doc.text or ""
        # Keep metadata and exclusion fields for subsequent indexing/deletion
        doc_id = doc.id_
        meta = doc.metadata or {}

        res: list[TextChunk] = []
        step = self.chunk_size - self.chunk_overlap if self.chunk_size > self.chunk_overlap else self.chunk_size
        start = 0
        while start < len(text):
            end = min(len(text), start + self.chunk_size)
            res.append(
                TextChunk(
                    id_=str(uuid.uuid4()),
                    text=text[start:end],
                    doc_id=doc_id,
                    metadata=meta,
                )
            )
            start += step
        return res


class IndexSentenceSplitter(TextSplitter):
    """
    SentenceSplitter wrapper with sentence splitting capabilities.
    """

    def __init__(
        self,
        tokenizer: Union["PreTrainedTokenizerBase", Any] = None,
        chunk_size: int | None = None,
        chunk_overlap: int | None = None,
        splitter_config: dict | None = None,
        language: str = "auto",
    ) -> None:
        """Wrapper with sentence splitting capabilities.

        Args:
            tokenizer (PreTrainedTokenizerBase): Tokenizer.
            chunk_size (int | None, optional): Chunk size to split documents into passages. Defaults to None.
                Note: this is based on tokens produced by the tokenizer of embedding model.
                If None, set to the maximum sequence length of the embedding model.
            chunk_overlap (int | None, optional): Window size for passage overlap. Defaults to None.
                If None, set to `chunk_size // 5`.
            splitter_config (dict, optional): Other arguments to SentenceSplitter. Defaults to None.
            language: Language code, defaults to "auto" (auto-detect)
        """
        super().__init__()
        self._tokenizer = tokenizer

        if not isinstance(splitter_config, dict):
            splitter_config = {}

        _, max_token_length = self._resolve_tokenizer(self._tokenizer)
        chunk_size = self._resolve_chunk_size(chunk_size, max_token_length)

        self._splitter = SentenceSplitter(
            chunk_size=chunk_size,
            chunk_overlap=chunk_overlap or chunk_size // 5,
            tokenizer=tokenizer,
            lan=language,
            **splitter_config,
        )

    @staticmethod
    def _resolve_tokenizer(
        tokenizer: Union["PreTrainedTokenizerBase", Any],
    ) -> Tuple[Callable[[str], list], int | None]:
        """
        Return a tokenizer callable and its max token length (if known).
        Falls back to tiktoken when the embedding model has no tokenizer.
        """
        # Prefer tokenizer.tokenize / tokenizer.encode when available
        if tokenizer is not None:
            if hasattr(tokenizer, "tokenize"):
                return tokenizer.tokenize, IndexSentenceSplitter._max_length(tokenizer)
            if hasattr(tokenizer, "encode"):
                return tokenizer.encode, IndexSentenceSplitter._max_length(tokenizer)
            if callable(tokenizer):
                return tokenizer, IndexSentenceSplitter._max_length(tokenizer)

        # Fallback: tiktoken (pulls encoding data from HuggingFace if needed)
        try:
            import tiktoken

            encoding = tiktoken.get_encoding("cl100k_base")
            return encoding.encode, getattr(encoding, "max_token_value", None)
        except Exception as exc:  # pragma: no cover - unexpected failure
            logger.warning("Failed to load tiktoken fallback tokenizer: %s", exc)
            return lambda text: text.split(), None

    @staticmethod
    def _max_length(tokenizer: Any) -> int | None:
        """Try to infer a reasonable maximum token length from a tokenizer."""
        for attr in (
            "model_max_length",
            "max_len_single_sentence",
            "max_position_embeddings",
            "max_seq_length",
        ):
            if hasattr(tokenizer, attr):
                try:
                    val = int(getattr(tokenizer, attr))
                    if val and val != float("inf"):
                        return val
                except Exception:
                    logger.warning("Failed to get max length", exc_info=True)
                    continue
        return None

    @staticmethod
    def _resolve_chunk_size(
        chunk_size: int | None,
        max_token_length: int | None,
    ) -> int:
        """
        Decide chunk_size based on caller input and tokenizer limits.
        """
        if chunk_size is None and max_token_length:
            return max_token_length
        if chunk_size is not None and max_token_length:
            return min(chunk_size, max_token_length)
        return chunk_size or DEFAULT_CHUNK_SIZE

    def split(self, doc: TextChunk | Document) -> list[TextChunk]:
        # Note: we don't want to consider the length of metadata for chunking
        if isinstance(doc, Document):
            node = doc
        else:
            node = Document(
                id_=doc.doc_id,
                text=doc.text,
                metadata=doc.metadata,
            )

        return self._splitter.get_nodes_from_documents([node])
