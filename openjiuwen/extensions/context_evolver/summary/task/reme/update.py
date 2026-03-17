# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""REME algorithm summary operations - Combined module.

This module contains all summary operations for the REME algorithm:
- TrajectoryPreprocessOp: Preprocess and validate trajectories
- SuccessExtractionOp: Extract insights from successful trajectories
- FailureExtractionOp: Extract lessons from failed trajectories
- ComparativeExtractionOp: Compare successes and failures for insights
- MemoryValidationOp: Validate quality of extracted memories
- MemoryDeduplicationOp: Remove duplicate memories
- UpdateVectorStoreOp: Persist memories to vector store
"""
import json
import re
from typing import List
from datetime import datetime, timezone
from openjiuwen.core.common.logging import context_engine_logger as logger
from ....core.op import BaseOp
from ....core.context import RuntimeContext
from ....schema import ReMeMemory, ReMeMemoryMetadata


from .prompt import ReMePrompts
from .utils import parse_json_experience_response, calculate_cosine_similarity


class TrajectoryPreprocessOp(BaseOp):
    """Preprocess trajectories for summarization.

    This operation:
    1. Validates trajectory data
    2. Filters out invalid/incomplete trajectories
    3. Normalizes feedback values
    4. Groups by feedback type
    """

    def __init__(self):
        """Initialize preprocessing operation."""
        super().__init__()

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute trajectory preprocessing.

        Args:
            context: Runtime context with 'trajectories' (list of dicts or Trajectory objects)

        Sets:
            context.success_trajectories: Trajectories with helpful feedback
            context.failure_trajectories: Trajectories with harmful feedback
            context.all_trajectories: All trajectories
        """
        trajectories = context.get("trajectories", [])

        if not trajectories:
            logger.warning("No trajectories to process")
            context.success_trajectories = []
            context.failure_trajectories = []
            return

        # Group by feedback type using context.score list
        scores = context.get("score", [])
        threshold = context.get("threshold", 1)
        success_trajectories = []
        failure_trajectories = []
        all_trajectories = []
        for trajectory, score in zip(trajectories, scores):
            if score >= threshold:
                success_trajectories.append(trajectory)
            else:
                failure_trajectories.append(trajectory)
            all_trajectories.append(trajectory)


        # Store in context
        context.success_trajectories = success_trajectories
        context.failure_trajectories = failure_trajectories
        context.all_trajectories = all_trajectories

        logger.info(
            "Preprocessed %s trajectories: %s success, %s failure",
            len(trajectories), len(success_trajectories), len(failure_trajectories)
        )


