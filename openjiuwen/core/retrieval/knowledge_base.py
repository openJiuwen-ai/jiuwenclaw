# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Knowledge Base Abstract Base Class

Provides a unified interface for knowledge bases as the top-level entry point.
"""

import uuid
from abc import ABC, abstractmethod
from typing import Any, Callable, Dict, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.config import KnowledgeBaseConfig, RetrievalConfig
from openjiuwen.core.retrieval.common.document import Document
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.vector_store.base import VectorStore


class KnowledgeBase(ABC):
    """Knowledge Base Abstract Base Class"""

    def __init__(
        self,
        config: KnowledgeBaseConfig,
        vector_store: Optional[VectorStore] = None,
        embed_model: Optional[Embedding] = None,
        parser: Optional[Parser] = None,
        chunker: Optional[Chunker] = None,
        extractor: Optional[Extractor] = None,
        index_manager: Optional[Indexer] = None,
        llm_client: Optional[Any] = None,
        strict_validation: bool = True,
        **kwargs,
    ):
        self.strict_validation = strict_validation
        self.config = config
        self.vector_store = vector_store
        self.embed_model = embed_model
        self.parser = parser
        self.chunker = chunker
        self.extractor = extractor
        self.index_manager = index_manager
        self.llm_client = llm_client

    def __setattr__(self, name: str, value: Any):
        """Override setattr to perform additional check"""
        super().__setattr__(name, value)
        special_attrs = {"vector_store", "index_manager"}
        if name in special_attrs and all(getattr(self, attr, None) for attr in special_attrs):
            self.validate_index()
            if any(type(getattr(self, attr)).__name__.casefold().startswith("chroma") for attr in special_attrs):
                if self.strict_validation and self.config.index_type != "vector":
                    raise build_error(
                        StatusCode.RETRIEVAL_KB_DATABASE_CONFIG_INVALID,
                        error_msg="Chroma database does not support sparse embedding & hybrid search in local mode yet",
                    )
            ensure_load_fn: Optional[Callable] = getattr(self.vector_store, "_ensure_loaded", None)
            if ensure_load_fn is not None:
                ensure_load_fn()

    def validate_index(self):
        """Validate vector store and index manager"""
        data_fields = ["text_field", "vector_field", "sparse_vector_field", "metadata_field", "doc_id_field"]
        for attr in ["database_name", "distance_metric"] + data_fields:
            vector_store_val = getattr(self.vector_store, attr, None)
            index_manager_val = getattr(self.index_manager, attr, None)
            if vector_store_val != index_manager_val:
                raise build_error(
                    StatusCode.RETRIEVAL_KB_DATABASE_CONFIG_INVALID,
                    error_msg=f"Vector store and index manager have incompatible {attr} configs:\n"
                    f'- Vector Store ({type(self.vector_store).__name__}) is using "{vector_store_val}"\n'
                    f'- Index manager ({type(self.index_manager).__name__}) is using "{index_manager_val}"',
                )
        if self.strict_validation and self.vector_store:
            self.vector_store.check_vector_field()

    async def delete_collection(self, collection: str) -> None:
        """Delete a collection from current database"""
        if self.vector_store is None:
            raise build_error(
                StatusCode.RETRIEVAL_KB_VECTOR_STORE_NOT_FOUND,
                error_msg="vector_store is required for delete_collection",
            )
        return await self.vector_store.delete_table(collection)

    async def parse_files(
        self,
        file_paths: List[str],
        **kwargs,
    ) -> List[Document]:
        """
        Parse files from file paths into a list of Document objects.

        Args:
            file_paths: List of file paths
            **kwargs: Additional parameters (e.g. file_name, file_id)

        Returns:
            List of Document objects
        """
        if not self.parser:
            raise build_error(
                StatusCode.RETRIEVAL_KB_PARSER_NOT_FOUND,
                error_msg="parser is required for parse_files",
            )
        all_documents = []
        for file_path in file_paths:
            try:
                file_name = kwargs.get("file_name", file_path.split("/")[-1])
                file_id = str(uuid.uuid4())
                documents = await self.parser.parse(
                    file_path,
                    file_id,
                    file_name=file_name,
                )
                all_documents.extend(documents)
            except Exception as e:
                logger.error("Failed to parse file %s: %s", file_path, e)
                continue
        return all_documents

    async def parse_urls(
        self,
        urls: List[str],
        **kwargs,
    ) -> List[Document]:
        """
        Fetch and parse URLs into Document list. With AutoParser/AutoLinkParser,
        WeChat (mp.weixin.qq.com) and generic web URLs are auto-routed.
        """
        if not self.parser:
            raise build_error(
                StatusCode.RETRIEVAL_KB_PARSER_NOT_FOUND,
                error_msg="parser is required for parse_urls",
            )
        all_documents = []
        for url in urls:
            try:
                if not self.parser.supports(url):
                    logger.warning("Parser does not support URL %s, skipping", url)
                    continue
                doc_id = str(uuid.uuid4())
                docs = await self.parser.parse(url, doc_id=doc_id, **kwargs)
                all_documents.extend(docs)
            except Exception as e:
                logger.error("Failed to parse URL %s: %s", url, e)
                continue
        return all_documents

    @abstractmethod
    async def add_documents(
        self,
        documents: List[Document],
        **kwargs,
    ) -> List[str]:
        """Add documents to the knowledge base"""

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        config: Optional[RetrievalConfig] = None,
        **kwargs,
    ) -> List[RetrievalResult]:
        """Retrieve relevant documents"""

    @abstractmethod
    async def delete_documents(
        self,
        doc_ids: List[str],
        **kwargs,
    ) -> bool:
        """Delete documents"""

    @abstractmethod
    async def update_documents(
        self,
        documents: List[Document],
        **kwargs,
    ) -> List[str]:
        """Update documents"""

    @abstractmethod
    async def get_statistics(self) -> Dict[str, Any]:
        """Get knowledge base statistics"""

    async def close(self) -> None:
        """Close the knowledge base and release resources"""
        import inspect

        async def _maybe_await(obj):
            if not obj:
                return
            if inspect.iscoroutinefunction(getattr(obj, "close", None)):
                await obj.close()
            elif hasattr(obj, "close"):
                try:
                    obj.close()
                except Exception:
                    logger.warning("Failed to close object", exc_info=True)

        await _maybe_await(self.vector_store)
        await _maybe_await(self.index_manager)
