# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""
Graph Store Protocol

Protocol definition for graph vector store interface
"""

import asyncio
from typing import Any, Iterable, Optional, Protocol, runtime_checkable

from openjiuwen.core.foundation.store.base_embedding import Embedding
from openjiuwen.core.foundation.store.base_reranker import Reranker
from openjiuwen.core.foundation.store.graph.config import GraphConfig
from openjiuwen.core.foundation.store.graph.result_ranking import BaseRankConfig
from openjiuwen.core.foundation.store.query import QueryExpr


@runtime_checkable
class GraphStore(Protocol):
    """
    Protocol defining the interface for graph vector store.

    This protocol defines the standard interface that all graph stores must implement, providing methods
    for data storage, retrieval, and search operations on graph-structured data.
    """

    @property
    def config(self) -> GraphConfig:
        """Access graph store config"""

    @property
    def semophore(self) -> Optional[asyncio.Semaphore]:
        """Access graph store semophore"""

    @property
    def embedder(self) -> Optional[Embedding]:
        """Access graph store embedder"""

    # Factory method
    @classmethod
    def from_config(cls, config: GraphConfig, **kwargs) -> "GraphStore":
        """Create a graph store instance from configuration.

        Args:
            config: Graph configuration object
            **kwargs: Additional configuration parameters

        Returns:
            Configured graph store instance
        """

    # Data management methods
    def rebuild(self):
        """Drop the collections and rebuild indices"""

    async def refresh(self, *args, **kwargs):
        """Refresh: flush data changes to database and compact segments"""

    # Data addition methods
    async def add_data(self, collection: str, data: Iterable[dict], flush: bool = True, upsert: bool = False, **kwargs):
        """Add arbitrary data into database.

        Args:
            collection (str): Collection name for data insertion
            data (Iterable[dict]): Data to insert, must be Iterable type like list or tuple
            flush: Whether to flush changes immediately
            upsert: Whether to upsert (update if exists, insert if not) instead of insert
        """

    async def add_entity(self, entities: Iterable, flush: bool = True, upsert: bool = False, no_embed: bool = False):
        """Add entity objects to the graph store. Should also create embeddings for entities unless no_embed=True.

        Args:
            entities: Iterable of Entity objects to add
            flush: Whether to flush changes immediately
            upsert: Whether to upsert (update if exists, insert if not) instead of insert
            no_embed: Whether to skip embedding
        """

    async def add_relation(self, relations: Iterable, flush: bool = True, upsert: bool = False, no_embed: bool = False):
        """Add relation objects to the graph store. Should also create embeddings for relations unless no_embed=True.

        Args:
            relations: Iterable of Relation objects to add
            flush: Whether to flush changes immediately
            upsert: Whether to upsert (update if exists, insert if not) instead of insert
            no_embed: Whether to skip embedding
        """

    async def add_episode(self, episodes: Iterable, flush: bool = True, upsert: bool = False, no_embed: bool = False):
        """Add episode objects to the graph store. Should also create embeddings for episodes unless no_embed=True.

        Args:
            episodes: Iterable of Episode objects to add
            flush: Whether to flush changes immediately
            upsert: Whether to upsert (update if exists, insert if not) instead of insert
            no_embed: Whether to skip embedding
        """

    # Data query and deletion methods
    def is_empty(self, collection: str) -> bool:
        """Check if a collection is empty

        Args:
            collection (str): name of collection

        Returns:
            bool: whether the collection is empty.
        """

    async def query(
        self,
        collection: str,
        ids: Optional[list[Any]] = None,
        expr: Optional[QueryExpr] = None,
        silence_errors: bool = False,
        **kwargs,
    ) -> list[dict]:
        """Query graph objects from a collection.

        Args:
            collection: Collection name to query
            ids: Optional list of IDs to query
            expr: Optional filter expression
            silence_errors (bool): Supresses Exceptions and return empty list instead. Defaults to False.
            **kwargs: Additional arguments to pass into query, such as "limit".

        Returns:
            list of query results
        """

    async def delete(
        self, collection: str, ids: Optional[list[Any]] = None, expr: Optional[QueryExpr] = None, **kwargs
    ) -> dict:
        """Delete graph objects from a collection.

        Args:
            collection: Collection name to delete from
            ids: Optional list of IDs to delete
            expr: Optional filter expression
            **kwargs: Additional delete parameters

        Returns:
            Result of the delete operation
        """

    # Search methods
    async def search(
        self,
        query: str,
        k: int,
        collection: str,
        ranker_config: BaseRankConfig,
        *,
        reranker: Optional[Reranker] = None,
        bfs_depth: int = 0,
        bfs_k: int = 0,
        filter_expr: Optional[QueryExpr] = None,
        output_fields: Optional[list[str]] = None,
        query_embedding: Optional[list[float]] = None,
        **kwargs,
    ) -> dict[str, list[dict]]:
        """Search for graph objects using hybrid search.

        Args:
            query: Search query string
            k: Number of results to return
            collection: Collection to search ("entities", "relations", "episodes", or "all")
            ranker_config: Configuration for search ranking
            reranker (Optional[BaseReranker], optional): Cross-encoder re-ranker to use. Defaults to None.
            bfs_depth: Breadth-first search depth for graph expansion
            bfs_k: Maximum number of nodes to expand in BFS
            filter_expr: Optional filter expression
            output_fields: Fields to include in results
            query_embedding: Pre-computed embedding of query string, optional

            **kwargs supports following arguments:

                - language (str, optional): Language to use for reranking, "cn" for Chinese, "en" for English.\
                    Defaults to "en".
                - min_score (float, optional): Minimum similarity / maximum distance score threshold. Defaults to 0.0.

        Returns:
            dict[str, list[dict]]: dict mapping collection names to results
        """

    # Embedding management
    def attach_embedder(self, embedder: Embedding) -> None:
        """Attach an embedding service to the graph store.

        Args:
            embedder (Embedding): Embedding service to use.
        """

    # Utility methods
    def close(self) -> None:
        """Close the backend and clean up resources."""
