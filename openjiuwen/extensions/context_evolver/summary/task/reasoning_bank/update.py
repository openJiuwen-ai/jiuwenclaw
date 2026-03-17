# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ReasoningBank memory summarization operations.

This module provides modular components for extracting and parsing
reasoning strategies from trajectories.
"""

import re
from typing import Any, Dict, List, Optional, Tuple
from openjiuwen.core.common.logging import context_engine_logger as logger

from ....core.op import BaseOp
from ....core.context import RuntimeContext
from ....schema import ReasoningBankMemoryItem, ReasoningBankMemory
from .prompt import ReasoningBankPrompts


def messages_to_text(messages: List[Dict[str, Any]]) -> str:
    """Convert a list of messages to a formatted text representation.

    Args:
        messages: List of message dictionaries with 'role' and 'content' keys

    Returns:
        Formatted string representation of messages

    Raises:
        ValueError: If an unknown message role is encountered
    """
    output_str = ""
    for message in messages:
        role = message["role"]
        if role == "system":
            output_str += "SYSTEM:\n" + message["content"] + "\n"
        elif role == "assistant":
            output_str += "ASSISTANT:\n" + message["content"] + "\n"
        elif role == "user":
            output_str += "USER:\n" + message["content"] + "\n"
        else:
            raise ValueError(f"Unknown message role {role} in: {message}")
    return output_str.strip()


class LabelDeterminator:
    """Determines success/failure labels for trajectories using LLM-as-judge."""

    @staticmethod
    async def determine_label(llm, query: str, trajectory: Any) -> bool:
        """Determine if a trajectory is successful or failed.

        Args:
            llm: LLM instance for generation
            query: User query
            trajectory: Trajectory data (can be messages list or string)

        Returns:
            True if successful, False if failed
        """
        # Convert trajectory to text if needed
        trajectory_text = (
            messages_to_text(trajectory)
            if isinstance(trajectory, list)
            else str(trajectory)
        )

        logger.info("Determining trajectory label using LLM-as-judge...")
        judge_response = await llm.async_generate(
            prompt=ReasoningBankPrompts.LLM_JUDGE_USER_PROMPT.format(
                query=query,
                trajectory=trajectory_text
            ),
            system_prompt=ReasoningBankPrompts.LLM_JUDGE_SYSTEM_PROMPT,
        )

        match = re.search(r"Status:\s*(success|failure)", judge_response, re.IGNORECASE)
        if match:
            is_success = match.group(1).lower() == "success"
        else:
            is_success = "success" in judge_response.lower()

        logger.info("Label determined: %s", 'success' if is_success else 'failure')
        return is_success


class MemoryItemParser:
    """Parses LLM markdown responses into structured memory items.

    This class handles all aspects of parsing LLM responses:
    - Cleaning markdown code fences
    - Splitting into memory item sections
    - Extracting individual fields (title, description, content)
    - Creating ReasoningBankMemory objects
    """

    @staticmethod
    def parse(llm_response: str, query: str, label: bool) -> List[ReasoningBankMemory]:
        """Parse LLM response into ReasoningBankMemory objects.

        Args:
            llm_response: Raw LLM response in markdown format
            query: Original user query
            label: True for success, False for failure

        Returns:
            List of parsed ReasoningBankMemory objects
        """
        # Clean and split response
        cleaned_response = MemoryItemParser._clean_response(llm_response)
        sections = MemoryItemParser._split_into_sections(cleaned_response)

        # Parse each section
        memories = []
        for section in sections:
            memory_item = MemoryItemParser._extract_memory_item(section)
            if memory_item:
                memories.append(memory_item)
                logger.debug("Extracted memory: %s", memory_item.title)
        memory = ReasoningBankMemory(
                    query=query,
                    memory=memories,
                    label=label
                )
        return [memory]

    @staticmethod
    def _clean_response(llm_response: str) -> str:
        """Remove markdown code fences from LLM response.

        Args:
            llm_response: Raw LLM response

        Returns:
            Cleaned response without code fences
        """
        llm_response = llm_response.strip()

        # Remove opening code fence
        if llm_response.startswith("```"):
            llm_response = llm_response.split("\n", 1)[1] if "\n" in llm_response else ""

        # Remove closing code fence
        if llm_response.endswith("```"):
            llm_response = llm_response.rsplit("```", 1)[0]

        return llm_response.strip()

    @staticmethod
    def _split_into_sections(llm_response: str) -> List[str]:
        """Split response into memory item sections.

        Args:
            llm_response: Cleaned LLM response

        Returns:
            List of section strings
        """
        sections = re.split(r'\n\s*#\s*Memory\s+Item\s+\d+', llm_response)
        return [section.strip() for section in sections if section.strip()]

    @staticmethod
    def _extract_field(lines: List[str], start_idx: int, field_pattern: str) -> Tuple[str, int]:
        """Extract a single field from lines starting at given index.

        Args:
            lines: List of text lines
            start_idx: Starting index
            field_pattern: Regex pattern for field header (e.g., r'^##\s*Title\s+')

        Returns:
            Tuple of (field_value, next_index)
        """
        line = lines[start_idx].strip()

        # Extract initial value from the field header line
        field_value = re.sub(field_pattern, '', line, flags=re.IGNORECASE).strip()

        # Continue reading lines until we hit another field or end
        idx = start_idx + 1
        while idx < len(lines):
            next_line = lines[idx].strip()

            # Skip empty lines and code fences
            if not next_line or next_line == "```":
                idx += 1
                continue

            # Stop if we hit another section header
            if next_line.startswith("##") or next_line.startswith("# Memory Item"):
                break

            # Append to field value
            field_value += " " + next_line
            idx += 1

        return field_value.strip(), idx

    @staticmethod
    def _extract_memory_item(section: str) -> Optional[ReasoningBankMemoryItem]:
        """Extract memory item fields from a markdown section.

        Args:
            section: Markdown section text

        Returns:
            ReasoningBankMemoryItem if all fields found, None otherwise
        """
        title = ""
        description = ""
        content = ""

        lines = section.split("\n")
        i = 0

        while i < len(lines):
            line = lines[i].strip()

            # Skip empty lines and code fences
            if not line or line == "```":
                i += 1
                continue

            # Extract title
            if re.match(r'^##\s*Title\s+', line, re.IGNORECASE):
                title, i = MemoryItemParser._extract_field(lines, i, r'^##\s*Title\s+')
                continue

            # Extract description
            elif re.match(r'^##\s*Description\s+', line, re.IGNORECASE):
                description, i = MemoryItemParser._extract_field(lines, i, r'^##\s*Description\s+')
                continue

            # Extract content
            elif re.match(r'^##\s*Content\s+', line, re.IGNORECASE):
                content, i = MemoryItemParser._extract_field(lines, i, r'^##\s*Content\s+')
                continue

            i += 1

        # Create memory item only if all fields are present
        if title and description and content:
            return ReasoningBankMemoryItem(
                title=title,
                description=description,
                content=content,
            )

        logger.warning("Incomplete memory item extracted (missing fields)")
        return None


class SummarizeMemoryOp(BaseOp):
    """Summarize trajectories into ReasoningBank memories.

    This operation:
    1. Validates input (query and trajectories)
    2. Determines success/failure label if not provided
    3. Selects appropriate prompts based on label
    4. Calls LLM to extract memory strategies
    5. Parses LLM response into structured memories
    """

    def __init__(self):
        """Initialize the summarization operation."""
        super().__init__()

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute trajectory summarization.

        Args:
            context: Runtime context with 'query', 'trajectories', optionally 'label' and 'user_id'

        Sets:
            context.memories: List of extracted ReasoningBankMemory objects
            context.label: Determined label (if not provided)
        """
        # Validate inputs
        matts = self._get_matts(context)
        if matts not in ["none", "sequential"]:
            return

        query = self._get_query(context)
        if not query:
            return

        trajectories = self._get_trajectories(context)
        if not trajectories:
            return

        user_id = context.get("user_id", "default")

        # Determine label (success/failure)
        label = await self._determine_label(context, query, trajectories[0])

        # Select prompts based on label
        system_prompt = self._select_system_prompt(label)

        # Generate extraction prompt
        user_prompt = ReasoningBankPrompts.EXTRACT_TRAJ_USER_PROMPT.format(
            query=query,
            trajectory=trajectories[0]
        )

        # Call LLM to extract memories
        logger.info("Extracting memories for %s trajectory...", 'successful' if label else 'failed')
        llm_response = await self.llm.async_generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
        )

        # Parse response into structured memories
        memories = MemoryItemParser.parse(llm_response, query, label)

        context.memories = memories
        logger.info("Extracted %s memories from trajectory.", len(memories[0].memory))

    def _get_matts(self, context: RuntimeContext) -> Optional[str]:
        """Get and validate matts from context."""
        matts = context.get("matts", "")
        if not matts:
            logger.warning("No matts found. Use memory summarization without scaling.")
            return "none"
        return matts

    def _get_query(self, context: RuntimeContext) -> Optional[str]:
        """Get and validate query from context."""
        query = context.get("query", "")
        if not query:
            logger.warning("No query found. Skipping trajectory summarization.")
            return None
        return query

    def _get_trajectories(self, context: RuntimeContext) -> Optional[List[Any]]:
        """Get and validate trajectories from context."""
        trajectories = context.get("trajectories", [])
        if not trajectories:
            logger.warning("No trajectories found. Skipping trajectory summarization.")
            return None
        return trajectories

    async def _determine_label(
        self,
        context: RuntimeContext,
        query: str,
        trajectory: Any
    ) -> bool:
        """Determine or retrieve the success/failure label.

        Args:
            context: Runtime context
            query: User query
            trajectory: Trajectory data

        Returns:
            True if successful, False if failed
        """
        label = context.get("label", [])

        if not label:
            is_success = await LabelDeterminator.determine_label(
                self.llm, query, trajectory
            )
            context.label = [is_success]
            return is_success
        else:
            return label[0]

    def _select_system_prompt(self, label: bool) -> str:
        """Select appropriate system prompt based on label.

        Args:
            label: True for success, False for failure

        Returns:
            System prompt string
        """
        if label:
            return ReasoningBankPrompts.EXTRACT_SUCCESS_TRAJ_SYSTEM_PROMPT
        else:
            return ReasoningBankPrompts.EXTRACT_FAIL_TRAJ_SYSTEM_PROMPT


