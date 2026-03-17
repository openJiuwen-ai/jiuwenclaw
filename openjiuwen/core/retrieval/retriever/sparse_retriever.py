# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Sparse Retriever Implementation

Sparse retriever based on BM25.
"""

from typing import List, Literal, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult, SearchResult
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.vector_store.base import VectorStore


class SparseRetriever(Retriever):
    """Sparse retriever implementation (BM25)"""

    def __init__(
        self,
        vector_store: VectorStore,
        **kwargs,
    ):
        """
        Initialize sparse retriever

        Args:
            vector_store: Vector store instance (needs to support sparse search)
        """
        self.vector_store = vector_store

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        mode: Literal["vector", "sparse", "hybrid"] = "sparse",
        **kwargs,
    ) -> List[RetrievalResult]:
        """
        Retrieve documents (sparse retrieval)

        Args:
            query: Query string
            top_k: Number of results to return
            score_threshold: Score threshold
            mode: Retrieval mode (this retriever only supports sparse)
            **kwargs: Additional parameters

        Returns:
            List of retrieval results
        """
        if mode != "sparse":
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_MODE_NOT_SUPPORT,
                error_msg=f"SparseRetriever only supports 'sparse' mode, got {mode}",
            )

        # Execute sparse search
        search_results = await self.vector_store.sparse_search(
            query_text=query,
            top_k=top_k,
            filters=None,
        )

        # Convert to RetrievalResult
        retrieval_results = []
        for result in search_results:
            retrieval_result = RetrievalResult(
                text=result.text,
                score=result.score,
                metadata=result.metadata,
                doc_id=result.metadata.get("doc_id"),
                chunk_id=result.id,
            )
            retrieval_results.append(retrieval_result)

        return retrieval_results

    async def batch_retrieve(
        self,
        queries: List[str],
        top_k: int = 5,
        **kwargs,
    ) -> List[List[RetrievalResult]]:
        """Batch retrieval"""
        import asyncio

        # Execute multiple retrievals concurrently
        tasks = [self.retrieve(query, top_k=top_k, **kwargs) for query in queries]
        results = await asyncio.gather(*tasks)
        return results

    async def retrieve_search_results(
        self,
        query: str,
        top_k: int = 5,
        mode: Literal["vector", "sparse", "hybrid"] = "sparse",
        **kwargs,
    ) -> List[SearchResult]:
        """
        Retrieve documents (sparse retrieval)

        Args:
            query: Query string
            top_k: Number of results to return
            mode: Retrieval mode (this retriever only supports sparse)
            **kwargs: Additional parameters

        Returns:
            List of search results
        """
        if mode != "sparse":
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_MODE_NOT_SUPPORT,
                error_msg=f"SparseRetriever only supports 'sparse' mode, got {mode}",
            )

        # Execute sparse search
        search_results = await self.vector_store.sparse_search(
            query_text=query,
            top_k=top_k,
            filters=None,
        )
        return search_results

    async def close(self) -> None:
        """Close the retriever"""
        import inspect

        if self.vector_store:
            close_fn = getattr(self.vector_store, "close", None)
            if close_fn:
                if inspect.iscoroutinefunction(close_fn):
                    await close_fn()
                else:
                    close_fn()
