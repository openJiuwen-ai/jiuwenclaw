# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Lazy loading for attributes in retrieval

Lazy loading helper for classes / functions involving heavy dependencies, used in __init__.py
"""

from typing import Optional

__all__ = ["_LAZY_ATTRIBUTES", "_LAZY_IMPORT_CACHE", "lazy_load"]


_LAZY_MILVUS = [
    "MilvusAUTO",
    "MilvusFLAT",
    "MilvusHNSW",
    "MilvusIVF",
    "MilvusSCANN",
    "MilvusVectorStore",
    "MilvusIndexer",
]
_LAZY_CHROMA = ["ChromaIndexer", "ChromaVectorStore", "ChromaVectorField"]
_LAZY_OPENAI = ["OpenAIEmbedding", "VLLMEmbedding", "parse_base64_embedding"]
_LAZY_HTTPX = ["StandardReranker", "ChatReranker"]
_LAZY_PARSER = [
    "AutoFileParser",
    "AutoLinkParser",
    "AutoParser",
    "ExcelParser",
    "Parser",
    "JSONParser",
    "PDFParser",
    "ImageParser",
    "TxtMdParser",
    "WebPageParser",
    "WeChatArticleParser",
    "WordParser",
]
_LAZY_KNOWLEDGE_BASE = [
    # Knowledge base classes
    "KnowledgeBase",
    "SimpleKnowledgeBase",
    "GraphKnowledgeBase",
    # Knowledge base functions
    "retrieve_multi_kb",
    "retrieve_multi_kb_with_source",
]
_LAZY_QUERY_REWRITER = ["QueryRewriter"]
_LAZY_ATTRIBUTES = (
    _LAZY_MILVUS
    + _LAZY_CHROMA
    + _LAZY_OPENAI
    + _LAZY_HTTPX
    + _LAZY_PARSER
    + _LAZY_KNOWLEDGE_BASE
    + _LAZY_QUERY_REWRITER
)
_LAZY_IMPORT_CACHE = dict.fromkeys(_LAZY_ATTRIBUTES, None)


def _load_httpx():
    from openjiuwen.core.retrieval.reranker.chat_reranker import ChatReranker
    from openjiuwen.core.retrieval.reranker.standard_reranker import StandardReranker

    _LAZY_IMPORT_CACHE["StandardReranker"] = StandardReranker
    _LAZY_IMPORT_CACHE["ChatReranker"] = ChatReranker


def _load_openai():
    from openjiuwen.core.retrieval.embedding.openai_embedding import OpenAIEmbedding
    from openjiuwen.core.retrieval.embedding.utils import parse_base64_embedding
    from openjiuwen.core.retrieval.embedding.vllm_embedding import VLLMEmbedding

    _LAZY_IMPORT_CACHE["OpenAIEmbedding"] = OpenAIEmbedding
    _LAZY_IMPORT_CACHE["VLLMEmbedding"] = VLLMEmbedding
    _LAZY_IMPORT_CACHE["parse_base64_embedding"] = parse_base64_embedding


def _load_milvus():
    from openjiuwen.core.foundation.store.vector_fields.milvus_fields import (
        MilvusAUTO,
        MilvusFLAT,
        MilvusHNSW,
        MilvusIVF,
        MilvusSCANN,
    )
    from openjiuwen.core.retrieval.indexing.indexer.milvus_indexer import MilvusIndexer
    from openjiuwen.core.retrieval.vector_store.milvus_store import MilvusVectorStore

    _LAZY_IMPORT_CACHE["MilvusIndexer"] = MilvusIndexer
    _LAZY_IMPORT_CACHE["MilvusVectorStore"] = MilvusVectorStore
    _LAZY_IMPORT_CACHE["MilvusAUTO"] = MilvusAUTO
    _LAZY_IMPORT_CACHE["MilvusFLAT"] = MilvusFLAT
    _LAZY_IMPORT_CACHE["MilvusHNSW"] = MilvusHNSW
    _LAZY_IMPORT_CACHE["MilvusIVF"] = MilvusIVF
    _LAZY_IMPORT_CACHE["MilvusSCANN"] = MilvusSCANN


def _load_chroma():
    from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField
    from openjiuwen.core.retrieval.indexing.indexer.chroma_indexer import ChromaIndexer
    from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore

    _LAZY_IMPORT_CACHE["ChromaIndexer"] = ChromaIndexer
    _LAZY_IMPORT_CACHE["ChromaVectorStore"] = ChromaVectorStore
    _LAZY_IMPORT_CACHE["ChromaVectorField"] = ChromaVectorField


def _load_parser():
    from openjiuwen.core.retrieval.indexing.processor import parser

    for name in parser.__all__:
        _LAZY_IMPORT_CACHE[name] = getattr(parser, name)


def _load_knowledge_base():
    from openjiuwen.core.retrieval.graph_knowledge_base import GraphKnowledgeBase
    from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase
    from openjiuwen.core.retrieval.simple_knowledge_base import (
        SimpleKnowledgeBase,
        retrieve_multi_kb,
        retrieve_multi_kb_with_source,
    )

    _LAZY_IMPORT_CACHE["KnowledgeBase"] = KnowledgeBase
    _LAZY_IMPORT_CACHE["GraphKnowledgeBase"] = GraphKnowledgeBase
    _LAZY_IMPORT_CACHE["SimpleKnowledgeBase"] = SimpleKnowledgeBase
    _LAZY_IMPORT_CACHE["retrieve_multi_kb"] = retrieve_multi_kb
    _LAZY_IMPORT_CACHE["retrieve_multi_kb_with_source"] = retrieve_multi_kb_with_source


def _load_query_rewriter():
    from openjiuwen.core.retrieval.query_rewriter.query_rewriter import QueryRewriter

    _LAZY_IMPORT_CACHE["QueryRewriter"] = QueryRewriter


def lazy_load(name: str) -> Optional[object]:
    """
    Lazy loading for heavy modules in retrieval
    """
    if name in _LAZY_OPENAI:
        _load_openai()
    elif name in _LAZY_MILVUS:
        _load_milvus()
    elif name in _LAZY_CHROMA:
        _load_chroma()
    elif name in _LAZY_HTTPX:
        _load_httpx()
    elif name in _LAZY_PARSER:
        _load_parser()
    elif name in _LAZY_KNOWLEDGE_BASE:
        _load_knowledge_base()
    elif name in _LAZY_QUERY_REWRITER:
        _load_query_rewriter()
    return _LAZY_IMPORT_CACHE.get(name)
