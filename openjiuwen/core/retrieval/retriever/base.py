# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Retriever Abstract Base Class

Provides a unified interface for retrievers.
"""

from abc import ABC, abstractmethod
from typing import List, Literal, Optional

from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult, SearchResult


class Retriever(ABC):
    """Retriever abstract base class"""

    @abstractmethod
    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
        **kwargs,
    ) -> List[RetrievalResult]:
        """
        Retrieve documents

        Args:
            query: Query string
            top_k: Number of results to return
            score_threshold: Score threshold
            mode: Retrieval mode (vector=vector retrieval, sparse=sparse retrieval/BM25, hybrid=hybrid retrieval)
            **kwargs: Additional parameters

        Returns:
            List of retrieval results
        """

    @abstractmethod
    async def batch_retrieve(
        self,
        queries: List[str],
        top_k: int = 5,
        **kwargs,
    ) -> List[List[RetrievalResult]]:
        """Batch retrieval"""
        pass

    async def retrieve_search_results(
        self,
        query: str,
        top_k: int = 5,
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
        **kwargs,
    ) -> List[SearchResult]:
        """
        Optional retrieval API that returns raw SearchResult(s).
        """

    async def close(self) -> None:
        """Close the retriever and release resources"""
