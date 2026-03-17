# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
ChromaDB Vector Store Implementation

Supports vector search, sparse search (text matching), and hybrid search.
"""

import asyncio
import json
import uuid
from typing import List, Optional

import chromadb
from chromadb.config import DEFAULT_DATABASE, Settings

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.store.query import QueryExpr
from openjiuwen.core.foundation.store.vector.utils import (
    convert_cosine_distance,
    convert_ip_distance,
    convert_l2_squared,
)
from openjiuwen.core.foundation.store.vector_fields.chroma_fields import ChromaVectorField
from openjiuwen.core.retrieval.common.config import VectorStoreConfig
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult, SearchResult
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion
from openjiuwen.core.retrieval.vector_store.base import VectorStore


class ChromaVectorStore(VectorStore):
    """ChromaDB vector store implementation"""

    def __init__(
        self,
        config: VectorStoreConfig,
        chroma_path: str,
        text_field: str = "content",
        vector_field: str | ChromaVectorField = "embedding",
        sparse_vector_field: str = "sparse_vector",
        metadata_field: str = "metadata",
        doc_id_field: str = "document_id",
        **kwargs,
    ):
        """
        Initialize ChromaDB vector store (persistent mode)

        Args:
            config: Vector store configuration
            chroma_path: ChromaDB persistent path (required)
            text_field: Text field name
            vector_field: Vector field name (str) or definition (ChromaVectorField)
            sparse_vector_field: Sparse vector field name (stored as metadata in ChromaDB)
            metadata_field: Metadata field name
            doc_id_field: Document ID field name

        Raises:
            ValueError: If chroma_path is not provided or empty
        """
        # Validate chroma_path
        if not chroma_path or not chroma_path.strip():
            raise build_error(
                StatusCode.RETRIEVAL_VECTOR_STORE_PATH_NOT_FOUND,
                error_msg="chroma_path is required and cannot be empty",
            )

        self.config = config
        self.collection_name = config.collection_name
        self.chroma_path = chroma_path
        self.text_field = text_field
        self.sparse_vector_field = sparse_vector_field
        self.metadata_field = metadata_field
        self.doc_id_field = doc_id_field
        self.database_name = self.config.database_name
        self._distance_metric = config.distance_metric.replace("dot", "ip").replace("euclidean", "l2")

        if isinstance(vector_field, str):
            self.vector_field = ChromaVectorField(vector_field=vector_field)
        elif isinstance(vector_field, ChromaVectorField):
            self.vector_field = vector_field
        else:
            raise build_error(
                StatusCode.RETRIEVAL_INDEXING_VECTOR_FIELD_INVALID,
                error_msg="vector_field must be either a str or ChromaVectorField instance",
            )
        self._construct_config = self.vector_field.to_dict(stage="construct")
        self._construct_config["space"] = self._distance_metric
        self._search_config = self.vector_field.to_dict(stage="search")

        # Initialize ChromaDB persistent client
        self._client: chromadb.PersistentClient = self.create_client(
            database_name=self.config.database_name,
            path_or_uri=self.chroma_path,
        )

        # Get or create collection
        self._collection = self._client.get_or_create_collection(
            name=self.collection_name,
            configuration={"hnsw": self._construct_config | self._search_config},
        )

    @property
    def client(self) -> chromadb.PersistentClient:
        """Get ChromaDB client"""
        return self._client

    @property
    def collection(self) -> chromadb.Collection:
        """Get ChromaDB collection"""
        return self._collection

    @property
    def distance_metric(self) -> str:
        """Get raw distance metric string"""
        return self._distance_metric

    @staticmethod
    def create_client(database_name: str, path_or_uri: str, token: str = "", **kwargs) -> chromadb.PersistentClient:
        """Create Chroma client and ensure database exists"""
        if database_name and database_name != DEFAULT_DATABASE:
            admin_client = chromadb.AdminClient(Settings(is_persistent=True, persist_directory=path_or_uri))
            if database_name not in {db.get("name") for db in admin_client.list_databases()}:
                admin_client.create_database(database_name)
            del admin_client
        else:
            database_name = DEFAULT_DATABASE
        return chromadb.PersistentClient(path=path_or_uri, database=database_name)

    def check_vector_field(self) -> None:
        """Check if vector field configuration is consistent with actual database"""
        self._check_configs_matching(self._construct_config, self.collection.configuration.get("hnsw", {}))

    async def add(
        self,
        data: dict | List[dict],
        batch_size: int | None = 128,
        **kwargs,
    ) -> None:
        """Add vector data"""
        if batch_size is None or batch_size <= 0:
            batch_size = 128

        if isinstance(data, dict):
            data = [data]

        processed = 0
        total = len(data)
        cache: list[dict] = []

        for doc in data:
            cache.append(doc)
            if len(cache) >= batch_size:
                nodes = cache[:batch_size]
                cache = []
                await self._add_batch(nodes)
                processed += len(nodes)
                if processed % 100 == 0:
                    logger.info(
                        "Written %d/%d records to %s",
                        processed,
                        total,
                        self.collection_name,
                    )

        if cache:
            await self._add_batch(cache)
            processed += len(cache)

        logger.info(
            "Writing completed, total %d/%d records to %s",
            processed,
            total,
            self.collection_name,
        )

    async def _add_batch(self, nodes: List[dict]) -> None:
        """Batch add data to ChromaDB"""
        ids = []
        embeddings = []
        documents = []
        metadatas = []

        for node in nodes:
            # Extract vector
            embedding = node.get(self.vector_field.vector_field, [])
            if not embedding:
                # If no vector, generate a warning but continue processing (may be allowed in some cases)
                logger.warning(f"Node has no embedding, skipping: {node.get('id', 'unknown')}")
                continue

            # Extract ID
            node_id = str(node.get("id", node.get("pk", "")))
            if not node_id:
                node_id = str(uuid.uuid4())
            ids.append(node_id)
            embeddings.append(embedding)

            # Extract text
            text = node.get(self.text_field, "")
            documents.append(text)

            # Build metadata
            metadata = {}
            # Copy original metadata
            if self.metadata_field in node:
                raw_metadata = node[self.metadata_field]
                if isinstance(raw_metadata, dict):
                    metadata.update(raw_metadata)
                elif isinstance(raw_metadata, str):
                    try:
                        metadata.update(json.loads(raw_metadata))
                    except Exception:
                        logger.warning(f"Failed to load metadata: {raw_metadata}")

            # Add other fields to metadata
            if self.doc_id_field in node:
                metadata[self.doc_id_field] = str(node[self.doc_id_field])
            if "chunk_id" in node:
                metadata["chunk_id"] = str(node["chunk_id"])
            if self.sparse_vector_field in node:
                # Store sparse vector as metadata (ChromaDB doesn't directly support sparse vectors)
                sparse_vec = node[self.sparse_vector_field]
                if isinstance(sparse_vec, (list, dict)):
                    metadata[self.sparse_vector_field] = json.dumps(sparse_vec)

            metadatas.append(metadata)

        # If no valid data, return directly
        if not ids:
            return

        # Re-fetch collection to ensure using the latest collection reference
        collection: chromadb.Collection = await asyncio.to_thread(
            self._client.get_collection,
            name=self.collection_name,
        )

        # Add to ChromaDB
        await asyncio.to_thread(
            collection.add,
            ids=ids,
            embeddings=embeddings,
            documents=documents,
            metadatas=metadatas,
        )

    async def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[dict | QueryExpr] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Vector search"""
        # Re-fetch collection to ensure using the latest collection reference
        collection: chromadb.Collection = await asyncio.to_thread(
            self._client.get_collection,
            name=self.collection_name,
        )

        # Build where filter conditions
        query_args = {}
        if isinstance(filters, dict):
            where = {}
            for key, value in filters.items():
                if isinstance(value, str):
                    where[key] = value
                else:
                    where[key] = value
            query_args["where"] = where
        elif isinstance(filters, QueryExpr):
            query_args.update({k: v for k, v in filters.to_expr("chroma").items() if v})

        # Execute search
        results = await asyncio.to_thread(
            collection.query,
            query_embeddings=[query_vector],
            n_results=top_k,
            **query_args,
        )

        return self._chroma_result_to_search_results(results, mode="vector")

    async def sparse_search(
        self,
        query_text: str,
        top_k: int = 5,
        filters: Optional[dict | QueryExpr] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """Sparse search (text matching)"""
        # Re-fetch collection to ensure using the latest collection reference
        collection: chromadb.Collection = await asyncio.to_thread(
            self._client.get_collection,
            name=self.collection_name,
        )

        # ChromaDB doesn't directly support BM25, use text query as alternative
        # Build where filter conditions
        query_args = {}
        if isinstance(filters, dict):
            where = {}
            for key, value in filters.items():
                if isinstance(value, str):
                    where[key] = value
                else:
                    where[key] = value
            query_args["where"] = where
        elif isinstance(filters, QueryExpr):
            query_args.update({k: v for k, v in filters.to_expr("chroma").items() if v})

        try:
            # Use text query (ChromaDB's text search is based on TF-IDF)
            results = await asyncio.to_thread(
                collection.query,
                query_texts=[query_text],
                n_results=top_k,
                **query_args,
            )

            if results and results.get("ids") and len(results["ids"][0]) > 0:
                return self._chroma_result_to_search_results(results, mode="sparse")
            return []
        except Exception as e:
            logger.warning(f"Text search failed: {e}")
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
        """Hybrid search (text retrieval + vector retrieval)"""
        try:
            # Execute vector search and text search separately
            tasks = []

            if query_vector is not None:
                task_vector = asyncio.create_task(self.search(query_vector, top_k * 2, filters))
                tasks.append(("vector", task_vector))

            task_text = asyncio.create_task(self.sparse_search(query_text, top_k * 2, filters))
            tasks.append(("text", task_text))

            # Wait for all tasks to complete
            results_dict = {}
            for mode, task in tasks:
                try:
                    results_dict[mode] = await task
                except Exception as e:
                    logger.warning(f"{mode} search failed in hybrid search: {e}")
                    results_dict[mode] = []

            # Fuse results
            results_list = [r for r in results_dict.values() if r]
            if not results_list:
                return []

            # Convert SearchResult to RetrievalResult for rrf_fusion
            # Save ID mapping for later recovery
            retrieval_results_list = []
            id_mapping = {}  # text -> id mapping

            for search_results in results_list:
                retrieval_results = []
                for sr in search_results:
                    # Save ID mapping
                    id_mapping[sr.text] = sr.id
                    # Ensure ID is in metadata
                    metadata = sr.metadata.copy()
                    metadata["id"] = sr.id
                    retrieval_results.append(
                        RetrievalResult(
                            text=sr.text,
                            score=sr.score,
                            metadata=metadata,
                            doc_id=sr.metadata.get(self.doc_id_field),
                            chunk_id=sr.metadata.get("chunk_id"),
                        )
                    )
                retrieval_results_list.append(retrieval_results)

            # Use RRF fusion
            fused_retrieval_results = rrf_fusion(retrieval_results_list, k=60)

            # Convert RetrievalResult back to SearchResult
            fused_results = []
            for rr in fused_retrieval_results[:top_k]:
                # Recover ID from metadata or mapping
                result_id = rr.metadata.get("id") or id_mapping.get(rr.text, str(hash(rr.text)))
                # Remove temporarily added id field from metadata
                metadata = rr.metadata.copy()
                metadata.pop("id", None)
                search_result = SearchResult(
                    id=result_id,
                    text=rr.text,
                    score=rr.score,
                    metadata=metadata,
                )
                fused_results.append(search_result)

            return fused_results
        except Exception as e:
            logger.warning(f"Hybrid search failed: {e}")
            return []

    async def delete(
        self,
        ids: Optional[List[str]] = None,
        filter_expr: Optional[str | QueryExpr] = None,
        **kwargs,
    ) -> bool:
        """Delete vectors"""
        try:
            # Re-fetch collection to ensure using the latest collection reference
            collection: chromadb.Collection = await asyncio.to_thread(
                self._client.get_collection,
                name=self.collection_name,
            )

            query_args = dict(ids=ids)
            if filter_expr is not None:
                if isinstance(filter_expr, QueryExpr):
                    query_args.update({k: v for k, v in filter_expr.to_expr("chroma").items() if v})
                else:
                    logger.warning("ChromaDB does not support string filter expressions.")
                    return False
            await asyncio.to_thread(collection.delete, **query_args)
            return True
        except Exception as e:
            logger.error(f"Failed to delete vectors: {e}")
            return False

    def _chroma_result_to_search_results(
        self,
        results: dict,
        mode: str,
    ) -> List[SearchResult]:
        """Convert ChromaDB search results to SearchResult list"""
        search_results = []

        if not results or "ids" not in results or not results["ids"]:
            return search_results

        ids_list = results["ids"][0] if results["ids"] else []
        documents_list = results.get("documents", [[]])[0] if results.get("documents") else []
        metadatas_list = results.get("metadatas", [[]])[0] if results.get("metadatas") else []
        distances_list = results.get("distances", [[]])[0] if results.get("distances") else []

        for idx, result_id in enumerate(ids_list):
            # Extract fields
            text = documents_list[idx] if idx < len(documents_list) else ""
            metadata = metadatas_list[idx] if idx < len(metadatas_list) else {}
            if not isinstance(metadata, dict):
                metadata = {}

            # Ensure doc_id is returned in metadata dict
            if "doc_id" not in metadata:
                metadata["doc_id"] = metadata.pop(self.doc_id_field, None)

            # Process sparse vector metadata
            if self.sparse_vector_field in metadata:
                try:
                    sparse_vec = json.loads(metadata[self.sparse_vector_field])
                    metadata[self.sparse_vector_field] = sparse_vec
                except Exception:
                    logger.warning(f"Failed to load sparse vector: {metadata[self.sparse_vector_field]}")

            # Calculate score
            raw_score = distances_list[idx] if idx < len(distances_list) else None
            raw_score_val = float(raw_score) if raw_score is not None else None
            raw_score_scaled: Optional[float] = None
            final_score: float = 0.0

            if mode == "vector":
                # ChromaDB returns distance, need to convert to similarity score
                if raw_score_val is not None:
                    if self._distance_metric == "l2":
                        # L2 distance, convert to similarity: (max_dist - distance) / max_dist
                        raw_score_scaled = convert_l2_squared(raw_score_val)
                    elif self._distance_metric == "cosine":
                        # Cosine distance ranges from 0 to 2, convert to similarity: (2.0 - distance) / 2.0
                        raw_score_scaled = convert_cosine_distance(raw_score_val)
                    else:
                        # Chroma IP is a distance: d = 1 - dot (range [0, 2])
                        # Convert to similarity: max(0, min(1, (2.0 - distance) / 2.0))
                        raw_score_scaled = convert_ip_distance(raw_score_val)
                    final_score = raw_score_scaled
            elif mode == "sparse":
                # Text search score (ChromaDB may return similarity score or distance)
                # If no distance info, use default score
                if raw_score_val is not None:
                    # If it's distance, convert to similarity
                    if raw_score_val <= 1.0:
                        final_score = 1.0 - raw_score_val
                    else:
                        final_score = raw_score_val
                else:
                    # No score info, use default value
                    final_score = 0.5
            else:  # hybrid or other
                if raw_score_val is not None:
                    # For hybrid search, score is already calculated during fusion
                    final_score = raw_score_val
                else:
                    final_score = 0.0

            metadata.setdefault("raw_score", raw_score_val)
            if raw_score_scaled is not None:
                metadata.setdefault("raw_score_scaled", raw_score_scaled)

            search_result = SearchResult(
                id=str(result_id),
                text=text,
                score=final_score,
                metadata=metadata,
            )
            search_results.append(search_result)

        return search_results

    def close(self) -> None:
        """Close vector store"""
        # ChromaDB client usually doesn't need explicit closing
        # But if it's a persistent client, can reset
        if hasattr(self, "_client") and self._client is not None:
            try:
                # ChromaDB client doesn't have close method, but can reset
                pass
            except Exception as e:
                logger.warning(f"Failed to close ChromaDB client: {e}")

    async def table_exists(self, table_name: str) -> bool:
        """Check if a collection exists in current database"""
        return bool([True for c in self._client.list_collections() if getattr(c, "name", None) == table_name])

    async def delete_table(self, table_name: str) -> None:
        """Delete a collection from current database"""
        await asyncio.to_thread(
            self._client.delete_collection,
            name=table_name,
        )