class SummarizeMemoryParallelOp(BaseOp):
    def __init__(self):
        """Initialize the summarization operation."""
        super().__init__()

    async def async_execute(self, context: RuntimeContext) -> None:
        # Validate inputs
        matts = self._get_matts(context)
        if matts not in ["parallel", "combined"]:
            return

        query = self._get_query(context)
        if not query:
            return

        trajectories = self._get_trajectories(context)
        if not trajectories:
            return

        if len(trajectories) < 2:
            logger.warning("Not enough trajectories for parallel summarization.")
            return

        user_id = context.get("user_id", "default")

        # Select prompts based on label
        system_prompt = ReasoningBankPrompts.PARALLEL_SCALING_SYSTEM_PROMPT

        # Generate extraction prompt
        trajectory_str = ''
        for i, trajectory in enumerate(trajectories):
            trajectory_str += f"<Trajectory {i+1}>\n{trajectory}\n\n"
        user_prompt = ReasoningBankPrompts.PARALLEL_SCALING_USER_PROMPT.format(
            query=query,
            trajectories=trajectory_str
        )

        # Call LLM to extract memories
        logger.info("Extracting parallel memories from %s trajectories...", len(trajectories))
        llm_response = await self.llm.async_generate(
            prompt=user_prompt,
            system_prompt=system_prompt,
        )

        # Parse response into structured memories
        memories = MemoryItemParser.parse(llm_response, query, None)

        context.memories = memories
        logger.info("Extracted %s memories from trajectory.", len(memories[0].memory))

    def _get_matts(self, context: RuntimeContext) -> Optional[str]:
        """Get and validate matts from context."""
        matts = context.get("matts", "")
        if not matts:
            logger.warning("No matts found. Use memory summarization without scaling.")
            return "none"
        return matts

    def _get_query(self, context: RuntimeContext) -> Optional[str]:
        """Get and validate query from context."""
        query = context.get("query", "")
        if not query:
            logger.warning("No query found. Skipping trajectory summarization.")
            return None
        return query

    def _get_trajectories(self, context: RuntimeContext) -> Optional[List[Any]]:
        """Get and validate trajectories from context."""
        trajectories = context.get("trajectories", [])
        if not trajectories:
            logger.warning("No trajectories found. Skipping trajectory summarization.")
            return None
        return trajectories


