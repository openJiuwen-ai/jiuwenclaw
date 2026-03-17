# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Agentic Retriever: Adds LLM query rewriting and multi-round fusion on top of any retriever.

When the underlying retriever is a GraphRetriever, graph-specific features
(graph expansion, triple linking) are automatically enabled. For all other
retriever types, the agent performs iterative query rewriting and result
fusion directly against the base retriever.
"""

from __future__ import annotations

import asyncio
from typing import Any, List, Literal, Optional, Tuple

from json_repair import repair_json

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import BaseModelClient
from openjiuwen.core.retrieval.common.retrieval_result import RetrievalResult, SearchResult
from openjiuwen.core.retrieval.common.triple_memory import TripleMemory
from openjiuwen.core.retrieval.retriever.base import Retriever
from openjiuwen.core.retrieval.retriever.graph_retriever import GraphRetriever
from openjiuwen.core.retrieval.utils.common import deduplicate
from openjiuwen.core.retrieval.utils.fusion import rrf_fusion

_READ_PROMPT = """
Your task is to find facts that help answer an input question.

You should present these facts as knowledge triples, which are structured as ("subject", "predicate", "object").
Example:
Question: When was Neville A. Stanton's employer founded?
Facts: [["Neville A. Stanton", "employer", "University of Southampton"], ["University of Southampton", "founded in", "1862"]]

Now you are given some documents:
{docs}

Based on these documents and preliminary facts that may be provided below, find additional supporting fact(s) that may help answer the following question.
Note: if the information you are given is insufficient, output only the relevant facts you can find.

Question: {query}
Facts: {facts}

Output the facts as a JSON array of triples, where each triple is an array of [subject, predicate, object].
Example output format: [["subject1", "predicate1", "object1"], ["subject2", "predicate2", "object2"]]
"""

_REWRITE_PROMPT = """
Given a question and its associated retrieved knowledge triples, you
are asked to evaluate if the triples by themselves are sufficient
to formulate an answer to the original question.

You must respond with a JSON object in the following format:

{{
  "sufficient": true/false,
  "next_question": "string or null"
}}

- If the triples are sufficient, set "sufficient" to true and "next_question" to null
- If the triples are not sufficient, set "sufficient" to false and provide a 
suitable next question in the "next_question" field

When the triples are not sufficient, please think about the additional evidence that needs to be found to 
answer the original question, and then provide a suitable next question for retrieving this potential evidence. 
Note that you have access to all the question rewriting steps that have been performed already, if any. 
Please make sure that the next question is different from all the previous questions. Break it down into smaller questions if needed.
As the number of question rewriting steps that have been performed already increases, 
the next question should be more vague, optimising for retrieving at least some evidence that is relevant to the original question.

Here are some examples:

# Example 1:
Original Question: The Sentinelese language is the language of people of one of which islands in the Bay of Bengal ?
Knowledge triples:
(Sentinelese language, Indigenous to, Sentinelese people)
(Bay of Bengal, area, Andaman and Nicobar Islands)

Response:
{{
  "sufficient": true,
  "next_question": null
}}

# Example 2:
Original Question: Who is the coach of the team owned by David Beckham?
Knowledge triples:
(David Beckham, co-owned, Inter Miami CF)
(David Beckham, country of citizenship, United Kingdom)

Response:
{{
  "sufficient": false,
  "next_question": "Who is the coach of Inter Miami CF?"
}}

Now, please carefully consider the following case:

Question History:
Original Question: {query}
{question_rewriting_history}

Knowledge triples:
{triples}