class SuccessExtractionOp(BaseOp):
    """Extract insights from successful trajectories.

    This operation analyzes trajectories marked as helpful to extract
    reusable knowledge in the form of TaskMemory objects.
    """
    def __init__(self, use_extraction: bool = True):
        super().__init__(use_extraction=use_extraction)
        self.use_extraction = use_extraction

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute success extraction.

        Args:
            context: Runtime context with 'success_trajectories'

        Sets:
            context.success_memories: List of TaskMemory objects extracted from successes
        """
        if not self.use_extraction:
            context.success_memories = []
            return

        success_trajectories = context.get("success_trajectories", [])

        if not success_trajectories:
            logger.info("No success trajectories to extract from")
            context.success_memories = []
            return

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        user_id = context.get("user_id", "default")
        query = context.get("query", "")


        logger.debug(
            "Extracting insights from %s successful trajectories...",
            len(success_trajectories)
        )
        memories = []
        for trajectory in success_trajectories:
            # Build prompt for LLM analysis
            prompt_template = ReMePrompts.success_memory_prompt
            user_prompt = prompt_template.format(
                query=query,
                step_sequence=trajectory,
                outcome="successful"
            )
            # Call LLM
            response = await self.llm.async_generate(
                prompt=user_prompt
            )
            # Parse response into ReMeMemory objects using utility function
            experiences = parse_json_experience_response(response)

            # Convert to ReMeMemory objects
            for exp_data in experiences:
                try:
                    memory_metadata = ReMeMemoryMetadata(
                        tags=exp_data.get("tags", []),
                        step_type=exp_data.get("step_type", ""),
                        tools_used=exp_data.get("tools_used", []),
                        confidence=exp_data.get("confidence", 1.0),
                        freq=0,
                        utility=0
                    )
                    memory = ReMeMemory(
                        workspace_id=user_id,
                        when_to_use=exp_data.get("when_to_use", ""),
                        content=exp_data.get("experience", ""),
                        score=1.0,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                        metadata=memory_metadata
                    )
                    memories.append(memory)
                except Exception as e:
                    logger.warning("Failed to create ReMeMemory from parsed data: %s", e)


        context.success_memories = memories
        logger.info("Extracted %s insights from success trajectories", len(memories))


class FailureExtractionOp(BaseOp):
    """Extract insights from failed trajectories.

    This operation analyzes trajectories marked as harmful to extract
    warnings and lessons learned about what NOT to do.
    """
    def __init__(self, use_extraction: bool = True):
        super().__init__(use_extraction=use_extraction)
        self.use_extraction = use_extraction

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute failure extraction.

        Args:
            context: Runtime context with 'failure_trajectories'

        Sets:
            context.failure_memories: List of ReMeMemory objects extracted from failures
        """
        if not self.use_extraction:
            context.failure_memories = []
            return

        failure_trajectories = context.get("failure_trajectories", [])

        if not failure_trajectories:
            logger.info("No failure trajectories to extract from")
            context.failure_memories = []
            return

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        user_id = context.get("user_id", "default")
        query = context.get("query", "")


        logger.debug(
            "Extracting insights from %s failed trajectories...",
            len(failure_trajectories)
        )
        memories = []
        for trajectory in failure_trajectories:
            # Build prompt for LLM analysis
            prompt_template = ReMePrompts.failure_memory_prompt
            user_prompt = prompt_template.format(
                query=query,
                step_sequence=trajectory,
                outcome="failed"
            )
            # Call LLM
            response = await self.llm.async_generate(
                prompt=user_prompt
            )
            # Parse response into ReMeMemory objects using utility function
            experiences = parse_json_experience_response(response)

            # Convert to ReMeMemory objects
            for exp_data in experiences:
                try:
                    memory_metadata = ReMeMemoryMetadata(
                        tags=exp_data.get("tags", []),
                        step_type=exp_data.get("step_type", ""),
                        tools_used=exp_data.get("tools_used", []),
                        confidence=exp_data.get("confidence", 1.0),
                        freq=0,
                        utility=0
                    )
                    memory = ReMeMemory(
                        workspace_id=user_id,
                        when_to_use=exp_data.get("when_to_use", ""),
                        content=exp_data.get("experience", ""),
                        score=1.0,
                        created_at=datetime.now(timezone.utc),
                        updated_at=datetime.now(timezone.utc),
                        metadata=memory_metadata
                    )
                    memories.append(memory)
                except Exception as e:
                    logger.warning("Failed to create ReMeMemory from parsed data: %s", e)

        context.failure_memories = memories
        logger.info("Extracted %s insights from failure trajectories", len(memories))


