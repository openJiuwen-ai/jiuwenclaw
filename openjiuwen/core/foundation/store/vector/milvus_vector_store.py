# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import json
import time
from typing import Any, Dict, List, Optional, Union, Callable

import anyio
from pymilvus import DataType as MilvusDataType, AsyncMilvusClient, MilvusException

from openjiuwen.core.common.logging import store_logger, LogEventType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.task_manager.manager import get_task_manager
from openjiuwen.core.foundation.store.base_vector_store import (
    BaseVectorStore,
    VectorSearchResult,
    CollectionSchema,
    VectorDataType,
    FieldSchema,
)
from openjiuwen.core.foundation.store.vector.utils import (
    convert_cosine_similarity,
    convert_l2_squared,
    convert_ip_similarity,
)
from openjiuwen.core.memory.migration.operation.base_operation import BaseOperation
from openjiuwen.core.foundation.store.vector.utils import (
    compute_new_schema,
    build_transform_func_for_operations
)


class MilvusVectorStore(BaseVectorStore):
    """
    Milvus vector store implementation.

    This class implements BaseVectorStore interface using Milvus as the backend.
    """

    def __init__(
        self,
        milvus_uri: str,
        milvus_token: Optional[str] = None,
        database_name: str = "default",
        **kwargs: Any,
    ):
        """
        Initialize MilvusVectorStore.

        The Milvus client is created lazily when first needed, not during initialization.
        This allows the store to be instantiated even when Milvus is temporarily unavailable.

        Args:
            milvus_uri: Milvus URI (e.g., "http://localhost:19530")
            milvus_token: Milvus token for authentication (optional)
            database_name: Name of the database. Defaults to "default".
            **kwargs: Additional parameters for Milvus client initialization.
        """
        self.milvus_uri = milvus_uri
        self.milvus_token = milvus_token
        self.database_name = database_name
        self._kwargs = kwargs
        self._task_manager = get_task_manager()
        # Client will be created lazily on first access
        self._client: Optional[AsyncMilvusClient] = None

        # Cache for collections metadata (distance metrics, etc.)
        self._collection_metadata: Dict[str, Dict[str, Any]] = {}

        # Cache for which collections are loaded
        self._collections_loaded: set[str] = set()

    async def client(self) -> AsyncMilvusClient:
        """
        Get or create the Milvus client lazily.

        The client is created on first access and reused for subsequent operations.

        Returns:
            AsyncMilvusClient: The Milvus client instance.

        Raises:
            MilvusException: If connection to Milvus fails.
        """
        if self._client is None:
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    self._create_client(
                        database_name=self.database_name,
                        path_or_uri=self.milvus_uri,
                        token=self.milvus_token or "",
                        **self._kwargs,
                    ),
                )
            self._client = task.result
            store_logger.info(
                "Successfully connected to AsyncMilvus",
                event_type=LogEventType.STORE_RETRIEVE,
                table_name=self.database_name
            )
        return self._client

    @staticmethod
    async def _create_client(
        database_name: str,
        path_or_uri: str,
        token: str = "",
        **kwargs: Any,
    ) -> AsyncMilvusClient:
        """Create Milvus client and ensure database exists."""
        client = AsyncMilvusClient(uri=path_or_uri, token=token, timeout=3, **kwargs)
        if database_name and database_name != "default":
            if database_name not in await client.list_databases():
                await client.create_database(database_name)
            await client.use_database(database_name)
        return client

    def close(self):
        """
        Close the Milvus client connection.

        This method releases the client resource. After calling close(),
        the client will be recreated on the next operation.
        """
        if self._client is not None:
            self._client = None
            store_logger.info(
                "Milvus client connection closed",
                event_type=LogEventType.STORE_DELETE,
            )

    def _map_field_type(self, field_type: "VectorDataType") -> MilvusDataType:
        """Map our VectorDataType to Milvus DataType"""
        type_mapping = {
            VectorDataType.VARCHAR: MilvusDataType.VARCHAR,
            VectorDataType.FLOAT_VECTOR: MilvusDataType.FLOAT_VECTOR,
            VectorDataType.INT64: MilvusDataType.INT64,
            VectorDataType.INT32: MilvusDataType.INT32,
            VectorDataType.FLOAT: MilvusDataType.FLOAT,
            VectorDataType.DOUBLE: MilvusDataType.DOUBLE,
            VectorDataType.BOOL: MilvusDataType.BOOL,
            VectorDataType.JSON: MilvusDataType.JSON,
        }
        if field_type not in type_mapping:
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                error_msg=f"unsupported field type, field_type={field_type}"
            )
        return type_mapping[field_type]

    def _build_filter_expr(self, filters: Dict[str, Any]) -> Optional[str]:
        """Build Milvus filter expression from filters dictionary (equality only)"""
        if not filters:
            return None

        filter_parts = []
        for key, value in filters.items():
            if isinstance(value, str):
                filter_parts.append(f'{key} == "{value}"')
            else:
                filter_parts.append(f"{key} == {value}")

        return " && ".join(filter_parts) if filter_parts else None

    async def create_collection(
        self,
        collection_name: str,
        schema: Union[CollectionSchema, Dict[str, Any]],
        **kwargs: Any,
    ) -> None:
        """
        Create a new collection with specified schema.

        If the collection already exists, this method does nothing.

        Args:
            collection_name: Name of the collection to create
            schema: CollectionSchema instance or schema dictionary
            **kwargs: Additional parameters for collection creation
                - distance_metric (str): Distance metric for vector search (default: "COSINE")
                  Options: "COSINE", "L2", "IP"
                - index_type (str): Index type for vector field (default: "AUTOINDEX")
        """
        # Check if collection already exists
        client = await self.client()
        async with self._task_manager.task_group():
            task = await self._task_manager.create_task(
                client.has_collection(collection_name)
            )
        has_collection = task.result
        if has_collection:
            store_logger.info(
                "Collection already exists, skipping creation",
                event_type=LogEventType.STORE_ADD,
                table_name=collection_name
            )
            return

        distance_metric = kwargs.get("distance_metric", "COSINE").upper()
        index_type = kwargs.get("index_type", "AUTOINDEX")

        # Convert dict to CollectionSchema if needed
        if isinstance(schema, dict):
            schema = CollectionSchema.from_dict(schema)

        async def _create():
            # Build Milvus schema
            client = await self.client()
            milvus_schema = client.create_schema(
                enable_dynamic_field=schema.enable_dynamic_field,
                description=schema.description or "",
            )
            client = await self.client()
            index_params = client.prepare_index_params()

            vector_field_name = None
            vector_dim = None

            # Process fields from schema
            for field in schema.fields:
                field_name = field.name
                is_primary = field.is_primary
                auto_id = field.auto_id

                # Map our VectorDataType to Milvus DataType
                milvus_type = self._map_field_type(field.dtype)

                # Handle vector field
                if milvus_type == MilvusDataType.FLOAT_VECTOR:
                    vector_dim = field.dim
                    if not vector_dim:
                        raise build_error(
                            StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                            error_msg=f"dim of vector field is missing, field={field_name}, dim={vector_dim}"
                        )
                    vector_field_name = field_name

                    milvus_schema.add_field(
                        field_name=field_name,
                        datatype=milvus_type,
                        dim=vector_dim,
                    )

                    # Add vector index
                    index_params.add_index(
                        field_name=field_name,
                        index_type=index_type,
                        metric_type=distance_metric,
                    )
                elif milvus_type == MilvusDataType.VARCHAR:
                    max_length = field.max_length or 65535
                    milvus_schema.add_field(
                        field_name=field_name,
                        datatype=milvus_type,
                        max_length=max_length,
                        is_primary=is_primary,
                        auto_id=auto_id,
                    )

                    # Add inverted index for VARCHAR fields (for filtering)
                    if not is_primary:
                        index_params.add_index(
                            field_name=field_name,
                            index_type="INVERTED",
                        )
                elif milvus_type == MilvusDataType.JSON:
                    milvus_schema.add_field(
                        field_name=field_name,
                        datatype=milvus_type,
                    )
                else:
                    # Other scalar types
                    milvus_schema.add_field(
                        field_name=field_name,
                        datatype=milvus_type,
                        is_primary=is_primary,
                        auto_id=auto_id,
                    )

                    # Add inverted index for scalar fields (for filtering)
                    if not is_primary and milvus_type in (MilvusDataType.INT64, MilvusDataType.INT32):
                        index_params.add_index(
                            field_name=field_name,
                            index_type="INVERTED",
                        )

            if not vector_field_name:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg="schema must contain at least one FLOAT_VECTOR field"
                )
            client = await self.client()
            # Create collection
            async with self._task_manager.task_group():
                await self._task_manager.create_task(
                    client.create_collection(
                        collection_name=collection_name,
                        schema=milvus_schema,
                        index_params=index_params,
                    )
                )

            # Store collection metadata
            self._collection_metadata[collection_name] = {
                "distance_metric": distance_metric,
                "vector_field": vector_field_name,
                "vector_dim": vector_dim,
            }

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
            try:
                client = await self.client()
                async with self._task_manager.task_group():
                    task = await self._task_manager.create_task(
                        client.has_collection(collection_name=collection_name)
                    )
                if not task.result:
                    store_logger.warning(
                        "Collection does not exist",
                        event_type=LogEventType.STORE_DELETE,
                        table_name=collection_name
                    )
                    return
                client = await self.client()
                async with self._task_manager.task_group():
                    await self._task_manager.create_task(
                        client.drop_collection(collection_name=collection_name)
                    )
                if collection_name in self._collection_metadata:
                    del self._collection_metadata[collection_name]
                store_logger.info(
                    "Deleted collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name
                )
            except MilvusException as e:
                # Handle both direct MilvusException and MilvusException within ExceptionGroup
                # e.value contains the actual MilvusException
                store_logger.error(
                    "Failed to delete collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name,
                    exception=str(e.exceptions)
                )
                raise
            except ExceptionGroup as eg:
                # Find the MilvusException in the exception group
                for e in eg.exceptions:
                    if isinstance(e, MilvusException):
                        store_logger.error(
                            "Failed to delete collection",
                            event_type=LogEventType.STORE_DELETE,
                            table_name=collection_name,
                            exception=str(e)
                        )
                        raise e from eg
                # If no MilvusException found, re-raise the entire group
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
        async def _check():
            client = await self.client()
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    client.has_collection(collection_name)
                )
            return task.result
        return await _check()

    def _map_milvus_type_to_our_type(self, milvus_type: MilvusDataType) -> VectorDataType:
        """Map Milvus DataType to our VectorDataType."""
        type_mapping = {
            MilvusDataType.VARCHAR: VectorDataType.VARCHAR,
            MilvusDataType.FLOAT_VECTOR: VectorDataType.FLOAT_VECTOR,
            MilvusDataType.INT64: VectorDataType.INT64,
            MilvusDataType.INT32: VectorDataType.INT32,
            MilvusDataType.FLOAT: VectorDataType.FLOAT,
            MilvusDataType.DOUBLE: VectorDataType.DOUBLE,
            MilvusDataType.BOOL: VectorDataType.BOOL,
            MilvusDataType.JSON: VectorDataType.JSON,
        }
        if milvus_type not in type_mapping:
            # For unsupported types, return a default or raise
            store_logger.warning(
                f"Unsupported Milvus type: {milvus_type}, defaulting to VARCHAR",
                event_type=LogEventType.STORE_RETRIEVE
            )
            return VectorDataType.VARCHAR
        return type_mapping[milvus_type]

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
            MilvusException: If getting schema fails
        """
        async def _get_schema():
            client = await self.client()
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    client.has_collection(collection_name)
                )
            # Check if collection exists
            if not task.result:
                raise build_error(StatusCode.STORE_VECTOR_COLLECTION_NOT_FOUND, collection_name=collection_name)

            # Get collection description from Milvus
            client = await self.client()
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    client.describe_collection(collection_name=collection_name)
                )
            collection_info = task.result

            # Extract schema information
            schema = CollectionSchema(
                description=collection_info.get("description", ""),
                enable_dynamic_field=collection_info.get("enable_dynamic_field", False),
            )

            # Process fields
            for field_info in collection_info.get("fields", []):
                field_name = field_info.get("name")
                field_type = field_info.get("type")

                # Map Milvus type to our type
                # field_type might be a DataType enum or string
                if isinstance(field_type, str):
                    try:
                        our_type = VectorDataType(field_type.upper())
                    except ValueError:
                        # If not in our enum, try to map from known Milvus types
                        our_type = VectorDataType.VARCHAR  # Default
                else:
                    # It's a MilvusDataType enum
                    our_type = self._map_milvus_type_to_our_type(field_type)

                # Create field schema
                # Handle both direct fields and nested params (different Milvus versions)
                max_length = field_info.get("max_length") or field_info.get("params", {}).get("max_length")
                dim = field_info.get("dim") or field_info.get("params", {}).get("dim")

                field = FieldSchema(
                    name=field_name,
                    dtype=our_type,
                    is_primary=field_info.get("is_primary", False),
                    auto_id=field_info.get("auto_id", False),
                    max_length=max_length,
                    dim=dim,
                    description=field_info.get("description"),
                )
                schema.add_field(field)

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
        await self._ensure_loaded(collection_name)
        batch_size = kwargs.get("batch_size", 128)
        if batch_size <= 0:
            batch_size = 128

        # Get collection metadata to know vector field name
        if collection_name not in self._collection_metadata:
            # Try to get from existing collection
            try:
                client = await self.client()
                async with self._task_manager.task_group():
                    task = await self._task_manager.create_task(
                        client.describe_collection(collection_name=collection_name)
                    )
                collection_info = task.result
                # Extract vector field from schema
                vector_field = None
                for field in collection_info.get("fields", []):
                    if field.get("type") == "FLOAT_VECTOR":
                        vector_field = field.get("name")
                        break
                if vector_field:
                    self._collection_metadata[collection_name] = {
                        "vector_field": vector_field,
                    }
            except Exception as e:
                store_logger.warning(
                    "Could not get collection metadata",
                    event_type=LogEventType.STORE_ADD,
                    table_name=collection_name,
                    exception=str(e)
                )

        async def _add_batch(batch: List[Dict[str, Any]]):
            client = await self.client()
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    client.insert(
                        collection_name=collection_name,
                        data=batch,
                    )
                )

        # Process in batches
        total = len(docs)
        processed = 0
        for i in range(0, total, batch_size):
            batch = docs[i: i + batch_size]
            await _add_batch(batch)
            processed += len(batch)
            if processed % 100 == 0:
                store_logger.info(
                    f"Added {processed}/{total} documents to collection",
                    event_type=LogEventType.STORE_ADD,
                    table_name=collection_name,
                    data_num=processed
                )

        # Flush to ensure data is persisted
        client = await self.client()
        async with self._task_manager.task_group():
            await self._task_manager.create_task(
                client.flush(collection_name=collection_name)
            )
        store_logger.info(
            "Successfully added documents collection",
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
            vector_field: Name of the vector field to search against (e.g., "embedding")
            top_k: Number of most relevant documents to return
            filters: Optional dictionary of scalar field filters for filtering results
            **kwargs: Additional search parameters
                - metric_type (str, optional): Distance metric override
                - output_fields (List[str], optional): Fields to return in results

        Returns:
            List of VectorSearchResult objects
        """
        # Get collection metadata
        await self._ensure_loaded(collection_name)
        collection_meta = self._collection_metadata.get(collection_name, {})
        distance_metric = kwargs.get("metric_type") or collection_meta.get("distance_metric", "COSINE")
        output_fields = kwargs.get("output_fields")

        # Build filter expression
        filter_expr = self._build_filter_expr(filters) if filters else None

        async def _search():
            # Determine output fields
            search_output_fields = output_fields
            if not search_output_fields:
                # Try to get collection schema to determine output fields
                try:
                    client = await self.client()
                    async with self._task_manager.task_group():
                        task = await self._task_manager.create_task(
                            client.describe_collection(collection_name=collection_name)
                        )
                    collection_info = task.result
                    search_output_fields = [field.get("name") for field in collection_info.get("fields", [])]
                except Exception:
                    # Fallback: use common field names
                    search_output_fields = ["id", "text", "metadata"]

            # Execute search
            client = await self.client()
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    client.search(
                        collection_name=collection_name,
                        data=[query_vector],
                        anns_field=vector_field,
                        limit=top_k,
                        output_fields=search_output_fields or [],
                        search_params={"metric_type": distance_metric},
                        filter=filter_expr,
                    )
                )
            results = task.result

            return results

        async with self._task_manager.task_group():
            task = await self._task_manager.create_task(_search())
        results = task.result

        # Convert results to VectorSearchResult
        search_results = []
        if results and len(results) > 0:
            # Milvus returns results as a list of hits per query
            hits = results[0] if isinstance(results[0], list) else results

            for hit in hits:
                # Extract score/distance
                distance = hit.get("distance")
                score = hit.get("score")

                # Convert distance to similarity score if needed
                if score is not None:
                    final_score = float(score)
                elif distance is not None:
                    # Convert distance to similarity score based on metric
                    distance_val = float(distance)
                    if distance_metric == "COSINE":
                        # Milvus COSINE returns similarity in [-1, 1]
                        # Convert to [0, 1]: (distance + 1) / 2
                        final_score = convert_cosine_similarity(distance_val)
                    elif distance_metric == "L2":
                        # Milvus L2 returns squared L2 distance
                        # Convert to [0, 1]: (max_dist - distance) / max_dist
                        final_score = convert_l2_squared(distance_val)
                    else:  # IP (Inner Product)
                        # Milvus IP returns raw inner product (unbounded)
                        # Convert to [0, 1]: max(0, min(1, (distance + 1) / 2))
                        final_score = convert_ip_similarity(distance_val)
                else:
                    final_score = 0.0

                # Extract all fields from hit
                fields = {}
                # Get entity fields
                entity = hit.get("entity", {})
                for key, value in entity.items():
                    # Handle JSON fields
                    if isinstance(value, str):
                        try:
                            value = json.loads(value)
                        except (json.JSONDecodeError, TypeError):
                            pass
                    fields[key] = value

                # Also include id if present in hit
                if "id" in hit:
                    fields["id"] = hit["id"]
                elif "pk" in hit:
                    fields["id"] = str(hit["pk"])

                search_results.append(
                    VectorSearchResult(
                        score=final_score,
                        fields=fields,
                    )
                )

        return search_results

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
        if not ids:
            store_logger.warning(
                "No IDs provided for deletion",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name
            )
            return
        await self._ensure_loaded(collection_name)

        async def _delete():
            try:
                client = await self.client()
                async with self._task_manager.task_group():
                    task = await self._task_manager.create_task(
                        client.delete(
                            collection_name=collection_name,
                            ids=ids,
                        )
                    )
                result = task.result
                # Flush to ensure deletion is persisted
                client = await self.client()
                async with self._task_manager.task_group():
                    await self._task_manager.create_task(
                        client.flush(collection_name=collection_name)
                    )
                deleted_count = result.get("delete_count", 0) if isinstance(result, dict) else len(ids)
                store_logger.info(
                    "Deleted documents from collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name,
                    data_num=deleted_count
                )
            except MilvusException as e:
                # Handle both direct MilvusException and MilvusException within ExceptionGroup
                # e.value contains the actual MilvusException
                store_logger.error(
                    "Failed to delete collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name,
                    exception=str(e.value)
                )
                raise
            except ExceptionGroup as eg:
                # Find the MilvusException in the exception group
                for e in eg.exceptions:
                    if isinstance(e, MilvusException):
                        store_logger.error(
                            "Failed to delete collection",
                            event_type=LogEventType.STORE_DELETE,
                            table_name=collection_name,
                            exception=str(e)
                        )
                        raise e from eg
                # If no MilvusException found, re-raise the entire group
                raise

        await _delete()

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
            filters: Dictionary of scalar field filters for matching documents to delete
            **kwargs: Additional parameters for deletion
        """
        if not filters:
            store_logger.warning(
                "No filters provided for deletion",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name
            )
            return
        await self._ensure_loaded(collection_name)

        # Build filter expression
        filter_expr = self._build_filter_expr(filters)

        async def _delete():
            try:
                client = await self.client()
                async with self._task_manager.task_group():
                    task = await self._task_manager.create_task(
                        client.delete(
                            collection_name=collection_name,
                            filter=filter_expr,
                        )
                    )
                result = task.result
                # Flush to ensure deletion is persisted
                client = await self.client()
                async with self._task_manager.task_group():
                    await self._task_manager.create_task(
                        client.flush(collection_name=collection_name)
                    )
                deleted_count = result.get("delete_count", 0) if isinstance(result, dict) else 0
                store_logger.info(
                    "Deleted documents matching filters from collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name,
                    data_num=deleted_count
                )
            except MilvusException as e:
                # Handle both direct MilvusException and MilvusException within ExceptionGroup
                # e.value contains the actual MilvusException
                store_logger.error(
                    "Failed to delete collection",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name,
                    exception=str(e.exceptions)
                )
                raise
            except ExceptionGroup as eg:
                # Find the MilvusException in the exception group
                for e in eg.exceptions:
                    if isinstance(e, MilvusException):
                        store_logger.error(
                            "Failed to delete collection",
                            event_type=LogEventType.STORE_DELETE,
                            table_name=collection_name,
                            exception=str(e)
                        )
                        raise e from eg
                # If no MilvusException found, re-raise the entire group
                raise

        await _delete()

    async def _ensure_loaded(self, collection: str) -> None:
        """Ensure a collection is loaded"""
        if collection in self._collections_loaded:
            return
        client = await self.client()
        if await client.has_collection(collection, timeout=15.0):
            store_logger.info(
                "MilvusVectorStore: loading collection %s", collection, event_type=LogEventType.STORE_LOAD,
            )
            client = await self.client()
            await client.load_collection(collection, timeout=180.0)
            store_logger.info(
                "MilvusVectorStore: %s collection loaded", collection, event_type=LogEventType.STORE_LOAD,
            )
            self._collections_loaded.add(collection)

    async def get_collection_metadata(self, collection_name: str) -> Dict[str, Any]:
        """
        Gets collection metadata from cache or by describing the collection and its index.
        This is crucial for migration operations to preserve index settings.

        The returned metadata includes:
        - distance_metric: The distance metric used for vector search (e.g., "COSINE", "L2", "IP")
        - vector_field: The name of the vector field
        - schema_version: The schema version stored in collection properties (0 if not set)

        Args:
            collection_name: Name of the collection to get metadata for

        Returns:
            Dict containing collection metadata with keys: distance_metric, vector_field, schema_version

        Raises:
            StoreError: If collection not found or operation fails
        """
        if collection_name in self._collection_metadata:
            metadata = self._collection_metadata[collection_name].copy()
            # Ensure schema_version is always present
            if "schema_version" not in metadata:
                # Fetch schema_version from Milvus
                try:
                    schema_version = await self._get_schema_version_from_milvus(collection_name)
                    metadata["schema_version"] = schema_version
                except Exception:
                    metadata["schema_version"] = 0
            return metadata

        store_logger.debug(f"Cache miss for '{collection_name}' metadata. Describing collection.",
                           event_type=LogEventType.STORE_RETRIEVE, table_name=collection_name)
        try:
            # This is a sync call, needs to be in a thread
            async def _describe() -> Dict[str, Any]:
                client = await self.client()
                async with self._task_manager.task_group():
                    task = await self._task_manager.create_task(
                        client.describe_collection(collection_name)
                    )
                collection_info = task.result
                vector_field_name = None
                for f in collection_info.get("fields", []):
                    if f.get("type") == MilvusDataType.FLOAT_VECTOR:
                        vector_field_name = f.get("name")
                        break

                if not vector_field_name:
                    # No vector field, return default metric
                    return {"distance_metric": "COSINE"}

                # The default index name is the field name
                client = await self.client()
                async with self._task_manager.task_group():
                    task = await self._task_manager.create_task(
                        client.describe_index(collection_name, index_name=vector_field_name)
                    )
                index_info = task.result
                metric_type = index_info["metric_type"]
                return {"distance_metric": metric_type, "vector_field": vector_field_name}

            metadata = await _describe()

            # Fetch schema_version from collection properties
            try:
                schema_version = await self._get_schema_version_from_milvus(collection_name)
                metadata["schema_version"] = schema_version
            except Exception:
                metadata["schema_version"] = 0

            self._collection_metadata[collection_name] = metadata
            return metadata
        except (MilvusException, ExceptionGroup) as e:
            if isinstance(e, ExceptionGroup):
                # Find MilvusException in the group
                for exc in e.exceptions:
                    if isinstance(exc, MilvusException):
                        store_logger.warning(
                            f"Could not describe index for collection '{collection_name}': {exc}.\
                            Falling back to defaults.",
                            event_type=LogEventType.STORE_RETRIEVE,
                            table_name=collection_name
                        )
                        return {"distance_metric": "COSINE", "schema_version": 0}
                # If no MilvusException found, re-raise the entire group
                raise
            else:
                # Direct MilvusException
                store_logger.warning(
                    f"Could not describe index for collection '{collection_name}': {e}. Falling back to defaults.",
                    event_type=LogEventType.STORE_RETRIEVE,
                    table_name=collection_name
                )
                return {"distance_metric": "COSINE", "schema_version": 0}

    async def _get_schema_version_from_milvus(self, collection_name: str) -> int:
        """Helper method to get schema version from Milvus collection properties."""
        client = await self.client()
        async with self._task_manager.task_group():
            task = await self._task_manager.create_task(
                client.describe_collection(collection_name)
            )
        collection_info = task.result
        properties = collection_info.get("properties", {})
        return int(properties.get("schema_version", 0))

    async def _execute_migration(
            self,
            collection_name: str,
            new_schema: "CollectionSchema",
            transform_func: Callable[[Dict[str, Any]], Dict[str, Any]],
            new_collection_kwargs: Dict[str, Any],
    ):
        """
        A generic helper to perform a schema migration on a collection.

        This process is resource-intensive and involves creating a new collection,
        streaming and transforming data, and then replacing the old collection.

        Args:
            collection_name: The name of the original collection.
            new_schema: The schema for the new collection.
            transform_func: A function to apply to each document during migration.
            new_collection_kwargs: Keyword arguments for creating the new collection (e.g., distance_metric).
        """
        # get new collection name
        temp_collection_name = f"{collection_name}_migration_{int(time.time())}"
        store_logger.info(
            f"Starting migration for '{collection_name}'. New collection: '{temp_collection_name}'.",
            event_type=LogEventType.STORE_UPDATE,
            table_name=collection_name
        )

        try:
            # Create the new collection
            await self.create_collection(
                temp_collection_name,
                new_schema,
                **new_collection_kwargs
            )
            # Stream data from the old collection, transform, and insert into the new one
            store_logger.info(f"Starting data copy from '{collection_name}' to '{temp_collection_name}'.",
                              event_type=LogEventType.STORE_UPDATE, table_name=collection_name)
            client = await self.client()
            async with self._task_manager.task_group():
                await self._task_manager.create_task(
                    client.load_collection(collection_name)
                )
            client = await self.client()
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    client.query_iterator(
                        collection_name=collection_name,
                        filter="",
                        output_fields=["*"]
                    )
                )
            iterator = task.result

            batch = []
            batch_size = 100  # A reasonable default batch size
            total_docs = 0
            while True:
                async with self._task_manager.task_group():
                    task = await self._task_manager.create_task(
                        iterator.next()
                    )
                doc_batch = task.result
                if not doc_batch:
                    break
                for doc in doc_batch:
                    transformed_doc = transform_func(doc)
                    batch.append(transformed_doc)
                    if len(batch) >= batch_size:
                        await self.add_docs(temp_collection_name, batch)
                        total_docs += len(batch)
                        store_logger.debug(f"Migrated {total_docs} documents to '{temp_collection_name}'.",
                                           event_type=LogEventType.STORE_UPDATE, table_name=collection_name)
                        batch = []

            if batch:
                await self.add_docs(temp_collection_name, batch)
                total_docs += len(batch)

            store_logger.info(f"Finished copying {total_docs} documents to '{temp_collection_name}'.",
                              event_type=LogEventType.STORE_UPDATE, table_name=collection_name)
            # Release the old collection to free up memory
            client = await self.client()
            async with self._task_manager.task_group():
                await self._task_manager.create_task(
                    client.release_collection(collection_name)
                )

            # Drop the old collection
            store_logger.info(f"Dropping old collection '{collection_name}'.",
                              event_type=LogEventType.STORE_DELETE, table_name=collection_name)
            await self.delete_collection(collection_name)

            # Rename the new collection to the original name
            store_logger.info(f"Renaming '{temp_collection_name}' to '{collection_name}'.",
                              event_type=LogEventType.STORE_UPDATE, table_name=collection_name)
            client = await self.client()
            async with self._task_manager.task_group():
                await self._task_manager.create_task(
                    client.rename_collection(temp_collection_name, collection_name)
                )

            # Clear the old metadata from the cache
            if collection_name in self._collection_metadata:
                del self._collection_metadata[collection_name]

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

    async def list_collection_names(self) -> List[str]:
        client = await self.client()
        async with self._task_manager.task_group():
            task = await self._task_manager.create_task(
                client.list_collections()
            )
        return task.result

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

    async def update_collection_metadata(
        self,
        collection_name: str,
        metadata: Dict[str, Any],
    ) -> None:
        """
        Update collection metadata in Milvus collection properties.

        This method updates the collection properties in Milvus and also updates
        the local cache to keep them in sync.

        Args:
            collection_name: Name of the collection to update
            metadata: Dictionary of metadata key-value pairs to update.
                    Supported keys include:
                    - schema_version: int, the schema version number
                    - Other custom properties (all values will be converted to strings)

        Raises:
            StoreError: If collection not found or update fails

        Example:
            await vector_store.update_collection_metadata(
                "my_collection",
                {"schema_version": 3}
            )
        """
        if not metadata:
            return

        # Validate schema_version if present
        if "schema_version" in metadata:
            version = metadata["schema_version"]
            if not isinstance(version, int) or version < 0:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg=f"schema_version must be a non-negative integer, got {version}"
                )

        # Check if collection exists
        try:
            client = await self.client()
            async with self._task_manager.task_group():
                task = await self._task_manager.create_task(
                    client.describe_collection(collection_name=collection_name)
                )
        except MilvusException as e:
            raise build_error(
                StatusCode.STORE_VECTOR_COLLECTION_NOT_FOUND,
                collection_name=collection_name,
                error_msg=str(e)
            ) from e
        except ExceptionGroup as eg:
            # Find the MilvusException in the exception group
            for e in eg.exceptions:
                if isinstance(e, MilvusException):
                    store_logger.error(
                        "Failed to delete collection",
                        event_type=LogEventType.STORE_DELETE,
                        table_name=collection_name,
                        exception=str(e)
                    )
                    raise e from eg
            # If no MilvusException found, re-raise the entire group
            raise

        # Convert all metadata values to strings for Milvus properties
        # Milvus only supports string values in properties
        properties_to_update = {}
        for key, value in metadata.items():
            properties_to_update[key] = str(value)

        # Update properties in Milvus
        try:
            client = await self.client()
            async with self._task_manager.task_group():
                await self._task_manager.create_task(
                    client.alter_collection_properties(
                        collection_name=collection_name,
                        properties=properties_to_update,
                    )
                )
        except MilvusException as e:
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                collection_name=collection_name,
                error_msg=f"failed to update collection metadata: {e}"
            ) from e
        except ExceptionGroup as eg:
            # Find the MilvusException in the exception group
            for e in eg.exceptions:
                if isinstance(e, MilvusException):
                    store_logger.error(
                        "Failed to delete collection",
                        event_type=LogEventType.STORE_DELETE,
                        table_name=collection_name,
                        exception=str(e)
                    )
                    raise e from eg
            # If no MilvusException found, re-raise the entire group
            raise

        # Update local cache if present
        if collection_name in self._collection_metadata:
            self._collection_metadata[collection_name].update(metadata)

        store_logger.debug(
            f"Updated collection metadata for '{collection_name}': {metadata}",
            event_type=LogEventType.STORE_UPDATE,
            table_name=collection_name
        )
