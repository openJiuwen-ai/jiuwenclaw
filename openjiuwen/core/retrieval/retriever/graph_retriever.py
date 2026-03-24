# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Graph Retriever Implementation

A graph retriever combining chunk retrieval and graph retrieval with graph expansion support.
"""

import asyncio
import json
from typing import Any, Dict, List, Literal, Optional

import numpy as np

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult
from openjiuwen.core.retrieval.common.triple_beam import TripleBeam
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion


class TripleBeamSearch:
    """Triple beam search"""

    def __init__(
        self,
        retriever: Retriever,
        num_beams: int = 10,
        num_candidates_per_beam: int = 100,
        max_length: int = 2,
        encoder_batch_size: int = 256,
        **kwargs,
    ) -> None:
        if max_length < 1:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_MODE_INVALID, error_msg=f"expect max_length >= 1; got {max_length=}"
            )

        self.retriever = retriever
        self.num_beams = num_beams
        self.num_candidates_per_beam = num_candidates_per_beam

        self.max_length = max_length
        self.encoder_batch_size = encoder_batch_size
        self.embed_model = retriever.embed_model if hasattr(retriever, "embed_model") else None

    @staticmethod
    def _cosine_scores(query_vec, cand_vecs):
        """Compute cosine similarity between query (d,) and candidates (N, d)."""
        q = np.asarray(query_vec, dtype=np.float32)
        c = np.asarray(cand_vecs, dtype=np.float32)
        if q.ndim == 1:
            q = q[None, :]
        q_norm = np.linalg.norm(q, axis=1, keepdims=True) + 1e-12
        c_norm = np.linalg.norm(c, axis=1, keepdims=True) + 1e-12
        q = q / q_norm
        c = c / c_norm
        return (q @ c.T).squeeze(0)

    @staticmethod
    def _topk(scores, k: int):
        k = min(k, scores.shape[0])
        if k <= 0:
            return [], []
        idx = np.argpartition(-scores, k - 1)[:k]
        idx = idx[np.argsort(-scores[idx])]
        return idx.tolist(), scores[idx].tolist()

    @staticmethod
    def _format_triples(triples: List[RetrievalResult]) -> str:
        return "; ".join(x.text for x in triples)

    async def beam_search(self, query: str, triples: List[RetrievalResult]) -> List[TripleBeam]:
        """Perform beam search on query"""
        if not triples:
            logger.warning("beam search got empty input triples, query=%r", query)
            return []

        if not self.embed_model:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_EMBED_MODEL_NOT_FOUND,
                error_msg="embed_model is required for beam search",
            )

        texts = [x.text for x in triples] + [query]
        embeddings = await self.embed_model.embed_documents(texts, batch_size=self.embed_model.max_batch_size)
        embeddings = np.asarray(embeddings, dtype=np.float32)
        query_embedding = embeddings[-1]  # shape (emb_size,)
        embeddings = embeddings[:-1]  # shape (N, emb_size)

        scores = self._cosine_scores(query_embedding, embeddings)  # shape (N,)
        topk_indices, topk_scores = self._topk(scores, k=self.num_beams)
        beams = [TripleBeam([triples[idx]], score) for idx, score in zip(topk_indices, topk_scores)]

        for _ in range(self.max_length - 1):
            candidates_per_beam = await asyncio.gather(*[self._search_candidates(x) for x in beams])
            beams = await self._expand_beams(
                beams=beams,
                candidates_per_beam=candidates_per_beam,
                query_embedding=query_embedding,
            )

        return beams

    async def _expand_beams(
        self,
        query_embedding,
        beams: List[TripleBeam],
        candidates_per_beam: List[List[RetrievalResult]],
    ) -> List[TripleBeam]:
        texts: List[str] = []
        candidate_paths: List[tuple] = []  # (TripleBeam, RetrievalResult | None)
        exist_triples = {x.text for beam in beams for x in beam}

        for beam, cands in zip(beams, candidates_per_beam):
            if not cands:
                # Keep beam as is without expansion
                candidate_paths.append((beam, None))
                texts.append(self._format_triples(beam))
                continue

            # Add all valid candidates for this beam
            for triple in cands:
                if triple.text in exist_triples:
                    continue
                candidate_paths.append((beam, triple))
                texts.append(self._format_triples(beam.triples + [triple]))

        if not texts:
            return beams

        if not self.embed_model:
            return beams

        # Get embeddings for all candidate paths
        embeddings = await self.embed_model.embed_documents(texts, batch_size=self.embed_model.max_batch_size)
        embeddings = np.asarray(embeddings, dtype=np.float32)
        next_scores = self._cosine_scores(query_embedding, embeddings)  # shape (N, )

        # Select top-k paths by score
        topk_indices, _ = self._topk(next_scores, k=self.num_beams)

        # Build new beams from selected paths
        _beams = []
        seen_beam_texts = set()  # Track unique beam combinations to avoid duplicates

        for idx in topk_indices:
            beam, next_triple = candidate_paths[idx]

            if next_triple is None:
                # Keep original beam
                beam_text = self._format_triples(beam)
                if beam_text not in seen_beam_texts:
                    _beams.append(beam)
                    seen_beam_texts.add(beam_text)
            else:
                # Create expanded beam
                new_triples = beam.triples + [next_triple]
                beam_text = self._format_triples(new_triples)

                if beam_text not in seen_beam_texts:
                    _beams.append(TripleBeam(new_triples, float(next_scores[idx])))
                    seen_beam_texts.add(beam_text)

        return _beams

    async def _search_candidates(self, beam: TripleBeam) -> List[RetrievalResult]:
        if len(beam) < 1:
            raise RuntimeError("unexpected empty beam")

        triple_data = beam[-1].metadata.get("triple")
        if not triple_data:
            logger.warning("beam has no triple metadata")
            return []

        try:
            triple = json.loads(triple_data)
        except (json.JSONDecodeError, TypeError) as e:
            logger.warning("[graph] Failed to parse triple metadata: %s", e)
            return []

        if not triple or len(triple) < 2:
            return []

        entities = {triple[0], triple[-1]}
        query_str = " ".join(entities)

        mode = getattr(self.retriever, "index_type")
        if mode == "bm25":
            mode = "sparse"
            
        # Use retrieve method instead of SearchQuery
        nodes = await self.retriever.retrieve(
            query=query_str,
            top_k=self.num_candidates_per_beam,
            mode=mode,
        )

        ret = []
        for x in nodes:
            if x in beam:
                continue

            x_triple = json.loads(x.metadata.get("triple"))
            if not x_triple or len(x_triple) < 2:
                continue

            if x_triple[0] not in entities and x_triple[-1] not in entities:
                continue

            ret.append(x)

        if not ret:
            logger.warning("empty candidates for beam: %r", self._format_triples(beam))

        return ret


class GraphRetriever(Retriever):
    """Graph retriever implementation combining chunk retrieval and graph retrieval"""

    def __init__(
        self,
        chunk_retriever: Optional[Retriever] = None,
        triple_retriever: Optional[Retriever] = None,
        vector_store: Optional[Any] = None,
        embed_model: Optional[Any] = None,
        chunk_collection: Optional[str] = None,
        triple_collection: Optional[str] = None,
        **kwargs,
    ):
        """
        Initialize graph retriever

        Args:
            chunk_retriever: Chunk retriever (for document chunk retrieval, optional,
                dynamically created based on mode if not provided)
            triple_retriever: Triple retriever (for triple retrieval, optional,
                dynamically created based on mode if not provided)
            vector_store: Vector store instance (for dynamic retriever creation)
            embed_model: Embedding model instance (for dynamic retriever creation)
            chunk_collection: Chunk collection name (for dynamic retriever creation)
            triple_collection: Triple collection name (for dynamic retriever creation)
        """
        self.chunk_retriever = chunk_retriever
        self.triple_retriever = triple_retriever
        self.vector_store = vector_store
        self.embed_model = embed_model
        self.chunk_collection = chunk_collection
        self.triple_collection = triple_collection
        self.index_type: Optional[str] = None  # Will be automatically injected by upper layer (e.g. KnowledgeBase)

    def _allowed_modes(self) -> Dict[str, set]:
        return {
            "vector": {"vector"},
            "bm25": {"sparse"},
            "hybrid": {"vector", "sparse", "hybrid"},
        }

    def _ensure_mode_allowed(self, mode: Literal["vector", "sparse", "hybrid"]) -> None:
        if self.index_type is None:
            # Don't enforce validation when index_type is not injected (ensured by upper layer)
            return
        allowed = self._allowed_modes().get(self.index_type)
        if allowed is None:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_INDEX_TYPE_NOT_SUPPORT,
                error_msg=f"Unsupported index_type={self.index_type}",
            )
        if mode not in allowed:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_MODE_INVALID,
                error_msg=(
                    f"mode={mode} is incompatible with index_type={self.index_type}; allowed modes: {sorted(allowed)}"
                ),
            )

    def _retriever_supports_mode(self, retriever: Retriever, mode: str) -> bool:
        from openjiuwen.core.retrieval.retriever.hybrid_retriever import HybridRetriever
        from openjiuwen.core.retrieval.retriever.sparse_retriever import SparseRetriever
        from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever

        if isinstance(retriever, VectorRetriever):
            return mode == "vector"
        if isinstance(retriever, SparseRetriever):
            return mode == "sparse"
        if isinstance(retriever, HybridRetriever):
            return mode in {"vector", "sparse", "hybrid"}

        supported = getattr(retriever, "SUPPORTED_MODES", None)
        if supported is not None:
            return mode in supported
        return True

    def get_retriever_for_mode(
        self,
        mode: Literal["vector", "sparse", "hybrid"],
        is_chunk: bool = True,
    ) -> Retriever:
        """
        Get corresponding retriever based on mode

        Args:
            mode: Retrieval mode
            is_chunk: Whether chunk retriever (True=chunk_retriever, False=triple_retriever)

        Returns:
            Corresponding retriever instance
        """
        self._ensure_mode_allowed(mode)

        # If fixed retriever is provided, use it directly (but need to check if it supports the mode)
        fixed_retriever = self.chunk_retriever if is_chunk else self.triple_retriever
        if fixed_retriever:
            if not self._retriever_supports_mode(fixed_retriever, mode):
                raise build_error(
                    StatusCode.RETRIEVAL_RETRIEVER_CAPABILITY_NOT_SUPPORT,
                    error_msg=f"Provided {'chunk' if is_chunk else 'triple'} retriever "
                    f"{fixed_retriever.__class__.__name__} does not support mode={mode}",
                )
            return fixed_retriever

        # Dynamically create retriever
        if not self.vector_store:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_VECTOR_STORE_NOT_FOUND,
                error_msg="vector_store is required for dynamic retriever creation",
            )

        collection_name = self.chunk_collection if is_chunk else self.triple_collection
        self.vector_store.collection_name = collection_name
        if not collection_name:
            collection_type = "chunk" if is_chunk else "triple"
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_COLLECTION_NOT_FOUND,
                error_msg=f"{collection_type}_collection is required for dynamic retriever creation",
            )

        # Create corresponding retriever based on mode
        if mode == "vector":
            from openjiuwen.core.retrieval.retriever.vector_retriever import VectorRetriever

            if not self.embed_model:
                raise build_error(
                    StatusCode.RETRIEVAL_RETRIEVER_EMBED_MODEL_NOT_FOUND,
                    error_msg="embed_model is required for vector mode",
                )
            retriever = VectorRetriever(
                vector_store=self.vector_store,
                embed_model=self.embed_model,
            )
        elif mode == "sparse":
            from openjiuwen.core.retrieval.retriever.sparse_retriever import SparseRetriever

            retriever = SparseRetriever(
                vector_store=self.vector_store,
            )
        else:  # hybrid
            from openjiuwen.core.retrieval.retriever.hybrid_retriever import HybridRetriever

            retriever = HybridRetriever(
                vector_store=self.vector_store,
                embed_model=self.embed_model,
            )

        return retriever

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
        **kwargs,
    ) -> List[RetrievalResult]:
        """
        Retrieve documents (graph retrieval)

        Args:
            query: Query string
            top_k: Number of results to return
            score_threshold: Score threshold
            mode: Retrieval mode (must be compatible with index_type)
            **kwargs: Additional parameters (may include graph_hops, etc.)

        Returns:
            List of retrieval results
        """
        self._ensure_mode_allowed(mode)
        graph_hops = kwargs.get("graph_hops", 2)
        # GraphRetriever always performs graph expansion by default, caller doesn't need to pass graph_expansion flag
        if score_threshold is not None and mode != "vector":
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_SCORE_THRESHOLD_INVALID,
                error_msg="score_threshold is only supported when mode='vector'",
            )
        effective_threshold = score_threshold

        # Get corresponding retriever based on mode
        chunk_retriever = self.get_retriever_for_mode(mode, is_chunk=True)

        # First perform chunk retrieval
        chunk_results = await chunk_retriever.retrieve(
            query=query,
            top_k=top_k,
            score_threshold=effective_threshold,
            mode=mode,
        )

        logger.info(
            f"[graph] Graph retrieval: graph_expansion=True chunk_hits={len(chunk_results)} topk={top_k} mode={mode}"
        )

        expanded_results = await self.graph_expansion(
            query=query,
            chunks=chunk_results,
            topk=top_k,
            mode=mode,
            max_length=graph_hops,
        )
        return expanded_results

    async def graph_expansion(
        self,
        query: str,
        chunks: List[RetrievalResult],
        triples: Optional[List[RetrievalResult]] = None,
        topk: Optional[int] = None,
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
        **kwargs,
    ) -> List[RetrievalResult]:
        """
        Graph expansion using beam search

        Args:
            query: Query string
            chunks: Initial chunk retrieval results
            triples: Optional pre-fetched triples (if None, will fetch from chunks)
            topk: Final return count
            mode: Retrieval mode

        Returns:
            List of expanded retrieval results
        """
        self._ensure_mode_allowed(mode)
        if not chunks:
            logger.warning("[graph] chunk_retriever returned empty, no results to expand (mode=%s)", mode)
            if mode == "sparse":
                sparse_retriever = self.get_retriever_for_mode("sparse", is_chunk=True)
                fallback = await sparse_retriever.retrieve(
                    query=query,
                    top_k=topk or 5,
                    mode="sparse",
                )
                return fallback[:topk] if topk else fallback
            return []

        if not triples:
            try:
                triples = await self._fetch_triples(chunks, mode)
                logger.info(
                    "[graph] Fetching triples from chunk index: chunks=%d triples=%d",
                    len(chunks),
                    len(triples),
                )
            except Exception as e:
                logger.warning("[graph] Failed to fetch triples from chunk index: %s", e)
                triples = []

        if not triples:
            logger.info("[graph] No triples found, returning original chunks")
            return chunks[:topk] if topk else chunks

        # Perform beam search on triples
        try:
            triple_retriever = self.get_retriever_for_mode(mode, is_chunk=False)
            triple_beam_search = TripleBeamSearch(retriever=triple_retriever, **kwargs)
            beams = await triple_beam_search.beam_search(query, triples)
        except Exception as e:
            logger.warning("[graph] Beam search failed: %s, falling back to chunks", e)
            return chunks[:topk] if topk else chunks

        if not beams:
            logger.info("[graph] Beam search returned empty, returning original chunks")
            return chunks[:topk] if topk else chunks

        # Extract triples from beams
        max_length = max(len(x) for x in beams)
        expanded_triples = []
        for col in range(max_length):
            for beam in beams:
                if col >= len(beam):
                    continue
                expanded_triples.append(beam[col])

        # Fetch chunks based on expanded triples
        new_chunks = await self._fetch_chunks(expanded_triples, mode)
        logger.info(
            "[graph] Graph expansion beam results: triples=%d additional_chunks=%d",
            len(expanded_triples),
            len(new_chunks),
        )

        # Fuse results
        if new_chunks:
            fused = rrf_fusion([new_chunks, chunks], k=60)
        else:
            fused = chunks

        return fused[:topk] if topk else fused

    def _is_chroma_client(self, client) -> bool:
        """Check if client is a Chroma client."""
        # ChromaDB's client structure can vary
        return (
            hasattr(client, "get_collection")
            and hasattr(client, "list_collections")
            and callable(getattr(client, "get_collection", None))
        )

    async def _query_by_filter_milvus(
        self,
        client,
        collection_name: str,
        filter_expr: str,
        limit: int = 100,
    ) -> List[dict]:
        """Query Milvus collection by filter."""
        results = await asyncio.to_thread(
            client.query,
            collection_name=collection_name,
            filter=filter_expr,
            output_fields=["*"],
            limit=limit,
        )
        return results if results else []

    async def _query_by_filter_chroma(
        self,
        client,
        collection_name: str,
        where_expr: dict,
        limit: int = 100,
    ) -> List[dict]:
        """Query Chroma collection by chunk_id."""
        try:
            collection = await asyncio.to_thread(
                client.get_collection,
                name=collection_name,
            )

            # Chroma uses where clause for filtering
            results = await asyncio.to_thread(
                collection.get,
                where=where_expr,
                limit=limit,
            )

            # Convert Chroma results to common format
            if not results or not results.get("ids"):
                return []

            items = []
            ids = results.get("ids", [])
            documents = results.get("documents", [])
            metadatas = results.get("metadatas", [])

            for item_id, doc, meta in zip(ids, documents, metadatas):
                item = {
                    "id": item_id,
                    "content": doc,
                    "metadata": meta,
                    "score": 0.0,  # Chroma get() doesn't return scores
                }
                items.append(item)

            return items
        except Exception as e:
            logger.warning(f"[graph] Chroma query failed: {e}")
            return []

    async def _fetch_triples(
        self,
        chunks: List[RetrievalResult],
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
    ) -> List[RetrievalResult]:
        """
        Return a list of triples associated with a list of chunks.

        Args:
            chunks: List of chunk retrieval results
            mode: Retrieval mode

        Returns:
            List of triple results
        """
        chunk_ids = [x.metadata.get("chunk_id") for x in chunks if x.metadata.get("chunk_id")]

        if not chunk_ids:
            return []

        # Get triple retriever
        triple_retriever = self.get_retriever_for_mode(mode, is_chunk=False)

        # Check if the triple retriever has vector store
        if not hasattr(triple_retriever, "vector_store"):
            logger.warning("[graph] triple_retriever does not have vector_store, cannot fetch triples")
            return []

        client = triple_retriever.vector_store.client if hasattr(triple_retriever.vector_store, "client") else None
        collection_name = (
            triple_retriever.vector_store.collection_name
            if hasattr(triple_retriever.vector_store, "collection_name")
            else None
        )

        if not client or not collection_name:
            logger.warning("[graph] Cannot fetch triples: missing client or collection_name")
            return []

        # Query triples
        async def fetch_for_chunks(chunk_ids: List[str]) -> List[RetrievalResult]:
            """Fetch triples for a single chunk."""
            from pymilvus import MilvusClient

            try:
                if isinstance(client, MilvusClient):
                    escaped_ids = [f'"{cid}"' for cid in chunk_ids]
                    filter_expr = f"chunk_id IN [{', '.join(escaped_ids)}]"
                    results = await self._query_by_filter_milvus(client, collection_name, filter_expr, limit=100)
                elif self._is_chroma_client(client):
                    where_expr = {"chunk_id": {"$in": chunk_ids}}
                    results = await self._query_by_filter_chroma(client, collection_name, where_expr, limit=100)
                else:
                    logger.warning("[graph] Unsupported client type in fetch_for_chunk")
                    return []

                nodes = []
                for item in results:
                    metadata = item.get("metadata", {}) or {}
                    if isinstance(metadata, str):
                        try:
                            metadata = json.loads(metadata)
                        except Exception:
                            metadata = {}
                    # doc_id may be at top level (e.g. Milvus query returns document_id there)
                    doc_id = item.get("document_id") or metadata.get("doc_id")

                    node = RetrievalResult(
                        text=item.get("content", ""),
                        score=item.get("score", 0.0),
                        metadata=metadata,
                        doc_id=doc_id,
                        chunk_id=metadata.get("chunk_id", ""),
                    )
                    nodes.append(node)
                return nodes
            except Exception as e:
                logger.warning(f"[graph] Failed to fetch triples for chunks: {e}")
                return []

        results = await fetch_for_chunks(chunk_ids)

        return results

    async def _fetch_chunks(
        self,
        triples: List[RetrievalResult],
        mode: Literal["vector", "sparse", "hybrid"] = "hybrid",
    ) -> List[RetrievalResult]:
        """
        Fetch chunks based on triples metadata.

        Args:
            triples: List of triple retrieval results
            mode: Retrieval mode

        Returns:
            List of chunk retrieval results
        """
        # Deduplicate chunk_ids from triples
        chunk_ids_set = set()
        for node in triples:
            chunk_id = node.chunk_id
            if chunk_id:
                chunk_ids_set.add(chunk_id)

        chunk_ids = list(chunk_ids_set)

        if not chunk_ids:
            return []

        # Get chunk retriever attributes
        chunk_retriever = self.get_retriever_for_mode(mode, is_chunk=True)

        # Check if the chunk retriever has vector store
        if not hasattr(chunk_retriever, "vector_store"):
            logger.warning("[graph] chunk_retriever does not have vector_store, cannot fetch chunks")
            return []

        client = chunk_retriever.vector_store.client if hasattr(chunk_retriever.vector_store, "client") else None
        collection_name = (
            chunk_retriever.vector_store.collection_name
            if hasattr(chunk_retriever.vector_store, "collection_name")
            else None
        )

        if not client or not collection_name:
            logger.warning("[graph] Cannot fetch chunks: missing client or collection_name")
            return []

        async def fetch_chunk(chunk_id: str) -> Optional[RetrievalResult]:
            """Fetch a single chunk by chunk_id."""
            from pymilvus import MilvusClient

            try:
                if isinstance(client, MilvusClient):
                    filter_expr = f'chunk_id == "{chunk_id}"'
                    results = await self._query_by_filter_milvus(client, collection_name, filter_expr, limit=1)
                elif self._is_chroma_client(client):
                    where_expr = {"chunk_id": chunk_id}
                    results = await self._query_by_filter_chroma(client, collection_name, where_expr, limit=1)
                else:
                    logger.warning("[graph] Unsupported client type in fetch_chunk")
                    return None
            except Exception as e:
                logger.warning(f"[graph] Failed to query chunk {chunk_id}: {e}")
                return None

            if not results:
                return None

            item = results[0]

            # Extract metadata; doc_id may be at top level (e.g. Milvus query returns document_id there)
            metadata = item.get("metadata", {}) or {}
            if isinstance(metadata, str):
                try:
                    metadata = json.loads(metadata)
                except Exception:
                    metadata = {}
            doc_id = item.get("document_id") or metadata.get("doc_id")

            # Create RetrievalResult
            result = RetrievalResult(
                text=item.get("content", ""),
                score=item.get("score", 0.0),
                metadata=metadata,
                doc_id=doc_id,
                chunk_id=metadata.get("chunk_id", chunk_id),
            )
            return result

        # Fetch all chunks concurrently
        tasks = [fetch_chunk(chunk_id) for chunk_id in chunk_ids]
        results = await asyncio.gather(*tasks)

        # Filter out None results
        chunks = []
        for node in results:
            if node is None:
                continue
            chunks.append(node)

        return chunks

    async def batch_retrieve(
        self,
        queries: List[str],
        top_k: int = 5,
        **kwargs,
    ) -> List[List[RetrievalResult]]:
        """Batch retrieval"""
        # Execute multiple retrievals concurrently
        tasks = [self.retrieve(query, top_k=top_k, **kwargs) for query in queries]
        results = await asyncio.gather(*tasks)
        return results

    async def close(self) -> None:
        """Close retriever"""
        import inspect

        # Close fixed retrievers
        if self.chunk_retriever:
            close_fn = getattr(self.chunk_retriever, "close", None)
            if close_fn:
                if inspect.iscoroutinefunction(close_fn):
                    await close_fn()
                else:
                    close_fn()
        if self.triple_retriever:
            close_fn = getattr(self.triple_retriever, "close", None)
            if close_fn:
                if inspect.iscoroutinefunction(close_fn):
                    await close_fn()
                else:
                    close_fn()