class ComparativeExtractionOp(BaseOp):
    """Extract insights by comparing successful and failed trajectories.

    This operation looks for patterns that differentiate success from failure,
    providing contrastive insights.
    """
    def __init__(self, use_extraction: bool = True):
        super().__init__(use_extraction=use_extraction)
        self.use_extraction = use_extraction

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute comparative extraction.

        Args:
            context: Runtime context with 'success_trajectories' and 'failure_trajectories'

        Sets:
            context.comparative_memories: List of TaskMemory objects from comparison
        """
        if not self.use_extraction:
            context.comparative_memories = []
            return

        all_trajectories = context.get("all_trajectories", [])
        user_id = context.get("user_id", "default")
        score = context.get("score", [])

        if len(all_trajectories) < 2:
            logger.info("Not enough trajectories for comparative extraction (need at least 2)")
            context.comparative_memories = []
            return

        if max(score) == min(score):
            logger.info("Best and worst trajectory score is the same, skipping comparative extraction")
            context.comparative_memories = []
            return

        # Get max and min scores
        max_score = max(score)
        min_score = min(score)
        max_score_index = score.index(max_score)
        min_score_index = score.index(min_score)
        high_trajectory = all_trajectories[max_score_index]
        low_trajectory = all_trajectories[min_score_index]

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        prompt_template = ReMePrompts.comparative_memory_prompt
        user_prompt = prompt_template.format(
            higher_score=max_score,
            lower_score=min_score,
            higher_steps=high_trajectory,
            lower_steps=low_trajectory
        )

        logger.debug(
            "Comparing trajectory with score %s and %s",
            max_score, min_score
        )

        response = await self.llm.async_generate(
            prompt=user_prompt
        )

        experiences = parse_json_experience_response(response)

        # Convert to ReMeMemory objects
        memories = []
        for exp_data in experiences:
            try:
                memory_metadata = ReMeMemoryMetadata(
                    tags=exp_data.get("tags", []),
                    step_type=exp_data.get("step_type", ""),
                    tools_used=exp_data.get("tools_used", []),
                    confidence=exp_data.get("confidence", 1.0),
                    freq=0,
                    utility=0
                )
                memory = ReMeMemory(
                    workspace_id=user_id,
                    when_to_use=exp_data.get("when_to_use", ""),
                    content=exp_data.get("experience", ""),
                    score=1.0,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    metadata=memory_metadata
                )
                memories.append(memory)
            except Exception as e:
                logger.warning("Failed to create ReMeMemory from parsed data: %s", e)

        context.comparative_memories = memories
        logger.info(
            "Extracted %s insights from comparative analysis",
            len(memories)
        )


class ComparativeAllExtractionOp(BaseOp):
    """Extract insights by comparing successful and failed trajectories.

    This operation looks for patterns that differentiate success from failure,
    providing contrastive insights.
    """
    def __init__(self, use_extraction: bool = True):
        super().__init__(use_extraction=use_extraction)
        self.use_extraction = use_extraction

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute comparative extraction.

        Args:
            context: Runtime context with 'success_trajectories' and 'failure_trajectories'

        Sets:
            context.comparative_memories: List of TaskMemory objects from comparison
        """
        if not self.use_extraction:
            context.comparative_memories = []
            return

        all_trajectories = context.get("all_trajectories", [])
        user_id = context.get("user_id", "default")

        if len(all_trajectories) < 2:
            logger.info("Not enough trajectories for comparative extraction (need at least 2)")
            context.comparative_memories = []
            return

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        prompt_template = ReMePrompts.comparative_all_memory_prompt
        trajectories_str = "\n\n".join([f"# Trajectory {i+1}\n{t}" for i, t in enumerate(all_trajectories)])
        user_prompt = prompt_template.format(
            trajectory=trajectories_str
        )
        response = await self.llm.async_generate(
            prompt=user_prompt
        )
        experiences = parse_json_experience_response(response)

        # Convert to ReMeMemory objects
        memories = []
        for exp_data in experiences:
            try:
                memory_metadata = ReMeMemoryMetadata(
                    tags=exp_data.get("tags", []),
                    step_type=exp_data.get("step_type", ""),
                    tools_used=exp_data.get("tools_used", []),
                    confidence=exp_data.get("confidence", 1.0),
                    freq=0,
                    utility=0
                )
                memory = ReMeMemory(
                    workspace_id=user_id,
                    when_to_use=exp_data.get("when_to_use", ""),
                    content=exp_data.get("experience", ""),
                    score=1.0,
                    created_at=datetime.now(timezone.utc),
                    updated_at=datetime.now(timezone.utc),
                    metadata=memory_metadata
                )
                memories.append(memory)
            except Exception as e:
                logger.warning("Failed to create ReMeMemory from parsed data: %s", e)

        context.comparative_memories = memories
        logger.info(
            "Extracted %s insights from comparative analysis",
            len(memories)
        )


