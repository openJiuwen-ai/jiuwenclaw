# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import json
from typing import Any, Dict, List, Optional, Union, Callable
import time

import anyio
import chromadb

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.task_manager.manager import get_task_manager
from openjiuwen.core.common.exception.errors import build_error, ValidationError
from openjiuwen.core.common.logging import store_logger, LogEventType
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    VectorSearchResult,
    CollectionSchema,
    FieldSchema,
    VectorDataType,
)
from openjiuwen.core.foundation.store.vector.utils import (
    convert_cosine_distance,
    convert_l2_squared,
    convert_ip_distance,
    compute_new_schema,
    build_transform_func_for_operations
)
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation


class ChromaVectorStore(BaseVectorStore):
    """
    ChromaDB vector store implementation.

    This class implements BaseVectorStore interface using ChromaDB as the backend.
    """

    def __init__(
        self,
        persist_directory: Optional[str] = None,
    ):
        """
        Initialize ChromaVectorStore.

        Args:
            persist_directory: Path to persist ChromaDB data. If None, uses in-memory storage.
        """
        self._client = chromadb.PersistentClient(path=persist_directory)
        self._task_manager = get_task_manager()
        # Cache for collections
        self._collections: Dict[str, chromadb.Collection] = {}

    def _get_collection(self, collection_name: str) -> chromadb.Collection:
        """Get or create a collection."""
        if collection_name not in self._collections:
            try:
                collection = self._client.get_collection(name=collection_name)
            except Exception as e:
                # Collection doesn't exist, will be created in create_collection
                raise ValidationError(
                    StatusCode.STORE_VECTOR_COLLECTION_NOT_FOUND,
                    msg="collection doesn't exist",
                    details=str(e),
                    collection_name=collection_name,
                ) from e
            self._collections[collection_name] = collection
        return self._collections[collection_name]

    async def create_collection(
        self,
        collection_name: str,
        schema: Union[CollectionSchema, Dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        """
        Create a new collection with specified schema.

        Args:
            collection_name: Name of the collection to create
            schema: CollectionSchema instance or schema dictionary
            **kwargs: Additional parameters for collection creation
                - distance_metric (str): Distance metric for vector search (default: "cosine")
                  Options: "cosine", "l2", "ip"
        """
        distance_metric = kwargs.get("distance_metric", "cosine")
        # Map distance metric to ChromaDB format
        chroma_metric = distance_metric.replace("dot", "ip").replace("euclidean", "l2")

        # Convert dict to CollectionSchema if needed
        if isinstance(schema, dict):
            schema = CollectionSchema.from_dict(schema)

        async def _create():
            # Validate: primary key field is required
            primary_field = schema.get_primary_key_field()
            if not primary_field:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg="schema must contain a primary key field (is_primary=True)"
                )

            # Validate: FLOAT_VECTOR field must be unique
            vector_fields = schema.get_vector_fields()
            if len(vector_fields) == 0:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg="schema must contain at least one FLOAT_VECTOR field"
                )

            # Extract vector field info
            vector_field = vector_fields[0]

            # Build field mapping: user-defined field names -> ChromaDB built-in fields
            # ChromaDB built-in fields: ids, embeddings, documents, metadatas
            field_mapping = {
                "primary_key": primary_field.name,  # Maps to ChromaDB's "ids"
                "vector_field": vector_field.name,  # Maps to ChromaDB's "embeddings"
                "text_field": None,  # Will be determined below, maps to ChromaDB's "documents"
            }

            # Identify text field (first non-primary VARCHAR field)
            for f in schema.fields:
                if f.dtype == VectorDataType.VARCHAR and not f.is_primary:
                    field_mapping["text_field"] = f.name
                    break

            # Store schema, field mapping, and distance metric in metadata
            metadata = {
                "schema": json.dumps(schema.to_dict()),
                "fields": json.dumps(schema.to_dict()),
                "field_mapping": json.dumps(field_mapping),
                "vector_field": vector_field.name,
                "distance_metric": chroma_metric,  # Save metric for later use
            }

            # Configure HNSW index with distance metric
            configuration = {
                "hnsw": {"space": chroma_metric}
            }

            async def _get_or_create_collection():
                return await anyio.to_thread.run_sync(
                    lambda: self._client.get_or_create_collection(
                        name=collection_name,
                        metadata=metadata,
                        configuration=configuration,
                    )
                )
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    _get_or_create_collection(),
                    name="get_or_create_collection")
            collection = task.result
            self._collections[collection_name] = collection
            store_logger.info(
                f"Created collection with {len(schema.fields)} fields",
                event_type=LogEventType.STORE_ADD,
                table_name=collection_name
            )

        await _create()

    async def delete_collection(
        self,
        collection_name: str,
        **kwargs: Any,
    ) -> None:
        """
        Delete a collection by name.

        Args:
            collection_name: Name of the collection to delete
            **kwargs: Additional parameters for collection deletion
        """
        async def _delete():
            async def _delete_collection():
                await anyio.to_thread.run_sync(
                    lambda: self._client.delete_collection(name=collection_name)
                )
            try:
                async with self._task_manager.task_group():
                    await self._task_manager.create_task(_delete_collection(), name="delete_collection")
                if collection_name in self._collections:
                    del self._collections[collection_name]
                store_logger.info(
                    "Deleted collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name
                )
            except Exception as e:
                store_logger.error(
                    "Failed to delete collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name,
                    exception=str(e)
                )
                raise

        await _delete()

    async def collection_exists(
        self,
        collection_name: str,
        **kwargs: Any,
    ) -> bool:
        """
        Check if a collection exists.

        Args:
            collection_name: Name of the collection to check
            **kwargs: Additional parameters for collection existence check

        Returns:
            bool: True if the collection exists, False otherwise
        """
        async def _exists():
            async def _check():
                await anyio.to_thread.run_sync(
                    lambda: self._client.get_collection(name=collection_name)
                )
                return True
            try:
                async with self._task_manager.task_group():
                    task = await self._task_manager.create_task(_check(), name="check_collection_exists")
                return task.result
            except Exception:
                return False

        return await _exists()

    async def get_schema(
        self,
        collection_name: str,
        **kwargs: Any,
    ) -> CollectionSchema:
        """
        Get the schema of a collection.

        Args:
            collection_name: Name of the collection
            **kwargs: Additional parameters for getting schema

        Returns:
            CollectionSchema: The schema of the collection

        Raises:
            ValueError: If the collection does not exist
        """
        collection = self._get_collection(collection_name)

        async def _get_schema():
            # Try to get schema from collection metadata
            try:
                metadata = collection.metadata
                if metadata and "schema" in metadata:
                    schema_dict = json.loads(metadata["schema"])
                    return CollectionSchema.from_dict(schema_dict)
                elif metadata and "fields" in metadata:
                    schema_dict = json.loads(metadata["fields"])
                    return CollectionSchema.from_dict(schema_dict)
                else:
                    # If schema not stored in metadata, build default schema
                    # Try to get field mapping from metadata
                    field_mapping = {}
                    if metadata and "field_mapping" in metadata:
                        field_mapping = json.loads(metadata["field_mapping"])

                    primary_key = field_mapping.get("primary_key", "id")
                    vector_field = field_mapping.get("vector_field", "embedding")
                    text_field = field_mapping.get("text_field", "text")

                    schema = CollectionSchema(
                        description=f"Collection '{collection_name}'",
                        enable_dynamic_field=True,
                    )
                    # Add fields based on field mapping
                    schema.add_field(
                        FieldSchema(
                            name=primary_key,
                            dtype=VectorDataType.VARCHAR,
                            max_length=256,
                            is_primary=True,
                        )
                    )
                    schema.add_field(
                        FieldSchema(
                            name=vector_field,
                            dtype=VectorDataType.FLOAT_VECTOR,
                            dim=None,  # Dimension may vary
                        )
                    )
                    schema.add_field(
                        FieldSchema(
                            name=text_field,
                            dtype=VectorDataType.VARCHAR,
                            max_length=65535,
                        )
                    )
                    schema.add_field(
                        FieldSchema(
                            name="metadata",
                            dtype=VectorDataType.JSON,
                        )
                    )
                    return schema
            except Exception as e:
                store_logger.warning(
                    "Could not get schema from collection",
                    event_type=LogEventType.STORE_RETRIEVE,
                    table_name=collection_name,
                    exception=str(e)
                )
                # Return default schema as fallback
                schema = CollectionSchema(
                    description=f"Collection '{collection_name}'",
                    enable_dynamic_field=True,
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
                    )
                )
                schema.add_field(
                    FieldSchema(
                        name="text",
                        dtype=VectorDataType.VARCHAR,
                        max_length=65535,
                    )
                )
                return schema

        return await _get_schema()

    async def add_docs(
        self,
        collection_name: str,
        docs: List[Dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        """
        Add documents to a collection.

        Args:
            collection_name: Name of the target collection
            docs: List of documents to add
            **kwargs: Additional parameters for document insertion
                - batch_size (int, optional): Batch size for bulk insertion (default: 128)
        """
        batch_size = kwargs.get("batch_size", 128)
        if batch_size <= 0:
            batch_size = 128

        collection = self._get_collection(collection_name)


        metadata = collection.metadata or {}
        field_mapping_str = metadata.get("field_mapping", "{}")
        field_mapping = json.loads(field_mapping_str)

        primary_key = field_mapping.get("primary_key", "id")
        vector_field = field_mapping.get("vector_field", "embedding")
        text_field = field_mapping.get("text_field", "text")

        async def _add_batch(batch: List[Dict[str, Any]]):
            ids = []
            embeddings = []
            documents = []
            metadatas = []
            has_metadata = True

            async def _add(ids: List[str], embeddings: List[List[float]],
                           documents: List[str], metadatas: List[Dict[str, Any]] | None = None):
                await anyio.to_thread.run_sync(
                    lambda: collection.add(
                        ids=ids,
                        embeddings=embeddings,
                        documents=documents,
                        metadatas=metadatas,
                    )
                )
            for doc in batch:
                # Extract ID from user-defined primary key field
                doc_id = doc.get(primary_key)
                if doc_id is None:
                    raise build_error(
                        StatusCode.STORE_VECTOR_DOC_INVALID,
                        error_msg=f"document must have primary field '{primary_key}'"
                    )
                ids.append(str(doc_id))

                # Extract embedding from user-defined vector field
                embedding = doc.get(vector_field)
                if embedding is None:
                    raise build_error(
                        StatusCode.STORE_VECTOR_DOC_INVALID,
                        error_msg=f"document must have vector field '{vector_field}'"
                    )
                embeddings.append(embedding)

                # Extract text content from user-defined text field
                text = doc.get(text_field, "")
                documents.append(text)

                # Build metadata (all other fields except primary key, vector field, text field)
                metadata = {}
                for key, value in doc.items():
                    if key not in [primary_key, vector_field, text_field]:
                        # ChromaDB metadata must be JSON serializable
                        if isinstance(value, (str, int, float, bool, type(None))):
                            metadata[key] = value
                        elif isinstance(value, (list, dict)):
                            metadata[key] = json.dumps(value)
                        else:
                            metadata[key] = str(value)
                if not metadata:
                    has_metadata = False
                metadatas.append(metadata)

            if has_metadata:
                async with self._task_manager.task_group():
                    await self._task_manager.create_task(
                        _add(ids, embeddings, documents, metadatas),
                        name="add_docs"
                    )
            else:
                async with self._task_manager.task_group():
                    await self._task_manager.create_task(
                        _add(ids, embeddings, documents),
                        name="add_docs"
                    )

        # Process in batches
        total = len(docs)
        processed = 0
        for i in range(0, total, batch_size):
            batch = docs[i: i + batch_size]
            await _add_batch(batch)
            processed += len(batch)
            store_logger.info(
                f"Added {processed}/{total} documents'",
                event_type=LogEventType.STORE_ADD,
                table_name=collection_name,
                data_num=processed
            )

        store_logger.info(
            "Successfully added documents",
            event_type=LogEventType.STORE_ADD,
            table_name=collection_name,
            data_num=total
        )

    async def search(
        self,
        collection_name: str,
        query_vector: List[float],
        vector_field: str,
        top_k: int = 5,
        filters: Optional[Dict[str, Any]] = None,
        **kwargs: Any,
    ) -> List[VectorSearchResult]:
        """
        Search for the most relevant documents by vector similarity.

        Args:
            collection_name: Name of the collection to search
            query_vector: Query vector for similarity search
            vector_field: Name of the vector field to search against (for compatibility, ignored)
            top_k: Number of most relevant documents to return
            filters: Optional dictionary of scalar field filters (equality only)
            **kwargs: Additional search parameters
                - metric_type (str, optional): Distance metric (not used, already set in collection)

        Returns:
            List of VectorSearchResult objects
        """
        collection = self._get_collection(collection_name)

        async def _search():
            # Get field mapping from collection metadata
            metadata = collection.metadata or {}
            field_mapping_str = metadata.get("field_mapping", "{}")
            field_mapping = json.loads(field_mapping_str)
            primary_key = field_mapping.get("primary_key", "id")
            text_field = field_mapping.get("text_field", None)

            # Build where filter for ChromaDB (equality filters only)
            where = filters if filters else None

            async def _query():
                return await anyio.to_thread.run_sync(
                    lambda: collection.query(
                        query_embeddings=[query_vector],
                        n_results=top_k,
                        where=where,
                        include=["documents", "metadatas", "distances"],
                    )
                )
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    _query(),
                    name="query"
                )
            results = task.result

            search_results = []
            if results and results.get("ids") and len(results["ids"]) > 0:
                ids_list = results["ids"][0]
                documents_list = results.get("documents", [[]])[0]
                metadatas_list = results.get("metadatas", [[]])[0]
                distances_list = results.get("distances", [[]])[0]

                for idx, doc_id in enumerate(ids_list):
                    # Get distance and convert to similarity score
                    distance = distances_list[idx] if idx < len(distances_list) else None
                    if distance is not None:
                        # Get metric from collection metadata (saved during creation)
                        metric = metadata.get("distance_metric", "cosine")

                        # Convert distance to similarity score based on metric
                        if metric == "cosine":
                            # ChromaDB cosine distance ranges from 0 to 2
                            # Convert to similarity: (2.0 - distance) / 2.0
                            score = convert_cosine_distance(distance)
                        elif metric == "l2":
                            # L2 distance, convert to similarity: (max_dist - distance) / max_dist
                            score = convert_l2_squared(distance)
                        else:  # ip (inner product)
                            # ChromaDB IP distance = 1 - inner_product, range [0, 2]
                            # Convert to similarity: max(0, min(1, (2.0 - distance) / 2.0))
                            score = convert_ip_distance(distance)
                    else:
                        score = 0.0

                    # Build fields dictionary with user-defined field names
                    text = documents_list[idx] if idx < len(documents_list) else ""
                    metadata = metadatas_list[idx] if idx < len(metadatas_list) else {}
                    if not isinstance(metadata, dict):
                        metadata = {}

                    # Parse JSON strings in metadata
                    parsed_metadata = {}
                    for key, value in metadata.items():
                        if isinstance(value, str):
                            try:
                                parsed_metadata[key] = json.loads(value)
                            except (json.JSONDecodeError, TypeError):
                                parsed_metadata[key] = value
                        else:
                            parsed_metadata[key] = value

                    # Map ChromaDB built-in fields back to user-defined field names
                    fields = {
                        primary_key: str(doc_id),
                        **parsed_metadata,
                    }

                    if text_field:
                        fields[text_field] = text

                    search_results.append(
                        VectorSearchResult(
                            score=score,
                            fields=fields,
                        )
                    )

            return search_results

        return await _search()

    async def delete_docs_by_ids(
        self,
        collection_name: str,
        ids: List[str],
        **kwargs: Any,
    ) -> None:
        """
        Delete documents by their IDs.

        Args:
            collection_name: Name of the collection
            ids: List of document IDs to delete
            **kwargs: Additional parameters for deletion
        """
        collection = self._get_collection(collection_name)

        async def _delete():
            await anyio.to_thread.run_sync(
                lambda: collection.delete(ids=ids)
            )
            store_logger.info(
                "Deleted documents",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name,
                data_num=len(ids)
            )

        async with self._task_manager.task_group():
            await self._task_manager.create_task(
                _delete(),
                name="delete_docs_by_ids"
            )

    async def delete_docs_by_filters(
        self,
        collection_name: str,
        filters: Dict[str, Any],
        **kwargs: Any,
    ) -> None:
        """
        Delete documents by scalar field filters.

        Args:
            collection_name: Name of the collection
            filters: Dictionary of scalar field filters (equality only)
            **kwargs: Additional parameters for deletion
        """
        collection = self._get_collection(collection_name)

        async def _delete():
            # Build where filter for ChromaDB (equality filters only)
            where = filters
            await anyio.to_thread.run_sync(
                lambda: collection.delete(where=where)
            )
            store_logger.info(
                "Deleted documents matching filters",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name
            )

        async with self._task_manager.task_group():
            await self._task_manager.create_task(
                _delete(),
                name="delete_docs_by_filters"
            )

    async def list_collection_names(self) -> List[str]:
        return [c.name for c in self._client.list_collections()]

    async def get_all_documents(self, collection_name: str) -> List[Dict[str, Any]]:
        """
        Retrieve all documents from a collection for migration purposes.

        Args:
            collection_name: Name of the collection to retrieve documents from

        Returns:
            List of documents from the collection
        """
        collection = self._get_collection(collection_name)
        metadata = collection.metadata or {}
        field_mapping_str = metadata.get("field_mapping", "{}")
        field_mapping = json.loads(field_mapping_str)

        primary_key = field_mapping.get("primary_key", "id")
        vector_field = field_mapping.get("vector_field", "embedding")
        text_field = field_mapping.get("text_field", "text")

        async def _get_all_documents():
            return await anyio.to_thread.run_sync(
                lambda: collection.get(
                    include=["documents", "metadatas", "embeddings", "uris"]
                )
            )
        async with self._task_manager.task_group():
            task = await self._task_manager.create_task(
                _get_all_documents(),
                name="get_all_documents"
            )
        results = task.result

        documents = []
        ids = results.get("ids", [])
        documents_list = results.get("documents", [])
        metadatas = results.get("metadatas", [])
        embeddings = results.get("embeddings", [])

        for i, doc_id in enumerate(ids):
            doc = {
                primary_key: doc_id,
                text_field: documents_list[i] if i < len(documents_list) else "",
                vector_field: embeddings[i].tolist() if i < len(embeddings) else [],
            }

            # Add metadata if available
            if i < len(metadatas) and metadatas[i]:
                doc.update(metadatas[i])

            documents.append(doc)

        return documents


    async def get_collection_metadata(self, collection_name: str) -> Dict[str, Any]:
        """
        Get the metadata of a collection (e.g., distance_metric).
        This is crucial for migration operations to preserve index settings.

        Args:
            collection_name: The name of the collection to get metadata from.

        Returns:
            A dictionary containing the collection's metadata.
        """
        collection = self._get_collection(collection_name)
        metadata = collection.metadata or {}
        if "distance_metric" not in metadata:
            metadata["distance_metric"] = "cosine"
        if "schema_version" not in metadata:
            metadata["schema_version"] = 0
        return metadata

    async def _execute_migration(
        self,
        collection_name: str,
        new_schema: CollectionSchema,
        transform_func: Callable[[Dict[str, Any]], Dict[str, Any]],
        new_collection_kwargs: Dict[str, Any],
    ):
        """
        A generic helper to perform a schema migration on a collection for ChromaDB.

        ChromaDB doesn't support schema changes directly, so this
        implementation creates a temporary collection, migrates data, and then replaces
        the old collection with the new one.

        Args:
            collection_name: The name of the original collection.
            new_schema: The schema for the new collection.
            transform_func: A function to apply to each document during migration.
            new_collection_kwargs: Keyword arguments for creating the new collection (e.g., distance_metric).
        """
        # Generate a temporary collection name
        temp_collection_name = f"{collection_name}_migration_{int(time.time())}"
        store_logger.info(
            f"Starting migration for '{collection_name}'. New collection: '{temp_collection_name}'.",
            event_type=LogEventType.STORE_UPDATE,
            table_name=collection_name
        )

        try:
            # Create the new temporary collection
            await self.create_collection(
                temp_collection_name,
                new_schema,
                distance_metric=new_collection_kwargs.get("distance_metric", "cosine")
            )

            # Get all documents from the old collection
            old_collection_data = await self.get_all_documents(collection_name)

            # Transform and re-add all documents to the temporary collection
            if old_collection_data:
                transformed_docs = []
                for doc in old_collection_data:
                    transformed_doc = transform_func(doc)
                    transformed_docs.append(transformed_doc)

                await self.add_docs(temp_collection_name, transformed_docs)

            # Drop the old collection
            await self.delete_collection(collection_name)

            # Rename the temporary collection to the original name
            # ChromaDB doesn't support rename directly, so we need to:
            # 1. Create a new collection with the original name using data from temp collection
            # 2. Delete the temporary collection
            temp_collection_data = await self.get_all_documents(temp_collection_name)
            await self.create_collection(
                collection_name,
                new_schema,
                distance_metric=new_collection_kwargs.get("distance_metric", "cosine")
            )
            if temp_collection_data:
                await self.add_docs(collection_name, temp_collection_data)
            await self.delete_collection(temp_collection_name)

            store_logger.info(f"Migration for '{collection_name}' completed successfully.",
                              event_type=LogEventType.STORE_UPDATE, table_name=collection_name)

        except Exception as e:
            store_logger.error(
                f"Migration for '{collection_name}' failed: {e}. Cleaning up temporary collection.",
                event_type=LogEventType.STORE_UPDATE,
                table_name=collection_name,
                exception=str(e)
            )
            # Clean up the temporary collection if it exists
            if await self.collection_exists(temp_collection_name):
                await self.delete_collection(temp_collection_name)
            raise

    async def update_schema(self, collection_name: str, operations: List[BaseOperation]):
        """
        Apply a list of schema migration operations to a collection.

        This method processes all operations in batch, applying the necessary
        changes to the collection schema in a single data migration. Supported operations include:
        - AddScalarFieldOperation: Add a new scalar field to the collection
        - RenameScalarFieldOperation: Rename an existing scalar field
        - UpdateScalarFieldTypeOperation: Change the data type of a scalar field
        - UpdateEmbeddingDimensionOperation: Modify the dimension of vector embeddings

        Args:
            collection_name: The name of the collection to modify.
            operations: A list of migration operations to apply.

        Raises:
            Error: If an operation fails or is not supported.
        """
        if not operations:
            return

        # 1. Get current schema
        old_schema = await self.get_schema(collection_name)

        # 2. Compute the final new schema after applying all operations
        new_schema = compute_new_schema(old_schema, operations)

        # 3. Build a unified transform function for all operations
        transform_func = build_transform_func_for_operations(operations)

        # 4. Execute migration once with the final schema and unified transform
        metadata = await self.get_collection_metadata(collection_name)
        await self._execute_migration(collection_name, new_schema, transform_func, metadata)

    async def update_collection_metadata(self, collection_name: str, metadata: Dict[str, Any]) -> None:
        """
        Update the schema version of a collection.
        """
        if "schema_version" in metadata:
            version = metadata["schema_version"]
            if not isinstance(version, int) or version < 0:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg=f"schema_version must be a non-negative integer, got {version}"
                )
        # For ChromaDB, we can store schema version in collection metadata
        collection = self._get_collection(collection_name)
        current_metadata = collection.metadata or {}
        current_metadata.update(metadata)
        collection.modify(name=collection_name, metadata=current_metadata)