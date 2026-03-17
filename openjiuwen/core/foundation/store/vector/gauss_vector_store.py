# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
import asyncio
import json
import re
from typing import Any, Dict, List, Optional, Union, Callable

import psycopg2

from openjiuwen.core.common.logging import store_logger, LogEventType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
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
from openjiuwen.core.foundation.store.vector.utils import compute_new_schema, build_transform_func_for_operations


class GaussVectorStore(BaseVectorStore):
    """
    GaussVector store implementation.

    This class implements BaseVectorStore interface using GaussVector as the backend.
    """

    def __init__(
            self,
            host: str = "localhost",
            port: int = 5432,
            database: str = "postgres",
            user: str = "postgres",
            password: str = "",
            **kwargs: Any,
    ):
        """
        Initialize GaussVectorStore.

        The connection is created lazily when first needed, not during initialization.

        Args:
            host: GaussVector server host
            port: GaussVector server port
            database: Database name
            user: Database user
            password: Database password
            **kwargs: Additional connection parameters (e.g., connection_timeout, sslmode)
        """
        self.host = host
        self.port = port
        self.database = database
        self.user = user
        self.password = password
        self._kwargs = kwargs

        self._conn: Optional[Any] = None
        self._collection_metadata: Dict[str, Dict[str, Any]] = {}

    @property
    def connection(self) -> Any:
        """Get or create the database connection lazily."""
        if self._conn is None:
            self._conn = self._create_connection()
            store_logger.info(
                "Successfully connected to GaussVector",
                event_type=LogEventType.STORE_RETRIEVE,
                table_name=self.database
            )
        return self._conn

    def _create_connection(self) -> Any:
        """Create a new database connection."""
        conn_params = {
            "host": self.host,
            "port": self.port,
            "database": self.database,
            "user": self.user,
            "password": self.password,
        }
        conn_params.update(self._kwargs)

        conn = psycopg2.connect(**conn_params)
        conn.autocommit = True
        return conn

    def close(self):
        """Close the database connection."""
        if self._conn is not None:
            self._conn.close()
            self._conn = None
            store_logger.info(
                "GaussVector connection closed",
                event_type=LogEventType.STORE_DELETE,
            )

    def _map_field_type_to_pg(self, field_type: VectorDataType) -> str:
        """Map VectorDataType to GaussVector data type."""
        type_mapping = {
            VectorDataType.VARCHAR: "VARCHAR",
            VectorDataType.FLOAT_VECTOR: "floatvector",
            VectorDataType.INT64: "BIGINT",
            VectorDataType.INT32: "INTEGER",
            VectorDataType.FLOAT: "REAL",
            VectorDataType.DOUBLE: "DOUBLE PRECISION",
            VectorDataType.BOOL: "BOOLEAN",
            VectorDataType.JSON: "JSONB",
        }
        if field_type not in type_mapping:
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                error_msg=f"unsupported field type, field_type={field_type}"
            )
        return type_mapping[field_type]

    def _map_pg_type_to_our_type(self, pg_type: str) -> VectorDataType:
        """Map data type to our VectorDataType."""
        type_mapping = {
            "varchar": VectorDataType.VARCHAR,
            "text": VectorDataType.VARCHAR,
            "floatvector": VectorDataType.FLOAT_VECTOR,
            "bigint": VectorDataType.INT64,
            "integer": VectorDataType.INT32,
            "int": VectorDataType.INT32,
            "real": VectorDataType.FLOAT,
            "double precision": VectorDataType.DOUBLE,
            "boolean": VectorDataType.BOOL,
            "jsonb": VectorDataType.JSON,
        }
        pg_type_lower = pg_type.lower()
        if pg_type_lower in type_mapping:
            return type_mapping[pg_type_lower]
        store_logger.warning(
            f"Unsupported data type: {pg_type}, defaulting to VARCHAR",
            event_type=LogEventType.STORE_RETRIEVE
        )
        return VectorDataType.VARCHAR

    def _build_filter_clause(self, filters: Dict[str, Any]) -> Optional[str]:
        """Build SQL WHERE clause from filters dictionary."""
        if not filters:
            return None

        filter_parts = []
        for key, value in filters.items():
            if isinstance(value, str):
                filter_parts.append(f"{key} = '{value}'")
            elif isinstance(value, bool):
                filter_parts.append(f"{key} = {'TRUE' if value else 'FALSE'}")
            else:
                filter_parts.append(f"{key} = {value}")

        return " AND ".join(filter_parts) if filter_parts else None

    async def create_collection(
            self,
            collection_name: str,
            schema: Union[CollectionSchema, Dict[str, Any]],
            **kwargs: Any,
    ) -> None:
        """
        Create a new collection (table) with specified schema.

        Args:
            collection_name: Name of the collection (table) to create
            schema: CollectionSchema instance or schema dictionary
            **kwargs: Additional parameters
                - distance_metric (str): Distance metric (default: "COSINE")
                  Options: "COSINE", "L2"
                - index_type (str): Index type for vector field (default: "DiskANN")
        """
        cursor = self.connection.cursor()

        try:
            cursor.execute(
                f"SELECT EXISTS (SELECT table_name FROM information_schema.tables WHERE table_name = %s);",
                (collection_name,),
            )
            exists = cursor.fetchone()[0]

            if exists:
                store_logger.info(
                    "Collection already exists, skipping creation",
                    event_type=LogEventType.STORE_ADD,
                    table_name=collection_name
                )
                return

            distance_metric = kwargs.get("distance_metric", "cosine").upper()
            index_type = kwargs.get("index_type", "diskann").lower()

            if isinstance(schema, dict):
                schema = CollectionSchema.from_dict(schema)

            vector_field_name = None
            vector_dim = None

            columns = []
            for field in schema.fields:
                col_name = field.name
                col_type = self._map_field_type_to_pg(field.dtype)

                if field.dtype == VectorDataType.FLOAT_VECTOR:
                    vector_field_name = field.name
                    vector_dim = field.dim
                    if not vector_dim:
                        raise build_error(
                            StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                            error_msg=f"dim of vector field is missing, field={col_name}, dim={vector_dim}"
                        )
                    col_type = f"floatvector({vector_dim})"

                if field.is_primary:
                    if field.auto_id:
                        columns.append(f"{col_name} SERIAL PRIMARY KEY")
                    else:
                        columns.append(f"{col_name} {col_type} PRIMARY KEY")
                else:
                    max_length = field.max_length or 65535
                    if field.dtype == VectorDataType.VARCHAR:
                        columns.append(f"{col_name} VARCHAR({max_length})")
                    else:
                        columns.append(f"{col_name} {col_type}")

            if not vector_field_name:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg="schema must contain at least one FLOAT_VECTOR field"
                )

            create_table_sql = f"CREATE TABLE {collection_name} ({', '.join(columns)});"
            cursor.execute(create_table_sql)

            metric_type_map = {
                "COSINE": "cosine",
                "L2": "l2"
            }
            pg_metric = metric_type_map.get(distance_metric, "cosine")

            if index_type.lower() == "diskann":
                pg_nseg = kwargs.get("pg_nseg", 128)
                pg_nclus = kwargs.get("pg_nclus", 16)
                num_parallels = kwargs.get("num_parallels", 32)
                index_sql = f"""
                CREATE INDEX {collection_name}_{vector_field_name}_idx 
                ON {collection_name} 
                USING GSDISKANN ({vector_field_name} {pg_metric}) 
                WITH (enable_pq = true, pg_nseg = {pg_nseg}, pg_nclus = {pg_nclus},
                num_parallels = {num_parallels}, quantization_type = 'lvq', subgraph_count = 1);
                """
            else:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg="index_type only support DiskANN"
                )
            cursor.execute(index_sql)

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

        except Exception as e:
            raise build_error(StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                              error_msg=f"Failed to create collection: {e}") from e
        finally:
            cursor.close()

    async def delete_collection(
            self,
            collection_name: str,
            **kwargs: Any,
    ) -> None:
        """Delete a collection (table) by name."""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                f"SELECT EXISTS (SELECT table_name FROM information_schema.tables WHERE table_name = %s);",
                (collection_name,),
            )
            exists = cursor.fetchone()[0]

            if not exists:
                store_logger.warning(
                    "Collection does not exist",
                    event_type=LogEventType.STORE_DELETE,
                    table_name=collection_name
                )
                return

            cursor.execute(f"DROP TABLE IF EXISTS {collection_name} CASCADE;")

            if collection_name in self._collection_metadata:
                del self._collection_metadata[collection_name]

            store_logger.info(
                "Deleted collection",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name
            )
        except Exception as e:
            raise build_error(StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                              error_msg=f"Failed to delete collection: {e}") from e
        finally:
            cursor.close()

    async def collection_exists(
            self,
            collection_name: str,
            **kwargs: Any,
    ) -> bool:
        """Check if a collection exists."""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT EXISTS (SELECT table_name FROM information_schema.tables WHERE table_name = %s);",
                (collection_name,)
            )
            return cursor.fetchone()[0]
        finally:
            cursor.close()

    async def get_schema(
            self,
            collection_name: str,
            **kwargs: Any,
    ) -> CollectionSchema:
        """Get the schema of a collection."""
        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT EXISTS (SELECT table_name FROM information_schema.tables WHERE table_name = %s);",
                (collection_name,)
            )
            if not cursor.fetchone()[0]:
                raise build_error(
                    StatusCode.STORE_VECTOR_COLLECTION_NOT_FOUND,
                    collection_name=collection_name
                )

            cursor.execute(
                f"""
                SELECT column_name, data_type, is_nullable, column_default
                FROM information_schema.columns
                WHERE table_name = %s
                ORDER BY ordinal_position;
            """,
                (collection_name,),
            )

            columns = cursor.fetchall()

            cursor.execute(
                f"""
                SELECT kcu.column_name
                FROM information_schema.table_constraints tc
                JOIN information_schema.key_column_usage kcu
                  ON tc.constraint_name = kcu.constraint_name
                WHERE tc.table_name = %s AND tc.constraint_type = 'PRIMARY KEY';
            """,
                (collection_name,),
            )

            primary_keys = [row[0] for row in cursor.fetchall()]

            schema = CollectionSchema(
                description=f"Table {collection_name}",
                enable_dynamic_field=False,
            )

            for col in columns:
                col_name, data_type, is_nullable, column_default = col
                is_primary = col_name in primary_keys
                auto_id = False

                our_type = self._map_pg_type_to_our_type(data_type)

                max_length = None
                dim = None

                if data_type.lower().startswith("varchar"):
                    match = re.search(r"varchar\((\d+)\)", data_type.lower())
                    if match:
                        max_length = int(match.group(1))

                if data_type.lower().startswith("floatvector"):
                    match = re.search(r"floatvector\((\d+)\)", data_type.lower())
                    if match:
                        dim = int(match.group(1))

                field = FieldSchema(
                    name=col_name,
                    dtype=our_type,
                    is_primary=is_primary,
                    auto_id=auto_id,
                    max_length=max_length,
                    dim=dim,
                )
                schema.add_field(field)

            return schema
        finally:
            cursor.close()

    async def add_docs(
            self,
            collection_name: str,
            docs: List[Dict[str, Any]],
            **kwargs: Any,
    ) -> None:
        """Add documents to a collection."""
        batch_size = kwargs.get("batch_size", 128)
        if batch_size <= 0:
            batch_size = 128

        cursor = self.connection.cursor()

        try:
            columns = list(docs[0].keys()) if docs else []
            if not columns:
                return

            placeholders = ", ".join(["%s"] * len(columns))
            column_names = ", ".join(columns)

            total = len(docs)
            processed = 0

            for i in range(0, total, batch_size):
                batch = docs[i: i + batch_size]

                values_list = []
                for doc in batch:
                    row_values = []
                    for col in columns:
                        value = doc.get(col)
                        if isinstance(value, dict):
                            value = json.dumps(value)
                        elif isinstance(value, list) and col == columns[0]:
                            pass
                        elif isinstance(value, (list, tuple)):
                            value = json.dumps(value)
                        row_values.append(value)
                    values_list.append(row_values)

                insert_sql = f"INSERT INTO {collection_name} ({column_names}) VALUES ({placeholders})"
                cursor.executemany(insert_sql, values_list)

                processed += len(batch)
                if processed % 100 == 0:
                    store_logger.info(
                        f"Added {processed}/{total} documents to collection",
                        event_type=LogEventType.STORE_ADD,
                        table_name=collection_name,
                        data_num=processed
                    )

            store_logger.info(
                "Successfully added documents to collection",
                event_type=LogEventType.STORE_ADD,
                table_name=collection_name,
                data_num=total
            )

        except Exception as e:
            raise build_error(
                StatusCode.STORE_VECTOR_DOC_INVALID,
                error_msg=f"Failed to add documents: {e}") from e
        finally:
            cursor.close()

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
        """
        collection_meta = self._collection_metadata.get(collection_name, {})
        distance_metric = kwargs.get("metric_type") or collection_meta.get("distance_metric", "COSINE")

        vector_str = "[" + ",".join(str(x) for x in query_vector) + "]"

        where_clause = ""
        if filters:
            filter_clause = self._build_filter_clause(filters)
            if filter_clause:
                where_clause = f"WHERE {filter_clause}"

        output_fields = kwargs.get("output_fields")
        select_fields = ", ".join(output_fields) if output_fields else "*"

        if output_fields and vector_field not in output_fields:
            select_fields += f", {vector_field}"

        search_sql = f"""
            SELECT {select_fields}, 
                   {vector_field} <-> '{vector_str}'::floatvector AS distance
            FROM {collection_name}
            {where_clause}
            ORDER BY distance
            LIMIT {top_k};
        """

        cursor = self.connection.cursor()
        try:
            cursor.execute(search_sql)
            rows = cursor.fetchall()

            search_results = []
            for row in rows:
                fields = {}
                distance = None

                for idx, desc in enumerate(cursor.description):
                    col_name = desc.name
                    value = row[idx]

                    if col_name == "distance":
                        distance = value
                    else:
                        if isinstance(value, str) and value.startswith("{"):
                            try:
                                value = json.loads(value.replace("'", '"'))
                            except Exception:
                                store_logger.warning(
                                    "Failed to parse JSON value",
                                    event_type=LogEventType.STORE_RETRIEVE,
                                    table_name=collection_name,
                                )
                                pass
                        fields[col_name] = value

                if distance is not None:
                    if distance_metric == "COSINE":
                        final_score = convert_cosine_similarity(1 - distance)
                    elif distance_metric == "L2":
                        final_score = convert_l2_squared(distance)
                    else:
                        final_score = convert_ip_similarity(distance)
                else:
                    final_score = 0.0

                search_results.append(
                    VectorSearchResult(
                        score=final_score,
                        fields=fields,
                    )
                )

            return search_results

        except Exception as e:
            raise build_error(StatusCode.STORE_VECTOR_DOC_INVALID,
                              error_msg=f"Failed to search: {e}") from e
        finally:
            cursor.close()

    async def delete_docs_by_ids(
            self,
            collection_name: str,
            ids: List[str],
            **kwargs: Any,
    ) -> None:
        """Delete documents by their IDs."""
        if not ids:
            store_logger.warning(
                "No IDs provided for deletion",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name
            )
            return

        cursor = self.connection.cursor()
        try:
            id_column = kwargs.get("id_column", "id")

            placeholders = ", ".join(["%s"] * len(ids))
            delete_sql = f"DELETE FROM {collection_name} WHERE {id_column} IN ({placeholders})"

            cursor.execute(delete_sql, ids)

            store_logger.info(
                f"Deleted {len(ids)} documents from collection",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name,
                data_num=len(ids)
            )
        except Exception as e:
            raise build_error(StatusCode.STORE_VECTOR_DOC_INVALID,
                              error_msg=f"Failed to delete documents: {e}") from e
        finally:
            cursor.close()

    async def delete_docs_by_filters(
            self,
            collection_name: str,
            filters: Dict[str, Any],
            **kwargs: Any,
    ) -> None:
        """Delete documents by scalar field filters."""
        if not filters:
            store_logger.warning(
                "No filters provided for deletion",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name
            )
            return

        cursor = self.connection.cursor()
        try:
            filter_clause = self._build_filter_clause(filters)
            if not filter_clause:
                return

            cursor.execute(f"SELECT COUNT(*) FROM {collection_name} WHERE {filter_clause}")
            count = cursor.fetchone()[0]

            delete_sql = f"DELETE FROM {collection_name} WHERE {filter_clause}"
            cursor.execute(delete_sql)

            store_logger.info(
                f"Deleted {count} documents matching filters from collection",
                event_type=LogEventType.STORE_DELETE,
                table_name=collection_name,
                data_num=count
            )
        except Exception as e:
            raise build_error(
                StatusCode.STORE_VECTOR_DOC_INVALID,
                error_msg=f"Failed to delete documents by filters: {e}"
            ) from e
        finally:
            cursor.close()

    async def get_collection_metadata(self, collection_name: str) -> Dict[str, Any]:
        """Get collection metadata."""
        if collection_name in self._collection_metadata:
            return self._collection_metadata.get(collection_name, {})

        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT EXISTS (SELECT table_name FROM information_schema.tables WHERE table_name = %s);",
                (collection_name,)
            )
            if not cursor.fetchone()[0]:
                return {"distance_metric": "COSINE", "schema_version": 0}

            cursor.execute(
                f"""
                SELECT column_name, data_type
                FROM information_schema.columns
                WHERE table_name = %s AND data_type LIKE 'floatvector%%';
            """,
                (collection_name,),
            )

            vector_fields = cursor.fetchall()
            vector_field = vector_fields[0][0] if vector_fields else None

            metadata = {
                "distance_metric": "COSINE",
                "vector_field": vector_field,
                "schema_version": 0,
            }

            self._collection_metadata[collection_name] = metadata
            return metadata
        finally:
            cursor.close()

    async def list_collection_names(self) -> List[str]:
        """List all collection names."""
        cursor = self.connection.cursor()
        try:
            cursor.execute("""
                SELECT table_name 
                FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_type = 'BASE TABLE';
            """)
            return [row[0] for row in cursor.fetchall()]
        finally:
            cursor.close()

    async def update_schema(self, collection_name: str, operations: List[BaseOperation]):
        """Apply a list of schema migration operations to a collection."""
        if not operations:
            return

        old_schema = await self.get_schema(collection_name)
        new_schema = compute_new_schema(old_schema, operations)
        transform_func = build_transform_func_for_operations(operations)

        temp_collection_name = f"{collection_name}_migration_{int(asyncio.get_event_loop().time() * 1000)}"

        store_logger.info(
            f"Starting migration for '{collection_name}'. New collection: '{temp_collection_name}'.",
            event_type=LogEventType.STORE_UPDATE,
            table_name=collection_name
        )

        try:
            metadata = await self.get_collection_metadata(collection_name)
            await self.create_collection(
                temp_collection_name,
                new_schema,
                distance_metric=metadata.get("distance_metric", "COSINE")
            )

            store_logger.info(
                f"Starting data copy from '{collection_name}' to '{temp_collection_name}'.",
                event_type=LogEventType.STORE_UPDATE,
                table_name=collection_name
            )

            cursor = self.connection.cursor()
            cursor.execute(f"SELECT * FROM {collection_name};")
            rows = cursor.fetchall()
            columns = [desc[0] for desc in cursor.description]

            batch = []
            batch_size = 100
            total_docs = 0

            for row in rows:
                doc = dict(zip(columns, row))
                transformed_doc = transform_func(doc)
                batch.append(transformed_doc)

                if len(batch) >= batch_size:
                    await self.add_docs(temp_collection_name, batch)
                    total_docs += len(batch)
                    store_logger.debug(
                        f"Migrated {total_docs} documents to '{temp_collection_name}'.",
                        event_type=LogEventType.STORE_UPDATE,
                        table_name=collection_name
                    )
                    batch = []

            if batch:
                await self.add_docs(temp_collection_name, batch)
                total_docs += len(batch)

            store_logger.info(
                f"Finished copying {total_docs} documents to '{temp_collection_name}'.",
                event_type=LogEventType.STORE_UPDATE,
                table_name=collection_name
            )

            await self.delete_collection(collection_name)

            cursor.execute(f"ALTER TABLE {temp_collection_name} RENAME TO {collection_name};")
            cursor.close()

            if collection_name in self._collection_metadata:
                del self._collection_metadata[collection_name]

            store_logger.info(
                f"Migration for '{collection_name}' completed successfully.",
                event_type=LogEventType.STORE_UPDATE,
                table_name=collection_name
            )

        except Exception as e:
            if await self.collection_exists(temp_collection_name):
                await self.delete_collection(temp_collection_name)
            raise build_error(
                StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                error_msg=f"Migration for '{collection_name}' failed: {e}"
            ) from e

    async def update_collection_metadata(
            self,
            collection_name: str,
            metadata: Dict[str, Any],
    ) -> None:
        """Update collection metadata."""
        if not metadata:
            return

        if "schema_version" in metadata:
            version = metadata["schema_version"]
            if not isinstance(version, int) or version < 0:
                raise build_error(
                    StatusCode.STORE_VECTOR_SCHEMA_INVALID,
                    error_msg=f"schema_version must be a non-negative integer, got {version}",
                )

        cursor = self.connection.cursor()
        try:
            cursor.execute(
                "SELECT EXISTS (SELECT table_name FROM information_schema.tables WHERE table_name = %s);",
                (collection_name,)
            )
            if not cursor.fetchone()[0]:
                raise build_error(
                    StatusCode.STORE_VECTOR_COLLECTION_NOT_FOUND,
                    error_msg=f"'{collection_name}' does not exist.")

            if collection_name in self._collection_metadata:
                self._collection_metadata[collection_name].update(metadata)

            store_logger.info(
                f"Updated collection metadata for '{collection_name}': {metadata}",
                event_type=LogEventType.STORE_UPDATE,
                table_name=collection_name
            )

        finally:
            cursor.close()
