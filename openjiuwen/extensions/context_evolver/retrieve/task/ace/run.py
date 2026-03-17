# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ACE algorithm retrieve operations.

This module contains retrieve operation for the ACE algorithm.
ACE retrieval simply loads all playbook bullets without ranking or filtering.
"""
from openjiuwen.core.common.logging import context_engine_logger as logger
from ....core.op import BaseOp
from ....core.context import RuntimeContext
from ....schema import ACEMemory, ACERetrievedMemory



class RecallMemoryOp(BaseOp):
    """Retrieve all ACE memories (playbook bullets) from vector store.

    Unlike other algorithms, ACE doesn't use semantic search or ranking.
    It simply loads the entire playbook for use in generation.
    """

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute memory recall by loading all ACE bullets.

        Args:
            context: Runtime context with 'user_id'

        Sets:
            context.retrieved_memories: List of ACERetrievedMemory objects
        """
        user_id = context.get("user_id", "default")

        if not self.vector_store:
            raise ValueError("Vector store not configured in ServiceContext")

        # Load all ACE memories for this user (no semantic search needed)
        logger.debug("Loading all ACE memories from vector store...")
        
        # Search with dummy embedding since we're filtering by metadata only
        all_nodes = await self.vector_store.async_search(
            embedding=[0.0] * 2560,  # Dummy embedding
            top_k=50,  # Max bullets
            metadata_filter={"workspace_id": user_id, "type": "ace_memory"}
        )

        # Convert to ACERetrievedMemory objects
        retrieved_memories = []
        for node in all_nodes:
            try:
                # First convert to ACEMemory
                ace_memory = ACEMemory.from_vector_node(node)
                
                # Then convert to ACERetrievedMemory
                retrieved_memory = ACERetrievedMemory(
                    id=ace_memory.id,
                    section=ace_memory.section,
                    content=ace_memory.content,
                    helpful=ace_memory.helpful,
                    harmful=ace_memory.harmful,
                    neutral=ace_memory.neutral
                )
                retrieved_memories.append(retrieved_memory)
            except Exception as e:
                logger.warning("Failed to convert ACE memory from node %s: %s", node.id, e)

        context.retrieved_memories = retrieved_memories
        logger.info("Retrieved %s ACE memories (playbook bullets)", len(retrieved_memories))


__all__ = [
    "RecallMemoryOp",
]
