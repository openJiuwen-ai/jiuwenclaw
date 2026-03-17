# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from typing import List, Tuple

from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
)
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import memory_logger
from openjiuwen.core.common.logging.events import LogEventType
from openjiuwen.core.memory.migration.migration_plan import vector_registry
from openjiuwen.core.memory.common.base import parse_memtype_from_idx_name


class SemanticStore:
    """
    Concrete implementation of semantic storage that uses an embedding model and vector store.

    This class provides an implementation of the semantic storage interface defined in BaseSemanticStore.
    It uses an embedding model to generate vector representations of text documents and a vector store
    to store and search these embeddings efficiently.
    """

    def __init__(self, vector_store: BaseVectorStore, embedding_model: Embedding | None = None):
        """
        Initialize the semantic store with an embedding model and vector store.

        Args:
            vector_store: The vector store to use for storing and searching embeddings.
            embedding_model: Optional embedding model to use for generating embeddings.
                If not provided, must be initialized later using initialize_embedding_model.
        """
        self.embedding_model = embedding_model
        self.vector_store = vector_store
        # Cache for created collections to avoid repeated existence checks
        self._created_collections: set[str] = set()

    def initialize_embedding_model(self, embedding_model: Embedding):
        """
        Initialize or update the embedding model used by the semantic store.

        Args:
            embedding_model: The embedding model to use for generating text embeddings.
        """
        self.embedding_model = embedding_model

    async def _create_collection_if_not_exists(self, collection_name: str, embedding_dim: int) -> None:
        """
        Create a collection if it does not already exist.

        Args:
            collection_name: Name of the collection to check/create
            embedding_dim: Dimension of the embedding vector
        """
        # Check if we already created this collection (in-memory cache)
        if collection_name in self._created_collections:
            return

        # Check if collection exists in vector store
        exists = await self.vector_store.collection_exists(collection_name)
        if exists:
            self._created_collections.add(collection_name)
            return

        # Create collection with schema
        schema = CollectionSchema(
            description="Semantic memory collection",
            enable_dynamic_field=False,
        )
        schema.add_field(
            FieldSchema(
                name="id",
                dtype=VectorDataType.VARCHAR,
                max_length=256,
                is_primary=True,
            )
        )
        schema.add_field(
            FieldSchema(
                name="embedding",
                dtype=VectorDataType.FLOAT_VECTOR,
                dim=embedding_dim,
            )
        )

        await self.vector_store.create_collection(collection_name, schema)

        mem_type = parse_memtype_from_idx_name(collection_name)
        latest_schema_version = vector_registry.get_current_version(f"vector_{mem_type}")
        await self.vector_store.update_collection_metadata(
            collection_name,
            {"schema_version": latest_schema_version}
        )

        self._created_collections.add(collection_name)
        memory_logger.debug(
            f"Created collection '{collection_name}' with embedding dimension {embedding_dim}",
            event_type=LogEventType.MEMORY_STORE,
            metadata={"collection_name": collection_name, "embedding_dim": embedding_dim}
        )

    async def add_docs(self, docs: List[Tuple[str, str]], table_name: str, scope_id: str | None = None) -> bool:
        """
        Add documents to a specified table after generating their embeddings.

        Args:
            docs (List[Tuple[str, str]]): A list of (id, text) tuples where id is a unique identifier
                and text is the raw string to be embedded.
            table_name (str): The name of the table where the embeddings will be stored.
            scope_id (str | None): Optional scope identifier to associate with the documents.
                Can be used for filtering during search.

        Returns:
            bool: True if the operation succeeded, False otherwise.
        """
        if not self.embedding_model:
            memory_logger.error(
                "Embedding model not initialized, please call initialize_embedding_model first.",
                event_type=LogEventType.MEMORY_STORE,
                scope_id=scope_id,
                metadata={"collection_name": table_name}
            )
            return False

        try:
            memory_ids, texts = zip(*docs)
            memory_ids = list(memory_ids)
            texts = list(texts)

            # Generate embeddings for the texts
            embeddings = await self.embedding_model.embed_documents(texts=texts)

            if len(memory_ids) != len(embeddings):
                raise build_error(
                    StatusCode.MEMORY_STORE_VALIDATION_INVALID,
                    store_type="semantic store",
                    error_msg=f"memory_ids and embeddings must have same length",
                )

            # Create collection if not exists (get dimension from first embedding)
            if embeddings:
                embedding_dim = len(embeddings[0])
                await self._create_collection_if_not_exists(table_name, embedding_dim)

            # Prepare data for vector store, content is not stored
            data = []
            for doc_id, embedding in zip(memory_ids, embeddings):
                data.append({
                    "id": doc_id,
                    "embedding": embedding,
                })

            # Add to vector store
            await self.vector_store.add_docs(collection_name=table_name, docs=data)
            return True
        except Exception as e:
            memory_logger.error(
                "Failed to add documents to semantic store.",
                event_type=LogEventType.MEMORY_STORE,
                exception=str(e),
                scope_id=scope_id,
                metadata={"collection_name": table_name}
            )
            return False

    async def delete_docs(self, ids: List[str], table_name: str) -> None:
        """
        Delete documents from a specified table by their unique identifiers.

        Args:
            ids (List[str]): A list of unique document ids whose embeddings should be removed.
            table_name (str): The name of the table from which to delete embeddings.
        """
        try:
            # Check if collection exists before attempting deletion
            exists = await self.vector_store.collection_exists(table_name)
            if not exists:
                memory_logger.debug(
                    f"Collection '{table_name}' does not exist, nothing to delete",
                    event_type=LogEventType.MEMORY_DELETE,
                    metadata={"collection_name": table_name}
                )
                return None

            return await self.vector_store.delete_docs_by_ids(ids=ids, collection_name=table_name)
        except Exception as e:
            memory_logger.error(
                "Failed to delete documents from semantic store.",
                event_type=LogEventType.MEMORY_DELETE,
                exception=str(e),
                meta_data={"collection_name": table_name},
                memory_id=ids
            )

    async def search(self, query: str, table_name: str,
                     scope_id: str | None = None, top_k: int = 5) -> List[Tuple[str, float]]:
        """
        Search for the top-k most similar documents to a query string.

        The query string is embedded internally before similarity comparison.

        Args:
            query (str): The raw query string to embed and search for.
            table_name (str): The name of the table to search within.
            scope_id (str | None): Optional scope identifier to filter results.
                Only documents with matching scope_id will be returned.
            top_k (int): The number of most similar results to return.
                Defaults to 5.

        Returns:
            List[Tuple[str, float]]: A list of (id, score) tuples where `id`
                is the unique identifier of the matched document and `score`
                is the similarity score, with higher values indicating greater similarity.
        """
        if not self.embedding_model:
            memory_logger.error(
                "Embedding model not initialized, please call initialize_embedding_model first.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                query=query,
                meta_data={"collection_name": table_name}
            )
            return []

        try:
            # Generate embedding for the query
            query_embeddings = await self.embedding_model.embed_documents(texts=[query])
            if len(query_embeddings) == 0:
                memory_logger.error(
                    "Failed to embed query.",
                    event_type=LogEventType.MEMORY_RETRIEVE,
                    query=query,
                    meta_data={"collection_name": table_name}
                )
                return []
            query_embedding = query_embeddings[0]

            exists = await self.vector_store.collection_exists(table_name)
            if not exists:
                return []

            # Search in vector store
            results = await self.vector_store.search(
                collection_name=table_name,
                query_vector=query_embedding,
                vector_field="embedding",
                top_k=top_k,
            )

            # Convert to required format
            return [(result.fields.get("id", ""), result.score) for result in results]
        except Exception as e:
            memory_logger.error(
                "Failed to embed query.",
                event_type=LogEventType.MEMORY_RETRIEVE,
                query=query,
                exception=str(e),
                metadata={"collection_name": table_name}
            )
            return []

    async def delete_table(self, table_name: str) -> None:
        """
        Delete an entire table and all its stored embeddings.

        Args:
            table_name (str): The name of the table to delete.
        """
        try:
            result = await self.vector_store.delete_collection(collection_name=table_name)
            # Remove from cache after successful deletion
            if table_name in self._created_collections:
                self._created_collections.remove(table_name)
            return result
        except Exception as e:
            memory_logger.error(
                "Failed to delete table from semantic store.",
                event_type=LogEventType.MEMORY_DELETE,
                message="delete table",
                exception=str(e),
                meta_data={"collection_name": table_name}
            )
