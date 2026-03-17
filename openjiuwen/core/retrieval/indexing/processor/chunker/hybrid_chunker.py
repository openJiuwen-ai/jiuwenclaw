# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Hybrid Chunker

Uses a hybrid strategy: documents matching ``no_split_when(doc)`` are kept as one chunk,
while all other documents are delegated to an inner chunker.
Default predicate treats table units (Excel/CSV row/column,
``metadata.source_type in ("row", "column")``) as one chunk.
"""

import uuid
from typing import Callable, List, Optional

from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker


def _default_no_split(doc: Document) -> bool:
    """Treat as single chunk when source_type is row or column (table unit)."""
    return (doc.metadata or {}).get("source_type") in ("row", "column")


class HybridChunker(Chunker):
    """
    Wraps an inner chunker: docs for which ``no_split_when(doc)`` is True are emitted as one
    chunk; others are delegated to the inner chunker.

    **SDK usage**::

        from openjiuwen.core.retrieval.indexing.processor.chunker import get_chunker

        # Default: table row/column docs stay as one chunk, others use CharChunker
        chunker = get_chunker("hybrid", chunk_size=512, chunk_overlap=50)

        # Custom inner chunker
        chunker = get_chunker("hybrid", inner_chunker=TokenizerChunker(...))

        # Custom predicate
        chunker = get_chunker("hybrid", no_split_when=lambda doc: ...)

    **Contributing a new chunker**:
    Create a new file (e.g. ``chunker/semantic_chunker.py``), subclass :class:`Chunker`,
    then register it in ``chunker/__init__.py``::

        register_chunker("semantic", SemanticChunker)
    """

    def __init__(
        self,
        inner_chunker: Chunker,
        no_split_when: Optional[Callable[[Document], bool]] = None,
        **kwargs,
    ):
        super().__init__(
            chunk_size=getattr(inner_chunker, "chunk_size", 512),
            chunk_overlap=getattr(inner_chunker, "chunk_overlap", 50),
            **kwargs,
        )
        self._inner = inner_chunker
        self._no_split_when = no_split_when or _default_no_split

    def chunk_text(self, text: str) -> List[str]:
        return self._inner.chunk_text(text)

    def chunk_documents(self, documents: List[Document]) -> List[TextChunk]:
        chunks: List[TextChunk] = []
        for doc in documents:
            if self._no_split_when(doc) and (doc.text or "").strip():
                uid = str(uuid.uuid4())
                chunks.append(
                    TextChunk(
                        id_=uid,
                        text=doc.text.strip(),
                        doc_id=doc.id_,
                        metadata={
                            **(doc.metadata or {}),
                            "chunk_index": 0,
                            "total_chunks": 1,
                            "chunk_id": uid,
                        },
                    )
                )
            else:
                chunks.extend(self._inner.chunk_documents([doc]))
        return chunks


