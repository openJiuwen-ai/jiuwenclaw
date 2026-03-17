# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
ChromaDB Index Manager Implementation

Responsible for building, updating and deleting ChromaDB indices.
"""

import asyncio
from typing import Any, Dict, List, Optional

import chromadb

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField
from openjiuwen.core.retrieval.common.callbacks import BaseCallback, TqdmCallback
from openjiuwen.core.retrieval.common.config import IndexConfig, VectorStoreConfig
from openjiuwen.core.retrieval.common.document import TextChunk
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.indexing.indexer.base import Indexer
from openjiuwen.core.retrieval.vector_store.chroma_store import ChromaVectorStore


class ChromaIndexer(Indexer):
    """ChromaDB index manager implementation"""

    def __init__(
        self,
        config: VectorStoreConfig,
        chroma_path: str,
        text_field: str = "content",
        vector_field: str | ChromaVectorField = "embedding",
        sparse_vector_field: str = "sparse_vector",
        metadata_field: str = "metadata",
        doc_id_field: str = "document_id",
        doc_index_callback: type[BaseCallback] = TqdmCallback,
        **kwargs,
    ):
        """
        Initialize ChromaDB index manager

        Args:
            config: Vector store configuration
            chroma_path: ChromaDB persistence path
            text_field: Text field name
            vector_field: Vector field name (str) or definition (ChromaVectorField)
            sparse_vector_field: Sparse vector field name
            metadata_field: Metadata field name
            doc_id_field: Document ID field name
            doc_index_callback: class of callback object to use, must be subclass of BaseCallback
        """
        if not chroma_path or not chroma_path.strip():
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_PATH_NOT_FOUND, error_msg="chroma_path is required and cannot be empty"
            )

        self.chroma_path = chroma_path
        self.text_field = text_field
        self.sparse_vector_field = sparse_vector_field
        self.metadata_field = metadata_field
        self.doc_id_field = doc_id_field
        self.database_name = config.database_name
        if isinstance(vector_field, str):
            self.vector_field = ChromaVectorField(vector_field=vector_field)
        elif isinstance(vector_field, ChromaVectorField):
            self.vector_field = vector_field
        else:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_VECTOR_FIELD_INVALID,
                error_msg="vector_field must be either a str or ChromaVectorField instance",
            )
        self._distance_metric = config.distance_metric.replace("dot", "ip").replace("euclidean", "l2")
        self._construct_config = self.vector_field.to_dict(stage="construct")
        self._construct_config["space"] = self._distance_metric
        self._search_config = self.vector_field.to_dict(stage="search")
        self.doc_index_callback = doc_index_callback
        if not isinstance(doc_index_callback, type) or not issubclass(doc_index_callback, BaseCallback):
            raise build_error(
                StatusCode.RETRIEVAL_EMBEDDING_CALLBACK_INVALID,
                error_msg=(
                    f"doc_index_callback in ChromaIndexer must be a subclass of BaseCallback, "
                    f"got {type(doc_index_callback)}"
                ),
            )

        self._client = ChromaVectorStore.create_client(
            database_name=self.database_name,
            path_or_uri=chroma_path,
        )

    @property
    def client(self) -> chromadb.PersistentClient:
        """Get ChromaDB client"""
        return self._client

    @property
    def distance_metric(self) -> str:
        """Get raw distance metric string"""
        return self._distance_metric

    async def build_index(
        self,
        chunks: List[TextChunk],
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
        **kwargs,
    ) -> bool:
        """Build index"""
        try:
            collection_name = config.index_name
            vector_store_config = VectorStoreConfig(
                store_provider="chroma", collection_name=collection_name, database_name=kwargs.pop("database_name", "")
            )
            vector_store = ChromaVectorStore(
                config=vector_store_config,
                chroma_path=self.chroma_path,
                text_field=self.text_field,
                vector_field=self.vector_field.vector_field,
                sparse_vector_field=self.sparse_vector_field,
                metadata_field=self.metadata_field,
                doc_id_field=self.doc_id_field,
            )
            collection = vector_store.collection

            # Raise exception if any doc_id already exists
            all_doc_ids = sorted({chunk.doc_id for chunk in chunks})
            duplicate_doc_ids = []
            filter_values = {None, ""}
            for doc_id in all_doc_ids:
                if doc_id not in filter_values and collection.get(where={self.doc_id_field: doc_id}).get("ids"):
                    duplicate_doc_ids.append(doc_id)
            if duplicate_doc_ids:
                raise build_error(
                    StatusCode.RETRIEVAL_INDEXING_ADD_DOC_RUNTIME_ERROR,
                    error_msg="some documents with same doc_id already exist, if they are the same documents, "
                    f"please consider updating instead of adding. {duplicate_doc_ids=}",
                )

            # If vector index is needed, generate embeddings
            embeddings = None
            if config.index_type in ("vector", "hybrid"):
                if not embed_model:
                    raise build_error(
                        StatusCode.RETRIEVAL_INDEXING_EMBED_MODEL_NOT_FOUND,
                        error_msg="embed_model is required for vector/hybrid index type",
                    )
                texts = [chunk.text for chunk in chunks]
                embeddings = await embed_model.embed_documents(texts, callback_cls=self.doc_index_callback)
                for chunk, embedding in zip(chunks, embeddings):
                    chunk.embedding = embedding

            # Convert TextChunk to ChromaDB required fields
            data = []
            for chunk in chunks:
                meta = chunk.metadata or {}
                item = {
                    "id": chunk.id_,
                    self.doc_id_field: chunk.doc_id,
                    self.text_field: chunk.text,
                    self.metadata_field: meta,
                }
                if chunk.embedding is not None:
                    item[self.vector_field.vector_field] = chunk.embedding
                data.append(item)

            await vector_store.add(data=data)

            logger.info(f"Successfully built index {collection_name} with {len(chunks)} chunks")
            return True
        except Exception as e:
            # Stored data could be damaged with runtime errors ignored, therefore it is raised
            should_raise = [StatusCode.RETRIEVAL_INDEXING_ADD_DOC_RUNTIME_ERROR.code]
            # Re-raise all BaseError exceptions to preserve error information
            # This includes embedding errors, configuration errors, and runtime errors
            if isinstance(e, BaseError) and getattr(e, "code", None) in should_raise:
                raise e
            # For non-BaseError exceptions (e.g., from third-party libraries),
            # log and return False to avoid breaking the process
            logger.error(f"Failed to build index: {e}")
            return False

    async def update_index(
        self,
        chunks: List[TextChunk],
        doc_id: str,
        config: IndexConfig,
        embed_model: Optional[Embedding] = None,
        **kwargs,
    ) -> bool:
        """Update index"""
        try:
            # Delete old data first
            await self.delete_index(doc_id, config.index_name)

            # Rebuild
            return await self.build_index(chunks, config, embed_model, **kwargs)
        except Exception as e:
            logger.error(f"Failed to update index: {e}")
            return False

    async def delete_index(
        self,
        doc_id: str,
        index_name: str,
        **kwargs,
    ) -> bool:
        """Delete index"""
        try:
            # ChromaDB doesn't support complex filter expressions, need to query first then delete
            collection: chromadb.Collection = await asyncio.to_thread(
                self._client.get_collection,
                name=index_name,
            )

            # Query all records matching doc_id
            results = await asyncio.to_thread(
                collection.get,
                where={self.doc_id_field: doc_id},
            )

            if not results or not results.get("ids") or len(results["ids"]) == 0:
                logger.info(f"No entries found for doc_id={doc_id}")
                return False

            # Delete matching records
            ids_to_delete = results["ids"]
            await asyncio.to_thread(
                collection.delete,
                ids=ids_to_delete,
            )

            delete_count = len(ids_to_delete)
            logger.info(f"Deleted {delete_count} entries for doc_id={doc_id}")
            return delete_count > 0
        except Exception as e:
            logger.error(f"Failed to delete index entries: {e}")
            return False

    async def index_exists(
        self,
        index_name: str,
    ) -> bool:
        """Check if index exists"""
        try:
            # Try to get collection, throws exception if not exists
            await asyncio.to_thread(
                self._client.get_collection,
                name=index_name,
            )
            return True
        except Exception:
            return False

    async def get_index_info(
        self,
        index_name: str,
    ) -> Dict[str, Any]:
        """Get index information"""
        try:
            if not await self.index_exists(index_name):
                return {"exists": False}

            collection: chromadb.Collection = await asyncio.to_thread(
                self._client.get_collection,
                name=index_name,
            )

            # Get collection statistics
            count = await asyncio.to_thread(
                collection.count,
            )

            # Get collection metadata
            metadata = collection.metadata or {}

            return {
                "exists": True,
                "collection_name": index_name,
                "count": count,
                "metadata": metadata,
            }
        except Exception as e:
            logger.error(f"Failed to get index info: {e}")
            return {"exists": False, "error": str(e)}

    def close(self) -> None:
        """Close index manager"""
        # ChromaDB client usually doesn't need explicit closing
        # But can reset client reference
        if self._client is not None:
            try:
                # ChromaDB client doesn't have close method, but can reset
                pass
            except Exception as e:
                logger.warning(f"Failed to close ChromaDB client: {e}")
