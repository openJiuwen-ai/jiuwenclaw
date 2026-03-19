# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Milvus Vector Store Implementation

Supports vector search, sparse search (BM25), and hybrid search.
"""

import asyncio
from typing import Any, Dict, List, Optional

from pymilvus import AnnSearchRequest, DataType, MilvusClient, RRFRanker
from pymilvus.client.types import LoadState

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.query import QueryExpr
from openjiuwen.core.foundation.store.vector.utils import (
    convert_cosine_similarity,
    convert_ip_similarity,
    convert_l2_squared,
)
from openjiuwen.core.foundation.store.vector_fields.milvus_fields import MilvusAUTO, MilvusVectorField
from openjiuwen.core.retrieval.common.config import VectorStoreConfig
from openjiuwen.core.retrieval.common.retrieval_result import SearchResult
from openjiuwen.core.retrieval.utils.common import create_milvus_alias
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion
from openjiuwen.core.retrieval.vector_store.base import VectorStore


class MilvusVectorStore(VectorStore):
    """Milvus vector store implementation"""

    def __init__(
        self,
        config: VectorStoreConfig,
        milvus_uri: str,
        milvus_token: Optional[str] = None,
        text_field: str = "content",
        vector_field: str | MilvusVectorField = "embedding",
        sparse_vector_field: str = "sparse_vector",
        metadata_field: str = "metadata",
        doc_id_field: str = "document_id",
        milvus_alias: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize Milvus vector store

        Args:
            config: Vector store configuration
            milvus_uri: Milvus URI
            milvus_token: Milvus Token (optional)
            text_field: Text field name
            vector_field: Vector field name (str) or definition (MilvusVectorField)
            sparse_vector_field: Sparse vector field name
            metadata_field: Metadata field name
            milvus_alias: Optional connection alias
        """
        self.config = config
        self.collection_name = config.collection_name
        self.milvus_uri = milvus_uri
        self.milvus_token = milvus_token
        self.text_field = text_field
        self.sparse_vector_field = sparse_vector_field
        self.metadata_field = metadata_field
        self.doc_id_field = doc_id_field
        self.database_name = self.config.database_name
        self._distance_metric = config.distance_metric.replace("dot", "ip").replace("euclidean", "l2").upper()

        if isinstance(vector_field, str):
            self.vector_field = MilvusAUTO(vector_field=vector_field)
        elif isinstance(vector_field, MilvusVectorField):
            self.vector_field = vector_field
        else:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_VECTOR_FIELD_INVALID,
                error_msg="vector_field must be either a str or MilvusVectorField instance",
            )
        if self.vector_field.index_type == "auto":
            self._construct_config = {}
        else:
            self._construct_config = self.vector_field.to_dict(stage="construct")
        self._construct_config["metric_type"] = self._distance_metric
        self._search_config = self.vector_field.to_dict(stage="search")

        # Initialize Milvus client & database
        self._client = self.create_client(
            database_name=self.config.database_name,
            path_or_uri=self.milvus_uri,
            token=self.milvus_token,
            alias=create_milvus_alias(milvus_alias, uri=milvus_uri, token=milvus_token),
        )
        self._collection_loaded = False

    @property
    def client(self) -> MilvusClient:
        """Get Milvus client"""
        return self._client

    @property
    def distance_metric(self) -> str:
        """Get raw distance metric string"""
        return self._distance_metric

    @staticmethod
    def create_client(database_name: str, path_or_uri: str, token: str = "", **kwargs) -> MilvusClient:
        """Create Milvus client and ensure database exists"""
        client = MilvusClient(uri=path_or_uri, token=token, **kwargs)
        if database_name and database_name != "default":
            if database_name not in client.list_databases():
                client.create_database(database_name)
            client.use_database(database_name)
        return client

    def get_search_params(self, top_k: int) -> dict[str, Any]:
        """Get correct parameters for searches"""
        search_params = self._search_config
        if "efSearchFactor" in self._search_config:
            search_params = search_params.copy()
            search_params["ef"] = round(top_k * search_params.pop("efSearchFactor"))
        return search_params

    def check_vector_field(self) -> None:
        """Check if vector field configuration is consistent with actual database"""
        if not self._client.has_collection(self.collection_name):
            return
        index_type = self.vector_field.index_type
        variant = str(getattr(self.vector_field, "variant", "") or "")
        field_name = self.vector_field.vector_field
        actual = self._client.describe_index(self.collection_name, field_name) or {}
        if not actual:
            collection_fields = self._client.describe_collection(self.collection_name).get("fields", [])
            v_fields = [v_field for v_field in collection_fields if v_field["type"] == DataType.FLOAT_VECTOR]
            v_fields_list = "\n".join("- [{field_id}] {name}: {params}".format(**v_field) for v_field in v_fields)
            raise build_error(
                StatusCode.RETRIEVAL_KB_DATABASE_CONFIG_INVALID,
                error_msg=f"MilvusVectorStore has vector_field at {field_name} while actual database has "
                f"vector field(s) at:\n{v_fields_list}\nYou may want to call delete_collection method on "
                f'collection "{self.collection_name}"',
            )

        if index_type != "auto":
            returned_type: str = actual.get("index_type", "unknown")
            if not (returned_type.startswith(index_type.upper()) and returned_type.endswith(variant)):
                raise build_error(
                    StatusCode.RETRIEVAL_KB_DATABASE_CONFIG_INVALID,
                    error_msg=f"MilvusVectorStore has index_type of {index_type} while actual database has "
                    f"index_type of {returned_type}, do not change index_type after Knowledge Base is constructed.",
                )
        self._check_configs_matching(self._construct_config, actual)

    async def add(
        self,
        data: dict | List[dict],
        batch_size: int | None = 128,
        **kwargs,
    ) -> None:
        self._ensure_loaded()
        if batch_size is None or batch_size <= 0:
            batch_size = 128

        processed = 0
        total = len(data)
        cache: list[dict] = []
        for doc in data:
            cache.append(doc)
            if len(cache) >= batch_size:
                nodes = cache[:batch_size]
                cache = []
                await asyncio.to_thread(
                    self._client.insert,
                    collection_name=self.collection_name,
                    data=nodes,
                )
                processed += len(nodes)
                if processed % 100 == 0:
                    logger.info(
                        "Written %d/%d records to %s",
                        processed,
                        total,
                        self.collection_name,
                    )
        if cache:
            await asyncio.to_thread(
                self._client.insert,
                collection_name=self.collection_name,
                data=cache,
            )
            processed += len(cache)
        logger.info(
            "Writing completed, total %d/%d records to %s",
            processed,
            total,
            self.collection_name,
        )

        # Flush using client API
        self._client.flush(self.collection_name)

    async def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[dict | QueryExpr] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Vector search"""
        self._ensure_loaded()
        output_fields = [self.text_field, self.metadata_field, self.doc_id_field, "chunk_id"]

        # Build filter expression
        filter_expr = None
        if isinstance(filters, dict):
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, str):
                    filter_parts.append(f"{key} == {QueryExpr.sanitize_str(value)}")
                else:
                    filter_parts.append(f"{key} == {value}")
            if filter_parts:
                filter_expr = " && ".join(filter_parts)
        elif isinstance(filters, QueryExpr):
            filter_expr = filters.to_expr("milvus")

        # Execute search
        results = await asyncio.to_thread(
            self._client.search,
            collection_name=self.collection_name,
            data=[query_vector],
            anns_field=self.vector_field.vector_field,
            limit=top_k,
            output_fields=output_fields,
            search_params={"metric_type": self._distance_metric, "params": self.get_search_params(top_k)},
            filter=filter_expr,
        )

        if results and len(results) > 0:
            return self._milvus_result_to_search_results(results[0], mode="vector")
        return []

    async def sparse_search(
        self,
        query_text: str,
        top_k: int = 5,
        filters: Optional[dict | QueryExpr] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Sparse search (BM25)"""
        self._ensure_loaded()
        output_fields = [self.text_field, self.metadata_field, self.doc_id_field]

        # Build filter expression
        filter_expr = None
        if isinstance(filters, dict):
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, str):
                    filter_parts.append(f"{key} == {QueryExpr.sanitize_str(value)}")
                else:
                    filter_parts.append(f"{key} == {value}")
            if filter_parts:
                filter_expr = " && ".join(filter_parts)
        elif isinstance(filters, QueryExpr):
            filter_expr = filters.to_expr("milvus")

        try:
            # Use native BM25 full-text search
            results = await asyncio.to_thread(
                self._client.search,
                collection_name=self.collection_name,
                data=[query_text],  # Pass text directly, BM25 function handles tokenization
                anns_field=self.sparse_vector_field,
                limit=top_k,
                output_fields=output_fields,
                search_params={"metric_type": "BM25"},
                filter=filter_expr,
            )

            if results and len(results) > 0:
                return self._milvus_result_to_search_results(results[0], mode="sparse")
            return []
        except Exception as e:
            logger.warning(f"BM25 text search failed: {e}")
            return []

    async def hybrid_search(
        self,
        query_text: str,
        query_vector: Optional[List[float]] = None,
        top_k: int = 5,
        alpha: float = 0.5,
        filters: Optional[dict | QueryExpr] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Hybrid search (sparse retrieval + vector retrieval)"""
        self._ensure_loaded()
        output_fields = [self.text_field, self.metadata_field, self.doc_id_field]

        # Build filter expression
        filter_expr = None
        if isinstance(filters, dict):
            filter_parts = []
            for key, value in filters.items():
                if isinstance(value, str):
                    filter_parts.append(f"{key} == {QueryExpr.sanitize_str(value)}")
                else:
                    filter_parts.append(f"{key} == {value}")
            if filter_parts:
                filter_expr = " && ".join(filter_parts)
        elif isinstance(filters, QueryExpr):
            filter_expr = filters.to_expr("milvus")

        try:
            # Build search requests
            search_requests = []

            # Dense vector search request
            if query_vector is not None:
                dense_req = AnnSearchRequest(
                    data=[query_vector],
                    anns_field=self.vector_field.vector_field,
                    param={"metric_type": self._distance_metric, "params": self.get_search_params(top_k)},
                    limit=top_k,
                    expr=filter_expr,
                )
                search_requests.append(dense_req)

            # Sparse BM25 search request
            sparse_req = AnnSearchRequest(
                data=[query_text],  # Pass text directly
                anns_field=self.sparse_vector_field,
                param={"metric_type": "BM25"},
                limit=top_k,
                expr=filter_expr,
            )
            search_requests.append(sparse_req)

            if not search_requests:
                return []

            # Use native hybrid search with RRF ranking
            results = await asyncio.to_thread(
                self._client.hybrid_search,
                collection_name=self.collection_name,
                reqs=search_requests,
                ranker=RRFRanker(k=60),  # RRF with k=60
                limit=top_k,
                output_fields=output_fields,
            )

            if results and len(results) > 0:
                result_list = results[0] if isinstance(results[0], list) else results
                return self._milvus_result_to_search_results(result_list, mode="hybrid")
            return []
        except Exception as e:
            logger.warning(f"Hybrid search failed, falling back to separate searches: {e}")
            # Fall back to separate searches then fusion
            return await self._hybrid_search_fallback(query_text, query_vector, top_k, filters)

    async def _hybrid_search_fallback(
        self,
        query_text: str,
        query_vector: Optional[List[float]],
        top_k: int,
        filters: Optional[dict | QueryExpr],
    ) -> List[SearchResult]:
        """Fallback hybrid search: execute searches separately then fuse"""
        # Execute two searches concurrently
        task_vector = (
            asyncio.create_task(self.search(query_vector, top_k, None))
            if query_vector
            else asyncio.create_task(asyncio.sleep(0, result=[]))
        )
        task_text = asyncio.create_task(self.sparse_search(query_text, top_k, None))

        results_vector = await task_vector
        results_text = await task_text

        fused_results = rrf_fusion([results_vector, results_text], k=60)
        return fused_results[:top_k]

    async def delete(
        self,
        ids: Optional[List[str]] = None,
        filter_expr: Optional[str | QueryExpr] = None,
        **kwargs,
    ) -> bool:
        """Delete vectors"""
        self._ensure_loaded()
        try:
            if isinstance(filter_expr, QueryExpr):
                filter_expr = filter_expr.to_expr("milvus")
            result = await asyncio.to_thread(
                self._client.delete,
                collection_name=self.collection_name,
                ids=ids,
                filter=filter_expr,
            )
            self._client.flush(self.collection_name)

            # Check delete result
            if isinstance(result, dict):
                return result.get("delete_count", 0) > 0
            return bool(result)
        except Exception as e:
            logger.error(f"Failed to delete vectors: {e}")
            return False

    def _normalize_milvus_hit(self, item: Any) -> Dict[str, Any]:
        """Flatten hit to one dict (SDK may put output_fields under 'entity')."""
        if not isinstance(item, dict):
            item = dict(item) if hasattr(item, "keys") else {}
        out = dict(item)
        entity = out.pop("entity", None)
        if isinstance(entity, dict):
            out |= entity
        return out

    def _milvus_result_to_search_results(
        self,
        results: List[dict],
        mode: str,
    ) -> List[SearchResult]:
        """Convert Milvus search results to SearchResult list"""
        search_results = []
        for raw_item in results:
            item = self._normalize_milvus_hit(raw_item)
            # Extract fields
            result_id = str(item.get("id", item.get("pk", "")))
            text = item.get(self.text_field, "")
            metadata = item.get(self.metadata_field, {})
            if isinstance(metadata, str):
                import json

                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}

            # Ensure doc_id is in metadata for retriever (pop from item then metadata to avoid duplication)
            if "doc_id" not in metadata:
                metadata["doc_id"] = item.pop(self.doc_id_field, None) or metadata.pop(self.doc_id_field, None)

            # Include chunk_id for upper layer association
            if item.get("chunk_id") is not None:
                metadata.setdefault("chunk_id", item.get("chunk_id"))

            raw_score = item.get("score", item.get("distance", None))
            raw_score_val = float(raw_score) if raw_score is not None else None
            raw_score_scaled: Optional[float] = None
            final_score: float = 0.0

            if mode == "vector":
                if raw_score_val is not None:
                    if self._distance_metric == "L2":
                        # Milvus L2 returns squared L2 distance
                        # Convert to [0, 1]: (max_dist - distance) / max_dist
                        raw_score_scaled = convert_l2_squared(raw_score_val)
                    elif self._distance_metric == "COSINE":
                        # Milvus COSINE returns similarity in [-1, 1]
                        # Convert to [0, 1]: (distance + 1) / 2
                        raw_score_scaled = convert_cosine_similarity(raw_score_val)
                    else:
                        # Milvus IP returns raw inner product (unbounded)
                        # Convert to [0, 1]: max(0, min(1, (distance + 1) / 2))
                        raw_score_scaled = convert_ip_similarity(raw_score_val)
                    final_score = raw_score_scaled
            elif mode == "sparse":
                if raw_score_val is not None:
                    final_score = raw_score_val
            else:  # hybrid or other
                if raw_score_val is not None:
                    final_score = raw_score_val

            metadata.setdefault("raw_score", raw_score_val)
            if raw_score_scaled is not None:
                metadata.setdefault("raw_score_scaled", raw_score_scaled)

            search_result = SearchResult(
                id=result_id,
                text=text,
                score=final_score,
                metadata=metadata,
            )
            search_results.append(search_result)

        return search_results

    def close(self, release_collection: bool = False) -> None:
        """Close vector store"""
        if self._client is None:
            return
        try:
            if release_collection and self._client.has_collection(self.collection_name, timeout=15.0):
                col_state = self._client.get_load_state(self.collection_name, timeout=15.0).get("state")
                if col_state == LoadState.Loaded:
                    self._client.release_collection(self.collection_name)
                self._collection_loaded = False
        except Exception as e:
            logger.warning(f"Failed to release Milvus collection: {e}")
        try:
            self._client.close()
        except Exception as e:
            logger.warning(f"Failed to close Milvus client: {e}")

    async def table_exists(self, table_name: str) -> bool:
        """Check if a collection exists in current database"""
        return self._client.has_collection(table_name)

    async def delete_table(self, table_name: str) -> None:
        """Delete a collection from current database"""
        await asyncio.to_thread(
            self._client.drop_collection,
            collection_name=table_name,
        )

    def _ensure_loaded(self) -> None:
        """Ensure collection is loaded"""
        if self._collection_loaded:
            return
        if self.client.has_collection(self.collection_name, timeout=15.0):
            logger.info("Retrieval Milvus Store: loading collection %s", self.collection_name)
            self.client.load_collection(self.collection_name, timeout=180.0)
            logger.info("Retrieval Milvus Store: %s collection loaded", self.collection_name)
            self._collection_loaded = True