Response (JSON only, no additional text):
"""


class AgenticRetriever(Retriever):
    """A retriever that adds LLM query rewriting and multi-round fusion on top of any retriever.

    When initialised with a :class:`GraphRetriever`, graph-specific features
    such as graph expansion and triple linking are enabled automatically.
    For any other :class:`Retriever` subclass the agent performs iterative
    query rewriting and result fusion directly.

    Args:
        retriever: The underlying retriever to delegate to.  Accepts any
            ``Retriever`` subclass.  When a ``GraphRetriever`` is supplied,
            graph-specific features are enabled automatically.
        llm_client: LLM client used for triple extraction and query rewriting.
        max_iter: Maximum number of agent iterations.
    """

    def __init__(
        self,
        retriever: Retriever,
        llm_client: BaseModelClient,
        max_iter: int = 2,
    ) -> None:
        if retriever is None:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_NOT_FOUND,
                error_msg="retriever is required for AgenticRetriever",
            )
        if llm_client is None:
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_LLM_CLIENT_NOT_FOUND,
                error_msg="llm_client is required for AgenticRetriever",
            )
        self.retriever = retriever
        self._is_graph_retriever = isinstance(retriever, GraphRetriever)
        self.llm = llm_client
        if isinstance(max_iter, int) and max_iter > 0:
            self.max_iter = max_iter
        else:
            logger.warning(
                f"[Agentic] Invalid {max_iter=} provided, falling back to default of 2"
            )
            self.max_iter = 2
        index_type = getattr(retriever, "index_type", None) or "hybrid"
        if index_type == "vector":
            self._default_mode: Literal["vector", "sparse", "hybrid"] = "vector"
        elif index_type == "bm25":
            self._default_mode = "sparse"
        else:
            self._default_mode = "hybrid"

    @property
    def is_graph_retriever(self) -> bool:
        """Whether the underlying retriever is a GraphRetriever."""
        return self._is_graph_retriever

    @property
    def default_mode(self) -> Literal["vector", "sparse", "hybrid"]:
        """Default retrieval mode derived from the underlying retriever's index_type."""
        return self._default_mode

    async def retrieve(
        self,
        query: str,
        top_k: int = 5,
        score_threshold: Optional[float] = None,
        mode: Optional[Literal["vector", "sparse", "hybrid"]] = None,
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        if not (isinstance(top_k, int) and top_k > 0):
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_TOP_K_INVALID, error_msg="top_k is invalid, must be a positive integer"
            )

        resolved_mode: Literal["vector", "sparse", "hybrid"] = mode if mode is not None else self._default_mode

        if self._is_graph_retriever:
            return await self._retrieve_with_graph(
                query, top_k=top_k, score_threshold=score_threshold, mode=resolved_mode, **kwargs
            )
        return await self._retrieve_generic(
            query, top_k=top_k, score_threshold=score_threshold, mode=resolved_mode, **kwargs
        )

    async def _retrieve_with_graph(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: Optional[float],
        mode: Literal["vector", "sparse", "hybrid"],
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        """Multi-round retrieval using graph expansion and triple linking."""
        self._assert_graph_retriever()
        graph_expansion = kwargs.pop("graph_expansion", True)

        queries: List[str] = [query]
        history_results: List[List[RetrievalResult]] = []
        memory = TripleMemory()

        for turn in range(1, self.max_iter + 1):
            q = queries[-1]

            chunk_retriever = self.retriever.get_retriever_for_mode(mode, is_chunk=True)
            chunk_results = await chunk_retriever.retrieve(
                query=q,
                top_k=top_k,
                score_threshold=score_threshold,
                mode=mode,
                **kwargs,
            )

            if graph_expansion:
                proximal_triples = await self._read(q, chunk_results, existing_facts=None)
                linked_triples = await self._link_triples(proximal_triples, mode, **kwargs)
                chunk_results = await self.retriever.graph_expansion(
                    query=q,
                    chunks=chunk_results,
                    triples=linked_triples,
                    topk=top_k,
                    mode=mode,
                    **kwargs,
                )

            triples = await self._read(query, chunk_results, existing_facts=memory.memory)
            memory.batch_extend_memory(triples)
            history_results.append(chunk_results)

            if turn >= self.max_iter:
                break

            rewritten = await self._rewrite(query, memory.triples_str, question_history=queries)
            if not rewritten:
                break
            queries.append(rewritten)

        ret = await self._link_passages(memory.memory, mode, **kwargs)
        combined = rrf_fusion(ret + history_results)[:top_k]
        return combined

    async def _retrieve_generic(
        self,
        query: str,
        *,
        top_k: int,
        score_threshold: Optional[float],
        mode: Literal["vector", "sparse", "hybrid"],
        **kwargs: Any,
    ) -> List[RetrievalResult]:
        """Multi-round retrieval with iterative query rewriting and RRF fusion.

        This approach is described in more detail in: 
        Millions of GeAR-s: Extending GraphRAG to Millions of Documents
        <https://arxiv.org/abs/2507.17399>
        """
        queries: List[str] = [query]
        history_results: List[List[RetrievalResult]] = []
        memory = TripleMemory()

        for turn in range(1, self.max_iter + 1):
            q = queries[-1]

            chunk_results = await self.retriever.retrieve(
                query=q,
                top_k=top_k,
                score_threshold=score_threshold,
                mode=mode,
                **kwargs,
            )

            triples = await self._read(q, chunk_results, existing_facts=memory.memory)
            memory.batch_extend_memory(triples)
            history_results.append(chunk_results)

            if turn >= self.max_iter:
                break

            rewritten = await self._rewrite(query, memory.triples_str, question_history=queries)
            if not rewritten:
                break
            queries.append(rewritten)

        combined = rrf_fusion(history_results)[:top_k]
        return combined

    async def _llm_call_async(self, prompt: str) -> Optional[str]:
        """
        Invoke the LLM.

        Args:
            prompt: Prompt to send to the LLM.

        Returns:
            LLM response string, or None if invocation failed.
        """
        try:
            resp = await self.llm.invoke(
                messages=[{"role": "user", "content": prompt}],
                temperature=0.0,
            )
            return resp.content if hasattr(resp, "content") else str(resp)
        except Exception as e:
            logger.warning(f"[Agentic] LLM invocation failed: {e}")
            return None

    def _assert_graph_retriever(self) -> None:
        """Raise an error if the underlying retriever is not a GraphRetriever."""
        if not isinstance(self.retriever, GraphRetriever):
            raise build_error(
                StatusCode.RETRIEVAL_RETRIEVER_INVALID,
                error_msg="Underlying retriever must be a GraphRetriever to link passages",
            )

    async def _rewrite(
        self, query: str, triples_str: str, question_history: Optional[List[str]] = None
    ) -> Optional[str]:
        """
        Analyze if the current facts are sufficient to answer the query, generating a follow-up question if not.

        Args:
            query: Original user question.
            triples_str: String representation of knowledge triples in Triple memory.
            question_history: Question history list.

        Returns:
            Next question to ask if more info is needed, or None if sufficient.
        """
        # Format question rewriting history
        if question_history and len(question_history) > 1:
            history_lines = []
            for i, q in enumerate(question_history[1:], start=1):
                history_lines.append(f"Rewritten Question {i}: {q}")
            question_rewriting_history = "\n".join(history_lines)
        else:
            question_rewriting_history = "(No rewriting steps yet)"

        prompt = _REWRITE_PROMPT.format(
            query=query,
            triples=triples_str,
            question_rewriting_history=question_rewriting_history,
        )
        response = await self._llm_call_async(prompt)
        if response is None:
            return None

        # Parse JSON response
        try:
            response_json = repair_json(response, return_objects=True)
            if not isinstance(response_json, dict):
                logger.warning(f"[Agentic] Rewrite response JSON is not an object: {response_json}")
                return None
            sufficient = response_json.get("sufficient", False)
            next_question = response_json.get("next_question")

            if sufficient or not next_question:
                return None
            return next_question
        except Exception as e:
            logger.warning(f"[Agentic] Failed to parse rewrite response as JSON: {e}.")
            return None

    async def _read(
        self,
        query: str,
        passages: List[RetrievalResult],
        existing_facts: Optional[List[Tuple[str, ...]]] = None,
    ) -> List[Tuple[str, ...]]:
        """
        Extract knowledge triples from retrieved passages using the LLM.

        Args:
            query: Search query.
            passages: Retrieved passages.
            existing_facts: Optional list of facts (triples).

        Returns:
            List of extracted triples.
        """
        docs = "\n\n".join(str(p.text) for p in passages[:5] if p.text)
        facts_str = ", ".join(str(fact) for fact in existing_facts if fact) if existing_facts else "None"
        prompt = _READ_PROMPT.format(
            docs=docs,
            query=query,
            facts=facts_str,
        )
        response = await self._llm_call_async(prompt)
        if response is None:
            return []

        try:
            response_json = repair_json(response, return_objects=True)
            if not isinstance(response_json, list):
                logger.warning(f"[Agentic] Read response JSON is not a list: {response_json}")
                return []
            triples = [
                tuple(map(str, triple)) for triple in response_json if isinstance(triple, list) and len(triple) == 3
            ]
            return triples
        except Exception as e:
            logger.warning(f"[Agentic] Failed to parse read response as JSON: {e}.")
            return []

    async def _link_triples(
        self, triples: List[Tuple[str, ...]], mode: Literal["vector", "sparse", "hybrid"], **kwargs
    ) -> List[RetrievalResult]:
        """
        Links proximal triples to triples in the knowledge base.

        This method requires a :class:`GraphRetriever` as the underlying retriever.

        Args:
            triples: List of triples.
            mode: Retrieval mode (vector, sparse, hybrid).
            **kwargs: Additional parameters

        Returns:
            List of unique linked triples.
        """
        self._assert_graph_retriever()
        tasks = []
        triple_retriever = self.retriever.get_retriever_for_mode(mode, is_chunk=False)
        for triple in triples:
            triple_str = " ".join(map(str, triple))
            # Fetch SearchResult instead of RetrievalResult since we need ids for deduplication
            task = triple_retriever.retrieve_search_results(
                query=triple_str,
                top_k=1,
                mode=mode,
                **kwargs,
            )
            tasks.append(task)
        search_results = await asyncio.gather(*tasks, return_exceptions=True)

        total_triples = len(triples)
        all_triples: List[SearchResult] = []
        failed_triples = []

        for result in search_results:
            if isinstance(result, list) and result and isinstance(result[0], SearchResult):
                all_triples.append(result[0])
            else:
                if isinstance(result, Exception):
                    failed_triples.append(result)
                else:
                    logger.warning(f"[Agentic] Empty or Unexpected result type during link triples {type(result)}")
                    failed_triples.append(result)

        if failed_triples:
            logger.warning(
                f"[Agentic] Triple linking failed for {len(failed_triples)}/{total_triples} triples."
            )

        search_results = deduplicate(all_triples, key=lambda node: node.id)

        # Map back to RetrievalResult as required by the Graph Expansion flow
        retrieval_results = []
        for result in search_results:
            metadata = result.metadata or {}
            retrieval_result = RetrievalResult(
                text=result.text,
                score=result.score,
                metadata=metadata,
                doc_id=metadata.get("doc_id"),
                chunk_id=metadata.get("chunk_id"),
            )
            retrieval_results.append(retrieval_result)
        return retrieval_results

    async def _link_passages(
        self, triples: List[Tuple[str, ...]], mode: Literal["vector", "sparse", "hybrid"], **kwargs
    ) -> List[List[RetrievalResult]]:
        """
        Links triples to passages in the knowledge base.

        This method requires a :class:`GraphRetriever` as the underlying retriever.

        Args:
            triples: List of triples.
            mode: Retrieval mode (vector, sparse, hybrid).
            **kwargs: Additional parameters

        Returns:
            List of lists of linked passages.
        """
        self._assert_graph_retriever()
        chunk_retriever = self.retriever.get_retriever_for_mode(mode, is_chunk=True)
        tasks = []
        for triple in triples:
            triple_str = " ".join(map(str, triple))
            task = chunk_retriever.retrieve(
                query=triple_str,
                top_k=5,
                mode=mode,
                **kwargs,
            )
            tasks.append(task)
        retrieval_results = await asyncio.gather(*tasks, return_exceptions=True)

        results: List[List[RetrievalResult]] = []
        failed_results = []
        for result in retrieval_results:
            if isinstance(result, list) and result and isinstance(result[0], RetrievalResult):
                results.append(result)
            else:
                if isinstance(result, Exception):
                    failed_results.append(result)
                else:
                    logger.warning(f"[Agentic] Empty or Unexpected result type during link passages {type(result)}")
                    failed_results.append(result)

        if failed_results:
            logger.warning(
                f"[Agentic] Link passages failed for {len(failed_results)}/{len(retrieval_results)} triples."
            )

        return results

    async def batch_retrieve(
        self,
        queries: List[str],
        top_k: int = 5,
        **kwargs,
    ) -> List[List[RetrievalResult]]:
        tasks = [self.retrieve(query, top_k=top_k, **kwargs) for query in queries]
        return await asyncio.gather(*tasks)

    async def close(self) -> None:
        import inspect

        if hasattr(self.retriever, "close"):
            close_fn = self.retriever.close
            if inspect.iscoroutinefunction(close_fn):
                await close_fn()
            else:
                close_fn()
