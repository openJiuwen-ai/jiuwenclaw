# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ReasoningBank algorithm retrieve operations.

This module implements the retrieval phase of the ReasoningBank algorithm:
- BuildQueryOp: Build query from input
- RecallMemoryOp: Retrieve relevant reasoning strategies from ReasoningBank
- RerankMemoryOp: Rerank memories by relevance score
- RewriteWithMemoryOp: Generate answer using retrieved reasoning strategies
"""

from typing import List
from openjiuwen.core.common.logging import context_engine_logger as logger
from ....core.op import BaseOp
from ....core.context import RuntimeContext
from ....schema import ReasoningBankMemory, ReasoningBankRetrievedMemory



class RecallMemoryOp(BaseOp):
    """Retrieve relevant reasoning strategies from ReasoningBank.

    This operation:
    1. Gets embedding for the query
    2. Searches vector store for similar reasoning strategies
    3. Converts vector nodes back to ReasoningBankMemory objects
    """

    def __init__(self, top_k: int = 1):
        """Initialize recall operation.

        Args:
            top_k: Number of top results to retrieve
        """
        super().__init__(top_k=top_k)
        self.top_k = top_k

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute memory recall from ReasoningBank.

        Args:
            context: Runtime context with 'query' and 'user_id'

        Sets:
            context.retrieved_memories: List of ReasoningBankRetrievedMemory objects
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

        # Search vector store with filter for ReasoningBank memories
        logger.debug("Searching ReasoningBank for top %s results...", self.top_k)
        vector_nodes = await self.vector_store.async_search(
            embedding=query_embedding,
            top_k=self.top_k,
            metadata_filter={"workspace_id": user_id, "type": "reasoning_bank_memory"}
        )

        # Convert to ReasoningBankRetrievedMemory objects
        memories = []
        for node in vector_nodes:
            try:
                # First convert to full ReasoningBankMemory
                memory = ReasoningBankMemory.from_vector_node(node)
                # Then extract individual memory items as ReasoningBankRetrievedMemory
                if memory.memory:
                    for item in memory.memory:
                        retrieved_mem = ReasoningBankRetrievedMemory(
                            title=item.title,
                            description=item.description,
                            content=item.content
                        )
                        memories.append(retrieved_mem)
            except Exception as e:
                logger.warning("Failed to convert vector node to memory: %s", e)
                continue

        context.retrieved_memories = memories
        logger.info("Retrieved %s reasoning strategies from ReasoningBank", len(memories))


__all__ = [
    "RecallMemoryOp"
]