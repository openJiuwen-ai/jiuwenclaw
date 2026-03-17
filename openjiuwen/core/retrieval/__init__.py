# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Retrieval module, supporting knowledge base management, document indexing, embedding generation,
vector search, and multi-strategy retrieval (vector, sparse, hybrid, graph, and agentic).

Lazy-loading module using PEP 562 __getattr__ to avoid unnecessary import of heavy dependencies.
"""

import importlib
from typing import TYPE_CHECKING

# Knowledge base implementations
# Common data models and configs
from openjiuwen.core.retrieval.common.callbacks import BaseCallback, TqdmCallback
from openjiuwen.core.retrieval.common.config import (
    EmbeddingConfig,
    IndexConfig,
    KnowledgeBaseConfig,
    RerankerConfig,
    RetrievalConfig,
    VectorStoreConfig,
)
from openjiuwen.core.retrieval.common.document import Document, MultimodalDocument, TextChunk
from openjiuwen.core.retrieval.common.retrieval_result import MultiKBRetrievalResult, RetrievalResult, SearchResult
from openjiuwen.core.retrieval.common.triple import Triple
from openjiuwen.core.retrieval.common.triple_beam import TripleBeam
from openjiuwen.core.retrieval.common.triple_memory import TripleMemory

# Embedding related
from openjiuwen.core.retrieval.embedding.api_embedding import APIEmbedding
from openjiuwen.core.retrieval.embedding.base import Embedding

# Indexer related
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer

# Processor & Chunker classes
from openjiuwen.core.retrieval.indexing.processor.base import Processor
from openjiuwen.core.retrieval.indexing.processor.chunker import (
    HybridChunker,
    get_chunker,
    register_chunker,
)
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.chunker.char_chunker import CharChunker
from openjiuwen.core.retrieval.indexing.processor.chunker.chunking import TextChunker
from openjiuwen.core.retrieval.indexing.processor.chunker.text_preprocessor import (
    PreprocessingPipeline,
    SpecialCharacterNormalizer,
    TextPreprocessor,
    URLEmailRemover,
    WhitespaceNormalizer,
)
from openjiuwen.core.retrieval.indexing.processor.chunker.text_splitter import (
    CharSplitter,
    IndexSentenceSplitter,
    TextSplitter,
)
from openjiuwen.core.retrieval.indexing.processor.chunker.tokenizer_chunker import TokenizerChunker

# Extractor implementations
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor
from openjiuwen.core.retrieval.indexing.processor.extractor.triple_extractor import TripleExtractor

# Splitter implementations
from openjiuwen.core.retrieval.indexing.processor.splitter.base import Splitter
from openjiuwen.core.retrieval.indexing.processor.splitter.splitter import SentenceSplitter

# Reranker related
from openjiuwen.core.retrieval.reranker.base import Reranker

# Retriever related
from openjiuwen.core.retrieval.retriever.agentic_retriever import AgenticRetriever
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
from openjiuwen.core.retrieval.retriever.hybrid_retriever import HybridRetriever
from openjiuwen.core.retrieval.retriever.sparse_retriever import SparseRetriever
from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever

# Utilities
from openjiuwen.core.retrieval.utils.common import deduplicate
from openjiuwen.core.retrieval.utils.config_manager import ConfigManager
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion

# Vector store related
from openjiuwen.core.retrieval.vector_store.base import VectorStore
from openjiuwen.core.retrieval.vector_store.store import create_vector_store

from .lazy_load import _LAZY_ATTRIBUTES, _LAZY_IMPORT_CACHE, lazy_load

if TYPE_CHECKING:
    # Lazy-loaded imports for type checking / IDE hinting
    from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField
    from openjiuwen.core.foundation.store.vector_fields.milvus_fields import (
        MilvusAUTO,
        MilvusFLAT,
        MilvusHNSW,
        MilvusIVF,
        MilvusSCANN,
    )
    from openjiuwen.core.retrieval.embedding.openai_embedding import OpenAIEmbedding
    from openjiuwen.core.retrieval.embedding.utils import parse_base64_embedding
    from openjiuwen.core.retrieval.embedding.vllm_embedding import VLLMEmbedding
    from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase
    from openjiuwen.core.retrieval.indexing.indexer.chroma_indexer import ChromaIndexer
    from openjiuwen.core.retrieval.indexing.indexer.milvus_indexer import MilvusIndexer
    from openjiuwen.core.retrieval.indexing.processor import parser
    from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase
    from openjiuwen.core.retrieval.query_rewriter.query_rewriter import QueryRewriter
    from openjiuwen.core.retrieval.reranker.chat_reranker import ChatReranker
    from openjiuwen.core.retrieval.reranker.standard_reranker import StandardReranker
    from openjiuwen.core.retrieval.simple_knowledge_base import (
        SimpleKnowledgeBase,
        retrieve_multi_kb,
        retrieve_multi_kb_with_source,
    )
    from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore
    from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore

_NON_LAZY_ATTRIBUTES = [
    # Common classes
    "KnowledgeBaseConfig",
    "RetrievalConfig",
    "IndexConfig",
    "VectorStoreConfig",
    "EmbeddingConfig",
    "RerankerConfig",
    "Document",
    "MultimodalDocument",
    "TextChunk",
    "MultiKBRetrievalResult",
    "RetrievalResult",
    "SearchResult",
    "Triple",
    "TripleBeam",
    "TripleMemory",
    "BaseCallback",
    "TqdmCallback",
    # Embedding / Reranker / Vector Store / Indexer classes
    "Embedding",
    "APIEmbedding",
    "Reranker",
    "VectorStore",
    "create_vector_store",
    "Indexer",
    # Processor classes
    "Processor",
    "Chunker",
    "Extractor",
    "Splitter",
    "SentenceSplitter",
    "TextSplitter",
    "CharSplitter",
    "IndexSentenceSplitter",
    "TextPreprocessor",
    "WhitespaceNormalizer",
    "URLEmailRemover",
    "SpecialCharacterNormalizer",
    "PreprocessingPipeline",
    "TextChunker",
    "CharChunker",
    "HybridChunker",
    "get_chunker",
    "register_chunker",
    "TokenizerChunker",
    "TripleExtractor",
    # Retriever classes
    "Retriever",
    "VectorRetriever",
    "SparseRetriever",
    "HybridRetriever",
    "GraphRetriever",
    "AgenticRetriever",
    # Utils
    "ConfigManager",
    "rrf_fusion",
    "deduplicate",
]


def __getattr__(name: str):
    """
    Lazy import for heavy dependencies using PEP 562.
    """
    if name in _NON_LAZY_ATTRIBUTES:
        return importlib.import_module("." + name, __name__)
    attr = _LAZY_IMPORT_CACHE.get(name) or lazy_load(name)
    if attr is not None:
        return attr

    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")


__all__ = _NON_LAZY_ATTRIBUTES + _LAZY_ATTRIBUTES
