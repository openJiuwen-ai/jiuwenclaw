# -*- coding: UTF-8 -*-
"""ReMe retrieval implementation."""

from typing import List

from openjiuwen.core.common.logging import context_engine_logger as logger

from ....core.context import RuntimeContext
from ....core.op import BaseOp
from ....schema import ReMeMemory, ReMeRetrievedMemory

from .prompt import ReMePrompts
from .utils import parse_json_field, parse_json_list_response


class RecallMemoryOp(BaseOp):
    """Retrieve relevant ReMe from ReMe.

    This operation:
    1. Gets embedding for the query
    2. Searches vector store for similar ReMe
    3. Converts vector nodes back to ReMeMemory objects
    """

    def __init__(self, topk_retrieval: int = 10):
        """Initialize recall operation.

        Args:
            topk_retrieval: Number of top results to retrieve
        """
        super().__init__(topk_retrieval=topk_retrieval)
        self.topk_retrieval = topk_retrieval

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute memory recall from ReMe.

        Args:
            context: Runtime context with 'query' and 'user_id'

        Sets:
            context.retrieved_memories: List of ReMeRetrievedMemory objects
        """
        query = context.query
        user_id = context.get("user_id", "default")

        if not self.embedding_model:
            raise ValueError("Embedding model not configured in ServiceContext")

        if not self.vector_store:
            raise ValueError("Vector store not configured in ServiceContext")

        # Get query embedding
        logger.debug("Generating query embedding...")
        query_embedding = await self.embedding_model.async_embed(query)

        # Search vector store with filter for ReMe memories
        logger.debug("Searching ReMe for top %s results...", self.topk_retrieval)
        vector_nodes = await self.vector_store.async_search(
            embedding=query_embedding,
            top_k=self.topk_retrieval,
            metadata_filter={"workspace_id": user_id, "type": "reme_memory"},
        )

        # Convert to ReMeRetrievedMemory objects
        memories = []
        for node in vector_nodes:
            try:
                # Convert to full ReMeMemory first
                memory = ReMeMemory.from_vector_node(node)
                # Create ReMeRetrievedMemory from ReMeMemory fields
                retrieved_mem = ReMeRetrievedMemory(
                    when_to_use=memory.when_to_use, content=memory.content
                )
                memories.append(retrieved_mem)
            except Exception as e:
                logger.warning("Failed to convert vector node to memory: %s", e)
                continue

        context.retrieved_memories = memories
        logger.info("Retrieved %s ReMe from ReMe", len(memories))


class RerankMemoryOp(BaseOp):
    """Rerank memories by relevance score."""

    def __init__(self, llm_rerank: bool = True, topk_rerank: int = 5):
        """Initialize rerank operation.

        Args:
            llm_rerank: Whether to use LLM for reranking
            topk_rerank: Number of top results after reranking
        """
        super().__init__(llm_rerank=llm_rerank, topk_rerank=topk_rerank)
        self.llm_rerank = llm_rerank
        self.topk_rerank = topk_rerank

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute memory reranking using LLM.

        Args:
            context: Runtime context with 'query' and 'retrieved_memories'

        Sets:
            context.retrieved_memories: Reranked list of ReMeRetrievedMemory objects (top-k)
        """
        if not self.llm_rerank:
            return

        query = context.query
        retrieved_memories = context.get("retrieved_memories", [])

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        if not retrieved_memories:
            logger.info("No memories retrieved, skipping rerank")
            return

        # Format memories for reranking prompt
        formatted_candidates = self._format_candidates_for_rerank(retrieved_memories)
        prompt_template = ReMePrompts.rerank_prompt
        user_prompt = prompt_template.format(
            query=query, num_candidates=len(retrieved_memories), candidates=formatted_candidates
        )
        response = await self.llm.async_generate(prompt=user_prompt)

        # Parse reranking response
        reranked_indices = parse_json_list_response(response, "ranked_indices")
        if reranked_indices:
            reranked_memories = []
            for idx in reranked_indices:
                if 0 <= idx < len(retrieved_memories):
                    reranked_memories.append(retrieved_memories[idx])

            # Add any remaining memories that weren't explicitly ranked
            ranked_indices_set = set(reranked_indices)
            for i, memory in enumerate(retrieved_memories):
                if i not in ranked_indices_set:
                    reranked_memories.append(memory)
        else:
            reranked_memories = retrieved_memories
            logger.warning("Failed to parse rerank response, using original order")

        # Trim to top-k
        reranked_memories = reranked_memories[: self.topk_rerank]
        context.retrieved_memories = reranked_memories

    @staticmethod
    def _format_candidates_for_rerank(candidates: List[ReMeRetrievedMemory]) -> str:
        """Format candidates for LLM reranking.

        Args:
            candidates: List of memory candidates to format.

        Returns:
            Formatted string representation of candidates for LLM evaluation.
        """
        formatted_candidates = []

        for i, candidate in enumerate(candidates):
            condition = candidate.when_to_use
            content = candidate.content

            candidate_text = f"Candidate {i}:\n"
            candidate_text += f"Condition: {condition}\n"
            candidate_text += f"Experience: {content}\n"

            formatted_candidates.append(candidate_text)

        return "\n---\n".join(formatted_candidates)


class RewriteMemoryOp(BaseOp):
    """Rewrite with memory."""

    def __init__(self, llm_rewrite: bool = True):
        """Initialize rewrite operation."""
        super().__init__(llm_rewrite=llm_rewrite)
        self.llm_rewrite = llm_rewrite

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute memory rewriting using LLM to create coherent context.

        Args:
            context: Runtime context with 'query' and 'retrieved_memories'

        Sets:
            context.memory_string: Rewritten context string from memories
        """
        retrieved_memories = context.get("retrieved_memories", [])

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        if not retrieved_memories:
            logger.info("No memories retrieved, skipping rewrite")
            context.memory_string = ""
            return

        # Format and rewrite retrieved memories using LLM
        original_memories = self._format_memories_for_context(retrieved_memories)
        if not self.llm_rewrite:
            context.memory_string = original_memories
            return
        prompt_template = ReMePrompts.rewrite_prompt
        user_prompt = prompt_template.format(
            current_query=context.query, original_context=original_memories
        )
        response = await self.llm.async_generate(prompt=user_prompt)

        # Parse rewritten context from response
        rewritten_context = parse_json_field(response, "rewritten_context")
        if rewritten_context:
            context.memory_string = rewritten_context
        else:
            logger.warning("Failed to parse rewritten context, using formatted original memories")
            context.memory_string = original_memories

    @staticmethod
    def _format_memories_for_context(memories: List[ReMeRetrievedMemory]) -> str:
        """Format memories for context generation.

        Args:
            memories: List of memories to format.

        Returns:
            Formatted string containing all memories with their conditions and content.
        """
        formatted_memories = []

        for i, memory in enumerate(memories, 1):
            condition = memory.when_to_use
            memory_content = memory.content
            memory_text = f"Memory {i}:\n  When to use: {condition}\n  Content: {memory_content}\n"

            formatted_memories.append(memory_text)

        return "\n".join(formatted_memories)