class UpdateVectorStoreOp(BaseOp):
    """Persist deduplicated memories to vector store.

    This operation:
    1. Converts TaskMemory objects to VectorNodes
    2. Generates embeddings for each memory
    3. Stores in vector store
    """

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute vector store update.

        Args:
            context: Runtime context with 'deduplicated_memories' and 'user_id'

        Sets:
            context.stored_count: Number of memories stored
            context.memory_ids: List of stored memory IDs
        """
        memories = context.get("memories", [])
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

        # Convert memories to vector nodes
        vector_nodes = []
        for memory in memories:
            memory.workspace_id = user_id  # Ensure correct workspace
            vector_node = memory.to_vector_node()
            vector_nodes.append(vector_node)

        logger.debug("Generating embeddings for %s memories...", len(vector_nodes))

        # Get embeddings in batch
        contents = [node.content for node in vector_nodes]
        embeddings = await self.embedding_model.async_embed_batch(contents)

        # Store in vector store
        stored_ids = []
        for node, embedding in zip(vector_nodes, embeddings):
            node.embedding = embedding
            await self.vector_store.async_upsert(node)
            stored_ids.append(node.id)
            logger.debug("Stored memory: %s", node.id)

        context.stored_count = len(stored_ids)
        context.memory_ids = stored_ids

        logger.info("Stored %s memories in vector store", len(stored_ids))


# Export all operations
__all__ = [
    "SummarizeMemoryOp",
    "SummarizeMemoryParallelOp",
    "UpdateVectorStoreOp",
]
