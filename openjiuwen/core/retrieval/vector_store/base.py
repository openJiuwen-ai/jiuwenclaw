# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Vector Store Abstract Base Class

Provides a unified interface for vector stores.
"""

from abc import ABC, abstractmethod
from math import isclose
from typing import Any, List, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.store.query import QueryExpr
from openjiuwen.core.retrieval.common.retrieval_result import SearchResult


class VectorStore(ABC):
    """Vector store abstract base class"""

    @staticmethod
    def _check_configs_matching(configured: dict, actual: dict) -> None:
        """Check if a config dict is equivalent to actual (which may have more keys)"""
        matches, mismatches = {}, {}
        for attr, val in configured.items():
            if attr in ["efSearchFactor"]:
                continue
            val_str = str(val).casefold()
            actual_val_str = str(actual.get(attr)).casefold()

            # We want either an exact match, or a close enough match numerically
            is_valid = actual_val_str == val_str
            if not is_valid and isinstance(val, (int, float)) and actual_val_str.replace(".", "").isnumeric():
                is_valid = isclose(float(actual_val_str), float(val), rel_tol=1e-2, abs_tol=1e-3)
            if is_valid:
                matches[attr] = val
            else:
                mismatches[attr] = dict(settings=val_str, actual=actual_val_str)

        if mismatches:
            raise build_error(
                StatusCode.RETRIEVAL_KB_DATABASE_CONFIG_INVALID,
                error_msg=f"database actual config differs from current knowledge base, \n{matches=}\n{mismatches=}",
            )

    @staticmethod
    @abstractmethod
    def create_client(database_name: str, path_or_uri: str, token: str = "", **kwargs) -> Any:
        """Create vector database client and ensure database exists"""

    @abstractmethod
    def check_vector_field(self) -> None:
        """Check if vector field configuration is consistent with actual database"""

    @abstractmethod
    async def add(
        self,
        data: dict | List[dict],
        batch_size: int | None = 128,
        **kwargs,
    ) -> None:
        """Add vectors"""

    @abstractmethod
    async def search(
        self,
        query_vector: List[float],
        top_k: int = 5,
        filters: Optional[dict | QueryExpr] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """
        Vector search

        Args:
            query_vector: Query vector
            top_k: Number of results to return
            filters: Metadata filter conditions
            **kwargs: Additional parameters

        Returns:
            List of search results
        """

    @abstractmethod
    async def sparse_search(
        self,
        query_text: str,
        top_k: int = 5,
        filters: Optional[dict | QueryExpr] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """
        Sparse search (BM25)

        Args:
            query_text: Query text
            top_k: Number of results to return
            filters: Metadata filter conditions
            **kwargs: Additional parameters

        Returns:
            List of search results
        """

    @abstractmethod
    async def hybrid_search(
        self,
        query_text: str,
        query_vector: Optional[List[float]] = None,
        top_k: int = 5,
        alpha: float = 0.5,
        filters: Optional[dict | QueryExpr] = None,
        **kwargs,
    ) -> List[SearchResult]:
        """
        Hybrid search (sparse retrieval + vector retrieval)

        Args:
            query_text: Query text
            query_vector: Query vector (optional, if provided will be used, otherwise needs to be embedded first)
            top_k: Number of results to return
            alpha: Hybrid weight (0=pure sparse retrieval, 1=pure vector retrieval, 0.5=balanced)
            filters: Metadata filter conditions
            **kwargs: Additional parameters

        Returns:
            List of search results
        """

    @abstractmethod
    async def delete(
        self,
        ids: Optional[List[str]] = None,
        filter_expr: str | QueryExpr | None = None,
        **kwargs,
    ) -> bool:
        """Delete vectors"""

    @abstractmethod
    async def table_exists(self, table_name: str) -> bool:
        """Check if a collection exists in current database"""

    @abstractmethod
    async def delete_table(self, table_name: str) -> None:
        """Delete a collection from current database"""