class MemoryValidationOp(BaseOp):
    """Validate quality of extracted memories.

    This operation filters out low-quality memories based on:
    - Minimum content length
    - Presence of required fields
    - Specificity and actionability
    """

    def __init__(self, use_validation: bool = True):
        """Initialize validation operation."""
        super().__init__(use_validation=use_validation)
        self.use_validation = use_validation

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute memory validation.

        Args:
            context: Runtime context with memory lists

        Sets:
            context.validated_memories: Combined list of validated memories
        """
        # Collect all memories from different sources
        all_memories = []
        all_memories.extend(context.get("success_memories", []))
        all_memories.extend(context.get("failure_memories", []))
        all_memories.extend(context.get("comparative_memories", []))

        if not all_memories:
            logger.info("No memories to validate")
            context.validated_memories = []
            return
        if not self.use_validation:
            context.validated_memories = all_memories
            return

        logger.info("Validating %s memories", len(all_memories))

        # Validate each memory
        validated = []
        for memory in all_memories:
            validation_result = await self._validate_memory(memory)

            if validation_result and validation_result.get("is_valid", False):
                memory.score = validation_result.get("score", 0.0)
                validated.append(memory)
            else:
                reason = validation_result.get("reason", "Unknown reason") if validation_result else "Validation failed"
                logger.warning("Memory validation failed: %s", reason)

        logger.info("Validated %s out of %s memories", len(validated), len(all_memories))

        # Update context
        context.validated_memories = validated

    async def _validate_memory(self, memory: ReMeMemory) -> dict:
        """Validate a single memory using LLM.

        Args:
            memory: ReMeMemory to validate

        Returns:
            Dict with validation result containing is_valid, score, feedback, reason
        """
        try:
            if not self.llm:
                raise ValueError("LLM not configured in ServiceContext")

            prompt_template = ReMePrompts.memory_validation_prompt
            user_prompt = prompt_template.format(
                condition=memory.when_to_use,
                task_memory_content=memory.content,
            )

            # Call LLM for validation
            response_content = await self.llm.async_generate(
                prompt=user_prompt
            )

            # Parse validation result from JSON
            json_pattern = r"```json\s*([\s\S]*?)\s*```"
            json_blocks = re.findall(json_pattern, response_content)

            if json_blocks:
                parsed = json.loads(json_blocks[0])
            else:
                # Try parsing the entire response as JSON
                try:
                    parsed = json.loads(response_content)
                except json.JSONDecodeError:
                    parsed = {}

            is_valid = parsed.get("is_valid", True)
            score = parsed.get("score", 0.5)

            # Set validation threshold (default 0.5)
            validation_threshold = 0.5

            return {
                "is_valid": is_valid and score >= validation_threshold,
                "score": score,
                "feedback": response_content,
                "reason": (
                    ""
                    if (is_valid and score >= validation_threshold)
                    else f"Low validation score ({score:.2f}) or marked as invalid"
                ),
            }

        except Exception as e:
            logger.error("LLM validation failed for memory: %s", e)
            return {
                "is_valid": False,
                "score": 0.0,
                "feedback": "",
                "reason": f"LLM validation error: {str(e)}",
            }



class MemoryDeduplicationOp(BaseOp):
    """Remove duplicate memories based on embedding similarity.

    This operation:
    1. Generates embeddings for each memory
    2. Checks similarity against existing memories in vector store
    3. Checks similarity against other memories in current batch
    4. Keeps only unique memories based on similarity threshold
    """

    def __init__(self, use_deduplication: bool = True, similarity_threshold: float = 0.5):
        """Initialize deduplication operation.

        Args:
            similarity_threshold: Threshold for considering memories similar (0-1)
        """
        super().__init__(use_deduplication=use_deduplication, similarity_threshold=similarity_threshold)
        self.use_deduplication = use_deduplication
        self.similarity_threshold = similarity_threshold

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute memory deduplication.

        Args:
            context: Runtime context with 'validated_memories'

        Sets:
            context.deduplicated_memories: List of unique memories
            context.duplicate_count: Number of duplicates removed
        """
        memories = context.get("validated_memories", [])
        workspace_id = context.get("user_id", "default")

        if not memories:
            logger.info("No memories to deduplicate")
            context.deduplicated_memories = []
            context.duplicate_count = 0
            return

        if not self.use_deduplication:
            context.deduplicated_memories = memories
            context.duplicate_count = 0
            return

        logger.info("Starting deduplication for %s memories", len(memories))

        # Get existing memory embeddings from vector store
        existing_embeddings = await self._get_existing_memory_embeddings(workspace_id)

        unique_memories = []
        duplicate_count = 0

        for memory in memories:
            # Generate embedding for current memory
            current_embedding = await self._get_memory_embedding(memory)

            if current_embedding is None:
                logger.warning("Failed to generate embedding for memory: %s...", memory.when_to_use[:50])
                continue

            # Check similarity with existing memories in vector store
            if self._is_similar_to_existing(current_embedding, existing_embeddings):
                duplicate_count += 1
                logger.debug("Removed duplicate (similar to existing): %s...", memory.when_to_use[:30])
                continue

            # Check similarity with current batch memories
            if self._is_similar_to_current_batch(current_embedding, unique_memories):
                duplicate_count += 1
                logger.debug(f"Removed duplicate (similar in batch): {memory.when_to_use[:30]}...")
                continue

            # Add to unique memories
            unique_memories.append(memory)

        context.deduplicated_memories = unique_memories
        context.duplicate_count = duplicate_count

        logger.info(
            f"Deduplicated {len(memories)} memories to {len(unique_memories)} "
            f"(removed {duplicate_count} duplicates)"
        )

    async def _get_existing_memory_embeddings(self, workspace_id: str) -> List[List[float]]:
        """Get embeddings of existing memories from vector store.

        Args:
            workspace_id: Workspace identifier

        Returns:
            List of embedding vectors
        """
        try:
            if not self.vector_store or not workspace_id:
                return []

            # Query existing memory nodes from vector store
            # Use a dummy query to retrieve memories by workspace
            existing_nodes = await self.vector_store.async_search(
                embedding=[0.0] * 2560,  # Dummy embedding
                top_k=1000,  # Max existing memories to check
                metadata_filter={"workspace_id": workspace_id, "type": "reme_memory"}
            )

            # Extract embeddings
            existing_embeddings = []
            for node in existing_nodes:
                if hasattr(node, "embedding") and node.embedding:
                    existing_embeddings.append(node.embedding)

            logger.debug(f"Retrieved {len(existing_embeddings)} existing memory embeddings")
            return existing_embeddings

        except Exception as e:
            logger.warning(f"Failed to retrieve existing memory embeddings: {e}")
            return []

    async def _get_memory_embedding(self, memory: ReMeMemory) -> List[float]:
        """Generate embedding for a memory.

        Args:
            memory: ReMeMemory to embed

        Returns:
            Embedding vector or None if failed
        """
        try:
            if not self.embedding_model:
                logger.warning("No embedding model available")
                return None

            # Combine when_to_use and content for embedding
            text_for_embedding = f"{memory.when_to_use} {memory.content}"

            # Generate embedding
            embeddings = await self.embedding_model.async_embed_batch([text_for_embedding])

            if embeddings and len(embeddings) > 0:
                return embeddings[0]
            else:
                logger.warning("Empty embedding generated")
                return None

        except Exception as e:
            logger.error(f"Error generating embedding: {e}")
            return None

    def _is_similar_to_existing(
        self,
        current_embedding: List[float],
        existing_embeddings: List[List[float]]
    ) -> bool:
        """Check if current embedding is similar to existing embeddings.

        Args:
            current_embedding: Embedding to check
            existing_embeddings: List of existing embeddings

        Returns:
            True if similar to any existing embedding
        """
        for existing_embedding in existing_embeddings:
            similarity = calculate_cosine_similarity(current_embedding, existing_embedding)
            if similarity > self.similarity_threshold:
                logger.debug(f"Found similar existing memory (similarity: {similarity:.3f})")
                return True
        return False

    def _is_similar_to_current_batch(
        self,
        current_embedding: List[float],
        current_memories: List[ReMeMemory]
    ) -> bool:
        """Check if current embedding is similar to memories in current batch.

        Args:
            current_embedding: Embedding to check
            current_memories: List of memories already added to unique batch

        Returns:
            True if similar to any memory in current batch
        """

        # Cache embeddings with memories if not already cached
        if not hasattr(self, '_batch_embeddings_cache'):
            self._batch_embeddings_cache = {}

        for i, existing_memory in enumerate(current_memories):
            # Get or generate embedding for existing memory
            if i not in self._batch_embeddings_cache:
                # Generate embedding synchronously (note: in production, use async with cache)
                text_for_embedding = f"{existing_memory.when_to_use} {existing_memory.content}"
                # For now, we'll skip embedding generation to avoid async issues in sync method
                # This is a simplified version - in production, refactor to async or pre-generate
                continue

            existing_embedding = self._batch_embeddings_cache.get(i)
            if existing_embedding is None:
                continue

            similarity = calculate_cosine_similarity(current_embedding, existing_embedding)
            if similarity > self.similarity_threshold:
                logger.debug(f"Found similar memory in current batch (similarity: {similarity:.3f})")
                return True

        # Cache current embedding for next iteration
        self._batch_embeddings_cache[len(current_memories)] = current_embedding

        return False




