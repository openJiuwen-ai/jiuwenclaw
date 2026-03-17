# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Hybrid Retriever Implementation

Hybrid retriever combining vector retrieval and sparse retrieval.
"""

from typing import List, Literal, Optional

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult, SearchResult
from openjiuwen.core.retrieval.embedding.base import Embedding
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.vector_store.base import VectorStore


class HybridRetriever(Retriever):
    """Hybrid retriever implementation (vector + sparse)"""

    def __init__(
        self,
        vector_store: VectorStore,
        embed_model: Optional[Embedding] = None,
        alpha: float = 0.5,
        **kwargs,
    ):
        """
        Initialize hybrid retriever

        Args:
            vector_store: Vector store instance
            embed_model: Embedding model instance (required for vector retrieval)
            alpha: Hybrid weight (0=pure sparse retrieval, 1=pure vector retrieval, 0.5=balanced)
        """
        self.vector_store = vector_store
        self.embed_model = embed_model
        self.alpha = alpha

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
        **kwargs,
    ) -> List[RetrievalResult]:
        """
        Retrieve documents (hybrid retrieval)

        Args:
            query: Query string
            top_k: Number of results to return
            score_threshold: Score threshold
            mode: Retrieval mode (this retriever supports hybrid, can also fallback to vector or sparse)
            **kwargs: Additional parameters (can include alpha parameter to override default)

        Returns:
            List of retrieval results
        """
        alpha = kwargs.get("alpha", self.alpha)

        if score_threshold is not None and mode != "vector":
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_SCORE_THRESHOLD_INVALID,
                error_msg="score_threshold is only supported when mode='vector'",
            )

        if mode == "hybrid":
            # Hybrid retrieval
            query_vector = None
            if self.embed_model:
                query_vector = await self.embed_model.embed_query(query)

            search_results = await self.vector_store.hybrid_search(
                query_text=query,
                query_vector=query_vector,
                top_k=top_k,
                alpha=alpha,
                filters=None,
            )
        elif mode == "vector":
            # Pure vector retrieval
            if self.embed_model is None:
                raise build_error(
                    StatusCode.RETRIEVAL_RETRIEVER_EMBED_MODEL_NOT_FOUND,
                    error_msg="embed_model is required for vector search",
                )

            query_vector = await self.embed_model.embed_query(query)
            search_results = await self.vector_store.search(
                query_vector=query_vector,
                top_k=top_k,
                filters=None,
            )
            if not search_results:
                search_results = await self.vector_store.sparse_search(
                    query_text=query,
                    top_k=top_k,
                    filters=None,
                )
        elif mode == "sparse":
            # Pure sparse retrieval
            search_results = await self.vector_store.sparse_search(
                query_text=query,
                top_k=top_k,
                filters=None,
            )
        else:
            raise build_error(StatusCode.RETRIEVAL_RETRIEVER_MODE_NOT_SUPPORT, error_msg=f"Unsupported mode: {mode}")

        # Convert to RetrievalResult
        retrieval_results = []
        for result in search_results:
            # Apply score threshold filtering
            if mode == "vector" and score_threshold is not None and result.score is not None:
                if result.score < score_threshold:
                    continue

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
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
        **kwargs,
    ) -> List[SearchResult]:
        """
        Retrieve documents (hybrid retrieval)

        Args:
            query: Query string
            top_k: Number of results to return
            mode: Retrieval mode (this retriever supports hybrid, can also fallback to vector or sparse)
            **kwargs: Additional parameters (can include alpha parameter to override default)

        Returns:
            List of search results
        """
        alpha = kwargs.get("alpha", self.alpha)

        if mode == "hybrid":
            # Hybrid retrieval
            query_vector = None
            if self.embed_model:
                query_vector = await self.embed_model.embed_query(query)

            search_results = await self.vector_store.hybrid_search(
                query_text=query,
                query_vector=query_vector,
                top_k=top_k,
                alpha=alpha,
                filters=None,
            )
        elif mode == "vector":
            # Pure vector retrieval
            if self.embed_model is None:
                raise build_error(
                    StatusCode.RETRIEVAL_RETRIEVER_EMBED_MODEL_NOT_FOUND,
                    error_msg="embed_model is required for vector search",
                )

            query_vector = await self.embed_model.embed_query(query)
            search_results = await self.vector_store.search(
                query_vector=query_vector,
                top_k=top_k,
                filters=None,
            )
            if not search_results:
                search_results = await self.vector_store.sparse_search(
                    query_text=query,
                    top_k=top_k,
                    filters=None,
                )
        elif mode == "sparse":
            # Pure sparse retrieval
            search_results = await self.vector_store.sparse_search(
                query_text=query,
                top_k=top_k,
                filters=None,
            )
        else:
            raise build_error(StatusCode.RETRIEVAL_RETRIEVER_MODE_NOT_SUPPORT, error_msg=f"Unsupported mode: {mode}")
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
