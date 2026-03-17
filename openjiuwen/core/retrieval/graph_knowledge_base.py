# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
GraphRAG Knowledge Base Implementation

Knowledge base implementation supporting graph indexing and retrieval.
"""

import json
import uuid
from typing import Any, Dict, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import BaseModelClient
from openjiuwen.core.retrieval.common.config import IndexConfig, KnowledgeBaseConfig, RetrievalConfig
from openjiuwen.core.retrieval.common.document import Document, TextChunk
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.indexing.processor.chunker.base import Chunker
from openjiuwen.core.retrieval.indexing.processor.extractor.base import Extractor
from openjiuwen.core.retrieval.indexing.processor.parser.base import Parser
from openjiuwen.core.retrieval.knowledge_base import KnowledgeBase
from openjiuwen.core.retrieval.retriever.agentic_retriever import AgenticRetriever
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
from openjiuwen.core.retrieval.simple_knowledge_base import retrieve_multi_kb, retrieve_multi_kb_with_source
from openjiuwen.core.retrieval.vector_store.base import VectorStore


class GraphKnowledgeBase(KnowledgeBase):
    """Graph-enhanced knowledge base implementation"""

    def __init__(
        self,
        config: KnowledgeBaseConfig,
        vector_store: Optional[VectorStore] = None,
        embed_model: Optional[Embedding] = None,
        parser: Optional[Parser] = None,
        chunker: Optional[Chunker] = None,
        extractor: Optional[Extractor] = None,
        index_manager: Optional[Indexer] = None,
        chunk_retriever: Optional[Retriever] = None,
        triple_retriever: Optional[Retriever] = None,
        llm_client: Optional[BaseModelClient] = None,
        **kwargs,
    ):
        """
        Initialize GraphRAG knowledge base

        Args:
            config: Knowledge base configuration
            vector_store: Vector store instance
            embed_model: Embedding model instance
            parser: Document parser instance
            chunker: Text chunker instance
            extractor: Triple extractor instance (required)
            index_manager: Index manager instance
            chunk_retriever: Chunk retriever instance (optional)
            triple_retriever: Triple retriever instance (optional)
            llm_client: LLM client instance (for triple extraction)
        """
        super().__init__(
            config=config,
            vector_store=vector_store,
            embed_model=embed_model,
            parser=parser,
            chunker=chunker,
            extractor=extractor,
            index_manager=index_manager,
            llm_client=llm_client,
            **kwargs,
        )
        self.chunk_retriever = chunk_retriever
        self.triple_retriever = triple_retriever
        self.graph_retriever: Optional[GraphRetriever] = None

    async def add_documents(
        self,
        documents: List[Document],
        **kwargs,
    ) -> List[str]:
        """Add documents to the knowledge base (including chunk index and triple index)"""
        if not self.chunker:
            raise build_error(
                StatusCode.RETRIEVAL_KB_CHUNKER_NOT_FOUND, error_msg="chunker is required for add_documents"
            )
        if not self.index_manager:
            raise build_error(
                StatusCode.RETRIEVAL_KB_INDEX_MANAGER_NOT_FOUND, error_msg="index_manager is required for add_documents"
            )
        if self.strict_validation and self.vector_store:
            self.vector_store.check_vector_field()

        for doc in documents:
            if not (getattr(doc, "id_", None) or "").strip():
                doc.id_ = str(uuid.uuid4())

        # Chunk documents
        chunks = self.chunker.chunk_documents(documents)
        logger.info(f"Chunked {len(documents)} documents into {len(chunks)} chunks")

        # Build chunk index
        chunk_index_config = IndexConfig(
            index_name=f"kb_{self.config.kb_id}_chunks",
            index_type=self.config.index_type,
        )

        database_name = getattr(getattr(self.vector_store, "config", None), "database_name", None)
        if not isinstance(database_name, str):
            database_name = ""
        success = await self.index_manager.build_index(
            chunks=chunks,
            config=chunk_index_config,
            embed_model=self.embed_model,
            database_name=database_name,
        )

        if not success:
            raise build_error(
                StatusCode.RETRIEVAL_KB_CHUNK_INDEX_BUILD_EXECUTION_ERROR, error_msg="Failed to build chunk index"
            )

        # If graph indexing is enabled, extract triples and build triple index
        if self.config.use_graph and self.extractor:
            logger.info("Extracting triples for graph index...")
            triples = await self.extractor.extract(chunks)

            if triples:
                logger.info(f"Extracted {len(triples)} triples")

                # Build triple index
                triple_index_config = IndexConfig(
                    index_name=f"kb_{self.config.kb_id}_triples",
                    index_type=self.config.index_type,
                )

                # Convert triples to TextChunk format for indexing
                triple_chunks = []
                for i, triple in enumerate(triples):
                    # Convert triple to text format
                    triple_text = f"{triple.subject} {triple.predicate} {triple.object}"
                    chunk = TextChunk(
                        id_=str(uuid.uuid4()),
                        text=triple_text,
                        doc_id=triple.metadata.get("doc_id", ""),
                        metadata={
                            **triple.metadata,
                            "triple": json.dumps([triple.subject, triple.predicate, triple.object]),
                            "confidence": triple.confidence or 0,
                            "chunk_index": i,
                            "chunk_id": triple.metadata.get("chunk_id", ""),
                        },
                    )
                    triple_chunks.append(chunk)

                success = await self.index_manager.build_index(
                    chunks=triple_chunks,
                    config=triple_index_config,
                    embed_model=self.embed_model,
                    database_name=database_name,
                )

                if not success:
                    raise build_error(
                        StatusCode.RETRIEVAL_KB_TRIPLE_INDEX_BUILD_EXECUTION_ERROR,
                        error_msg="Failed to build triple index",
                    )
                logger.info(f"Built triple index with {len(triple_chunks)} triples")

        # Return document ID list
        doc_ids = [doc.id_ for doc in documents]
        logger.info(f"Successfully added {len(doc_ids)} documents to knowledge base")
        return doc_ids

    async def retrieve(
        self,
        query: str,
        config: Optional[RetrievalConfig] = None,
        **kwargs,
    ) -> List[RetrievalResult]:
        """Retrieve relevant documents (supports graph retrieval)"""
        retrieval_config = config or RetrievalConfig()

        # If using graph retrieval, create or use graph retriever
        if retrieval_config.use_graph or (retrieval_config.use_graph is None and self.config.use_graph):
            if not self.graph_retriever:
                if not self.vector_store:
                    raise build_error(
                        StatusCode.RETRIEVAL_KB_VECTOR_STORE_NOT_FOUND,
                        error_msg="vector_store is required for graph retrieval",
                    )
                chunk_collection = f"kb_{self.config.kb_id}_chunks"
                triple_collection = f"kb_{self.config.kb_id}_triples"

                # Create GraphRetriever, pass necessary parameters for dynamic retriever creation
                self.graph_retriever = GraphRetriever(
                    chunk_retriever=self.chunk_retriever,  # If fixed retriever is provided, use it first
                    triple_retriever=self.triple_retriever,  # If fixed retriever is provided, use it first
                    vector_store=self.vector_store,  # For dynamic retriever creation
                    embed_model=self.embed_model,  # For dynamic retriever creation
                    chunk_collection=chunk_collection,  # For dynamic retriever creation
                    triple_collection=triple_collection,  # For dynamic retriever creation
                )
                # Inject index_type from upper layer for GraphRetriever mode validation
                self.graph_retriever.index_type = self.config.index_type

            mode = "hybrid"
            if self.config.index_type == "vector":
                mode = "vector"
            elif self.config.index_type == "bm25":
                mode = "sparse"

            # Enable agentic retrieval based if needed based on retrieval config
            if retrieval_config.agentic:
                retriever = AgenticRetriever(
                    retriever=self.graph_retriever,
                    llm_client=self.llm_client,
                    **kwargs,
                )
            else:
                retriever = self.graph_retriever

            results = await retriever.retrieve(
                query=query,
                top_k=retrieval_config.top_k,
                score_threshold=retrieval_config.score_threshold,
                filters=retrieval_config.filters,
                mode=mode,
                graph_expansion=retrieval_config.graph_expansion,
            )

            return results

        # Use normal retrieval (fallback to simple knowledge base retrieval method)
        from openjiuwen.core.retrieval.simple_knowledge_base import SimpleKnowledgeBase

        base_kb = SimpleKnowledgeBase(
            config=self.config,
            vector_store=self.vector_store,
            embed_model=self.embed_model,
            parser=self.parser,
            chunker=self.chunker,
            index_manager=self.index_manager,
            llm_client=self.llm_client,
        )

        return await base_kb.retrieve(query, config, **kwargs)

    async def delete_documents(
        self,
        doc_ids: List[str],
        **kwargs,
    ) -> bool:
        """Delete documents (including chunk index and triple index)"""
        if not self.index_manager:
            raise build_error(
                StatusCode.RETRIEVAL_KB_INDEX_MANAGER_NOT_FOUND,
                error_msg="index_manager is required for delete_documents",
            )
        if self.strict_validation and self.vector_store:
            self.vector_store.check_vector_field()

        chunk_index_name = f"kb_{self.config.kb_id}_chunks"
        triple_index_name = f"kb_{self.config.kb_id}_triples"

        success = True

        # Delete chunk index
        for doc_id in doc_ids:
            result = await self.index_manager.delete_index(
                doc_id=doc_id,
                index_name=chunk_index_name,
            )
            if not result:
                success = False

        # Delete triple index (if exists)
        if self.config.use_graph:
            for doc_id in doc_ids:
                result = await self.index_manager.delete_index(
                    doc_id=doc_id,
                    index_name=triple_index_name,
                )
                if not result:
                    # Triple deletion failure does not affect overall result
                    logger.warning(f"Failed to delete triples for doc_id={doc_id}")

        return success

    async def update_documents(
        self,
        documents: List[Document],
        **kwargs,
    ) -> List[str]:
        """Update documents (including chunk index and triple index)"""
        if self.strict_validation and self.vector_store:
            self.vector_store.check_vector_field()
        # First delete old documents
        doc_ids = [doc.id_ for doc in documents]
        await self.delete_documents(doc_ids)

        # Re-add documents
        return await self.add_documents(documents, **kwargs)

    async def get_statistics(self) -> Dict[str, Any]:
        """Get knowledge base statistics"""
        chunk_index_name = f"kb_{self.config.kb_id}_chunks"
        triple_index_name = f"kb_{self.config.kb_id}_triples"

        if not self.index_manager:
            return {
                "kb_id": self.config.kb_id,
                "index_exists": False,
            }

        chunk_info = await self.index_manager.get_index_info(chunk_index_name)
        triple_info = None
        if self.config.use_graph:
            triple_info = await self.index_manager.get_index_info(triple_index_name)

        return {
            "kb_id": self.config.kb_id,
            "index_type": self.config.index_type,
            "use_graph": self.config.use_graph,
            "chunk_index_info": chunk_info,
            "triple_index_info": triple_info,
            "has_parser": self.parser is not None,
            "has_chunker": self.chunker is not None,
            "has_extractor": self.extractor is not None,
            "has_embed_model": self.embed_model is not None,
            "has_vector_store": self.vector_store is not None,
            "has_graph_retriever": self.graph_retriever is not None,
        }

    async def close(self) -> None:
        """Close the knowledge base"""
        await super().close()
        if self.graph_retriever:
            await self.graph_retriever.close()
        if self.chunk_retriever:
            await self.chunk_retriever.close()
        if self.triple_retriever:
            await self.triple_retriever.close()


# ========= Multi-Knowledge Base Retrieval Helpers =========


async def retrieve_multi_graph_kb(
    kbs: List[KnowledgeBase],
    query: str,
    config: Optional[RetrievalConfig] = None,
    top_k: Optional[int] = None,
) -> List[str]:
    """Perform retrieval on multiple knowledge bases (returns text list)."""
    return await retrieve_multi_kb(kbs, query, config=config, top_k=top_k)


async def retrieve_multi_graph_kb_with_source(
    kbs: List[KnowledgeBase],
    query: str,
    config: Optional[RetrievalConfig] = None,
    top_k: Optional[int] = None,
) -> List[Dict[str, Any]]:
    """Perform retrieval on multiple knowledge bases (includes source information)."""
    return await retrieve_multi_kb_with_source(kbs, query, config=config, top_k=top_k)