class UpdateVectorStoreOp(BaseOp):
    """Persist deduplicated ReMe memories to vector store.

    This operation:
    1. Converts ReMeMemory objects to VectorNodes using to_vector_node()
    2. Generates embeddings for each memory
    3. Stores in vector store with metadata
    """

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute vector store update.

        Args:
            context: Runtime context with 'deduplicated_memories' and 'user_id'

        Sets:
            context.stored_count: Number of memories stored
            context.memory_ids: List of stored memory IDs
        """
        memories: List[ReMeMemory] = context.get("deduplicated_memories", [])
        user_id = context.get("user_id", "default")

        if not memories:
            logger.info("No memories to store")
            context.stored_count = 0
            context.memory_ids = []
            return

        if not self.embedding_model:
            raise ValueError("Embedding model not configured in ServiceContext")

        if not self.vector_store:
            raise ValueError("Vector store not configured in ServiceContext")

        logger.info(f"Storing {len(memories)} ReMe memories to vector store...")

        # Convert memories to vector nodes
        vector_nodes = []
        for memory in memories:
            # Ensure correct workspace is set
            memory.workspace_id = user_id
            # Convert using ReMeMemory's to_vector_node method
            vector_node = memory.to_vector_node()
            vector_nodes.append(vector_node)

        logger.debug(f"Generating embeddings for {len(vector_nodes)} memories...")

        # Get embeddings in batch
        contents = [node.content for node in vector_nodes]
        embeddings = await self.embedding_model.async_embed_batch(contents)

        # Store in vector store
        stored_ids = []
        for node, embedding in zip(vector_nodes, embeddings):
            node.embedding = embedding
            await self.vector_store.async_upsert(node)
            stored_ids.append(node.id)
            logger.debug(f"Stored ReMe memory: {node.id}")

        context.stored_count = len(stored_ids)
        context.memory_ids = stored_ids
        context.memories = memories


        logger.info(f"Successfully stored {len(stored_ids)} ReMe memories in vector store")


# Export all operations
__all__ = [
    "TrajectoryPreprocessOp",
    "SuccessExtractionOp",
    "FailureExtractionOp",
    "ComparativeExtractionOp",
    "MemoryValidationOp",
    "MemoryDeduplicationOp",
    "UpdateVectorStoreOp",
]
