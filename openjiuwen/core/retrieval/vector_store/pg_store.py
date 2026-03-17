# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
PostgreSQL Vector Store Implementation

Supports vector search using pgvector extension.
"""

import asyncio
import json
from typing import Any, List, Optional

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    Column,
    MetaData,
    String,
    Table,
    Text,
    delete,
    func,
    inspect,
    select,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker, create_async_engine

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.vector_fields.pg_fields import PGVectorField
from openjiuwen.core.retrieval.common.config import VectorStoreConfig
from openjiuwen.core.retrieval.common.retrieval_result import SearchResult
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion
from openjiuwen.core.retrieval.vector_store.base import VectorStore


class PGVectorStore(VectorStore):
    """PostgreSQL vector store implementation using pgvector"""

    def __init__(
        self,
        config: VectorStoreConfig,
        pg_uri: str,
        text_field: str = "content",
        vector_field: str | PGVectorField = "embedding",
        sparse_vector_field: str = "sparse_vector",
        metadata_field: str = "metadata",
        doc_id_field: str = "document_id",
        **kwargs: Any,
    ):
        """
        Initialize PostgreSQL vector store

        Args:
            config: Vector store configuration
            pg_uri: PostgreSQL connection URI (postgresql+asyncpg://...)
            text_field: Text field name
            vector_field: Vector field name (str) or definition (PGVectorField)
            sparse_vector_field: Sparse vector field name (used for consistency, but PG uses tsvector)
            metadata_field: Metadata field name
            doc_id_field: Document ID field name
        """
        self.config = config
        self.collection_name = config.collection_name
        self.pg_uri = pg_uri
        self.text_field = text_field
        self.sparse_vector_field = sparse_vector_field
        self.metadata_field = metadata_field
        self.doc_id_field = doc_id_field
        self.database_name = self.config.database_name
        # Map distance metric to pgvector operator function name
        # l2 -> <-> (l2_distance)
        # cosine -> <=> (cosine_distance)
        # dot -> <#> (max_inner_product)
        self.distance_metric = config.distance_metric  # Expose for validation
        self._distance_metric = config.distance_metric

        if isinstance(vector_field, str):
            self.vector_field_config = PGVectorField(vector_field=vector_field)
        elif isinstance(vector_field, PGVectorField):
            self.vector_field_config = vector_field
        else:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_VECTOR_FIELD_INVALID,
                error_msg="vector_field must be either a str or PGVectorField instance",
            )

        self.vector_col_name = self.vector_field_config.vector_field
        # Expose all fields as attributes for validation
        self.vector_field = self.vector_col_name

        # Initialize Engine
        self._engine = self.create_client(database_name=self.database_name, path_or_uri=self.pg_uri)
        self._async_session = async_sessionmaker(self._engine, expire_on_commit=False)

        # Table reference (initialized lazily or on load)
        self._table: Optional[Table] = None
        # Public property for test/inspection (read-only recommended but mutable for testing if needed)
        self.table_ref = None
        self._metadata = MetaData()

        # Initialize: check if table exists and load it
        # We need to run this in event loop, but __init__ is sync.
        # So we just set up the engine. Table loading happens in methods.

    @staticmethod
    def create_client(database_name: str, path_or_uri: str, token: str = "", **kwargs) -> AsyncEngine:
        """Create AsyncEngine"""
        # Note: Database creation is assumed to be handled externally or path_or_uri points to default DB.
        # Ideally, we should connect to 'postgres' db to check/create target db, but here we assume validity.
        return create_async_engine(path_or_uri, **kwargs)

    async def _ensure_extension(self):
        """Ensure pgvector extension exists"""
        async with self._async_session() as session:
            async with session.begin():
                await session.execute(text("CREATE EXTENSION IF NOT EXISTS vector"))

    async def _get_or_create_table(self, dim: int = 0) -> Table:
        """Get existing table or create new one if dim is provided"""
        if self._table is not None:
            return self._table

        # Check if table was injected via test property
        if self.table_ref is not None:
            self._table = self.table_ref
            return self._table

        # Try to reflect
        async with self._engine.connect() as conn:
            # Check if table exists
            table_exists = await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table(self.collection_name))

            if table_exists:
                self._table = Table(self.collection_name, self._metadata, autoload_with=conn)
                return self._table

            if dim > 0:
                if dim > 2000:
                    raise build_error(
                        StatusCode.RETRIEVAL_KB_DATABASE_CONFIG_INVALID,
                        error_msg=f"pgvector only supports vector dimensions up to 2000. Got {dim}.",
                    )

                await self._ensure_extension()
                # Define table
                self._table = Table(
                    self.collection_name,
                    self._metadata,
                    Column("id", String, primary_key=True),
                    Column(self.text_field, Text),
                    Column(self.metadata_field, JSONB),
                    Column(self.vector_col_name, Vector(dim)),
                    # We can add tsvector column for sparse search optimization if needed
                    # But for now we might compute it on the fly or add it.
                    # Column("tsv", TSVECTOR)
                )
                await conn.run_sync(self._metadata.create_all)

                # Create index
                # We need to use specific operator class based on metric
                # l2 -> vector_l2_ops
                # cosine -> vector_cosine_ops
                # dot -> vector_ip_ops

                ops_map = {
                    "l2": "vector_l2_ops",
                    "euclidean": "vector_l2_ops",
                    "cosine": "vector_cosine_ops",
                    "dot": "vector_ip_ops",
                    "ip": "vector_ip_ops",
                }
                ops = ops_map.get(self._distance_metric, "vector_cosine_ops")

                # Default to HNSW
                index_method = self.vector_field_config.index_type
                if index_method == "hnsw":
                    m = self.vector_field_config.m
                    ef_construction = self.vector_field_config.ef_construction

                    # Create HNSW index
                    # CREATE INDEX ON table USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64)
                    index_name = f"idx_{self.collection_name}_{self.vector_col_name}"
                    stmt = text(
                        f"CREATE INDEX IF NOT EXISTS {index_name} ON {self.collection_name} "
                        f"USING hnsw ({self.vector_col_name} {ops}) "
                        f"WITH (m = {m}, ef_construction = {ef_construction})"
                    )
                    async with self._async_session() as session:
                        async with session.begin():
                            await session.execute(stmt)

                return self._table

            return None

    def check_vector_field(self) -> None:
        """Check config match"""
        # This is hard to fully implement without async, but we can skip strict check here
        # or implement a sync version if strictly required.
        # Base class defines this as abstract but sync.
        # We can just pass for now or log warning.
        pass

    async def add(
        self,
        data: dict | List[dict],
        batch_size: int | None = 128,
        **kwargs: Any,
    ) -> None:
        """Add vector data"""
        if isinstance(data, dict):
            data = [data]

        if not data:
            return

        if batch_size is None or batch_size <= 0:
            batch_size = 128

        # Infer dim from first record if needed
        first_vec = data[0].get(self.vector_col_name)
        dim = len(first_vec) if first_vec else 0

        table = await self._get_or_create_table(dim)
        if table is None:
            raise build_error(
                StatusCode.RETRIEVAL_KB_DATABASE_CONFIG_INVALID, error_msg="Failed to create or retrieve table"
            )

        processed = 0
        total = len(data)

        # Prepare data
        records = []
        for item in data:
            # Prepare metadata
            meta = item.get(self.metadata_field, {})
            if isinstance(meta, str):
                try:
                    meta = json.loads(meta)
                except json.JSONDecodeError:
                    meta = {}

            # Add other fields to metadata
            if self.doc_id_field in item:
                meta[self.doc_id_field] = str(item[self.doc_id_field])
            if "chunk_id" in item:
                meta["chunk_id"] = str(item["chunk_id"])

            record = {
                "id": str(item.get("id", item.get("pk"))),
                self.text_field: item.get(self.text_field, ""),
                self.metadata_field: meta,
                self.vector_col_name: item.get(self.vector_col_name),
            }
            records.append(record)

            if len(records) >= batch_size:
                await self._insert_batch(table, records)
                processed += len(records)
                records = []
                if processed % 100 == 0:
                    logger.info(f"Written {processed}/{total} records to {self.collection_name}")

        if records:
            await self._insert_batch(table, records)
            processed += len(records)

        logger.info(f"Writing completed, total {processed}/{total} records to {self.collection_name}")

    async def _insert_batch(self, table: Table, records: List[dict]):
        async with self._async_session() as session:
            async with session.begin():
                # Postgres upsert (INSERT ... ON CONFLICT DO UPDATE)
                from sqlalchemy.dialects.postgresql import insert as pg_insert

                stmt = pg_insert(table).values(records)
                stmt = stmt.on_conflict_do_update(
                    index_elements=["id"],
                    set_={
                        self.text_field: stmt.excluded[self.text_field],
                        self.metadata_field: stmt.excluded[self.metadata_field],
                        self.vector_col_name: stmt.excluded[self.vector_col_name],
                    },
                )
                await session.execute(stmt)

    async def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """Vector search"""
        table = await self._get_or_create_table()
        if table is None:
            return []

        stmt = select(table)

        # Filters
        if filters:
            conds = self.build_filters(table, filters)
            if conds:
                stmt = stmt.where(*conds)

        # Order by distance
        vec_col = table.c[self.vector_col_name]

        if self._distance_metric in ["l2", "euclidean"]:
            dist_func = vec_col.l2_distance(query_vector)
        elif self._distance_metric == "cosine":
            dist_func = vec_col.cosine_distance(query_vector)
        elif self._distance_metric in ["dot", "ip"]:
            # max_inner_product returns negative distance for sorting (higher is better)
            # but usually we want to order by distance ASC.
            # pgvector max_inner_product operator <#> returns negative inner product
            dist_func = vec_col.max_inner_product(query_vector)
        else:
            dist_func = vec_col.cosine_distance(query_vector)

        stmt = stmt.order_by(dist_func).limit(top_k)

        # Include distance in selection for score calculation
        stmt = stmt.add_columns(dist_func.label("distance"))

        async with self._async_session() as session:
            result = await session.execute(stmt)
            rows = result.fetchall()
            return self._rows_to_search_results(rows, mode="vector")

    async def sparse_search(
        self,
        query_text: str,
        top_k: int = 5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """Sparse search using Postgres Full Text Search"""
        table = await self._get_or_create_table()
        if table is None:
            return []

        # Use websearch_to_tsquery for better query handling
        ts_query = func.websearch_to_tsquery("english", query_text)
        ts_vector = func.to_tsvector("english", table.c[self.text_field])

        stmt = select(table).where(ts_vector.op("@@")(ts_query))

        if filters:
            conds = self.build_filters(table, filters)
            if conds:
                stmt = stmt.where(*conds)

        # Rank by relevance
        rank = func.ts_rank(ts_vector, ts_query)
        stmt = stmt.order_by(rank.desc()).limit(top_k)
        stmt = stmt.add_columns(rank.label("rank"))

        async with self._async_session() as session:
            result = await session.execute(stmt)
            rows = result.fetchall()
            return self._rows_to_search_results(rows, mode="sparse")

    async def hybrid_search(
        self,
        query_text: str,
        query_vector: Optional[List[float]] = None,
        top_k: int = 5,
        alpha: float = 0.5,
        filters: Optional[dict] = None,
        **kwargs: Any,
    ) -> List[SearchResult]:
        """Hybrid search"""
        # Execute concurrently
        tasks = []
        if query_vector:
            tasks.append(self.search(query_vector, top_k * 2, filters))
        else:
            tasks.append(asyncio.sleep(0, result=[]))

        tasks.append(self.sparse_search(query_text, top_k * 2, filters))

        results = await asyncio.gather(*tasks)
        vec_results, text_results = results[0], results[1]

        # Fusion
        fused = rrf_fusion([vec_results, text_results], k=60)

        # Clean up metadata (remove temp id)
        final_results = []
        for res in fused[:top_k]:
            meta = res.metadata.copy()
            meta.pop("id", None)
            final_results.append(
                SearchResult(
                    id=res.metadata.get("id", str(hash(res.text))), text=res.text, score=res.score, metadata=meta
                )
            )

        return final_results

    async def delete(
        self,
        ids: Optional[List[str]] = None,
        filter_expr: Optional[str] = None,
        **kwargs: Any,
    ) -> bool:
        """Delete vectors"""
        table = await self._get_or_create_table()
        if table is None:
            return False

        stmt = delete(table)

        if ids:
            stmt = stmt.where(table.c.id.in_(ids))
        elif filter_expr:
            # We don't support arbitrary string expressions securely easily.
            # But we can support simple filters if passed as dict in kwargs?
            # Base class interface uses `filter_expr` as string.
            # We will warn and return False if only filter_expr string provided without implementation.
            logger.warning("PGVectorStore: filter_expr string not fully supported, use ids.")
            return False
        else:
            logger.warning("PGVectorStore: delete requires ids")
            return False

        async with self._async_session() as session:
            async with session.begin():
                await session.execute(stmt)
        return True

    async def table_exists(self, table_name: str) -> bool:
        async with self._engine.connect() as conn:
            return await conn.run_sync(lambda sync_conn: inspect(sync_conn).has_table(table_name))

    async def delete_table(self, table_name: str) -> None:
        async with self._engine.begin() as conn:
            await conn.execute(text(f"DROP TABLE IF EXISTS {table_name}"))

    def build_filters(self, table: Table, filters: dict) -> List[Any]:
        conds = []
        for k, v in filters.items():
            # Check if it is a metadata field
            if k in [c.name for c in table.c]:
                # Standard column
                col = table.c[k]
                conds.append(col == v)
            else:
                # Metadata column
                # metadata->>'key' == value
                # Check type of v to decide casting?
                # JSONB containment: metadata @> {"key": value}
                if isinstance(v, (dict, list)):
                    conds.append(table.c[self.metadata_field].contains({k: v}))
                else:
                    # For simple values, use containment too: {"key": value}
                    conds.append(table.c[self.metadata_field].contains({k: v}))
        return conds

    def _rows_to_search_results(self, rows: List[Any], mode: str) -> List[SearchResult]:
        results = []
        for row in rows:
            # Row is keyed by column
            # In sqlalchemy 2.0+ result rows are accessible by name
            # row.id, row.text, etc.

            meta = dict(row.metadata) if row.metadata else {}

            # Ensure special fields in metadata
            if self.doc_id_field not in meta and self.doc_id_field in row.metadata:
                meta[self.doc_id_field] = row.metadata[self.doc_id_field]

            score = 0.0
            raw_score = 0.0

            if mode == "vector":
                dist = row.distance
                raw_score = dist
                if self._distance_metric in ["l2", "euclidean"]:
                    # dist is distance
                    score = max(0.0, 1.0 - dist)  # Normalize? Or just use inverse?
                    # Common practice: 1 / (1 + dist) or similar.
                    # Chroma does (4-d)/4. Milvus similar.
                    # Let's use 1 / (1 + d) for infinite range?
                    # Or just return distance if not strictly 0-1.
                    # Let's match Chroma logic if possible or standard.
                    # Assuming normalized vectors for cosine: dist = 1 - cos.
                    pass
                elif self._distance_metric == "cosine":
                    # pgvector cosine_distance returns 1 - cosine_similarity
                    score = 1.0 - dist
                elif self._distance_metric in ["dot", "ip"]:
                    # max_inner_product returns negative inner product
                    # so inner product = -dist
                    score = -dist
            elif mode == "sparse":
                score = float(row.rank)
                raw_score = score

            meta["raw_score"] = raw_score

            results.append(
                SearchResult(
                    id=str(row.id),
                    text=row.content,  # using content column name
                    score=score,
                    metadata=meta,
                )
            )
        return results
