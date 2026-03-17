# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""ACE algorithm summary operations.

This module implements the ACE (Agentic Context Engineering) summary workflow:
- ReflectOp: Generate reflection from trajectories
- CurateOp: Generate playbook operations from reflection
- ApplyDeltaOp: Apply operations to playbook and persist to vector store
- ParallelReflectOp: Generate reflection from multiple trajectories (MaTTS)
- ParallelCurateOp: Generate playbook operations from parallel reflection
"""
import json
from openjiuwen.core.common.logging import context_engine_logger as logger

from ....core.op import BaseOp
from ....core.context import RuntimeContext
from ....schema import ACEMemory

from .utils import _safe_json_loads
from .playbook import Playbook, DeltaBatch, Bullet
from .prompt import ACEPrompts


class LoadPlaybookOp(BaseOp):
    """Load existing playbook from vector store."""

    async def async_execute(self, context: RuntimeContext) -> None:
        """Load playbook from vector store.

        Args:
            context: Runtime context with 'user_id'

        Sets:
            context.playbook: Loaded Playbook instance
        """
        user_id = context.get("user_id", "default")

        if not self.vector_store:
            raise ValueError("Vector store not configured in ServiceContext")

        # Load all ACE memories for this user
        try:
            # Search with a dummy embedding (we're just filtering by metadata)
            all_nodes = await self.vector_store.async_search(
                embedding=[0.0] * 2560,  # Dummy embedding
                top_k=50,  # Get all bullets
                metadata_filter={"workspace_id": user_id, "type": "ace_memory"}
            )

            # Convert nodes to playbook
            playbook = Playbook()
            for node in all_nodes:
                metadata = node.metadata
                bullet = Bullet(
                    id=metadata["id"],
                    section=metadata["section"],
                    content=metadata["content"],
                    helpful=metadata.get("helpful", 0),
                    harmful=metadata.get("harmful", 0),
                    neutral=metadata.get("neutral", 0),
                    created_at=metadata.get("created_at", ""),
                    updated_at=metadata.get("updated_at", "")
                )
                playbook.load_bullet(bullet)

            # Extract highest ID number from all bullets to avoid collisions
            # IDs are in format "section-00123" where _next_id is the integer 123
            max_id = 0
            for bullet_id in playbook.bullet_ids():
                # Extract number from ID format like "strategies-00123"
                try:
                    id_parts = bullet_id.rsplit('-', 1)
                    if len(id_parts) == 2:
                        id_num = int(id_parts[1])
                        max_id = max(max_id, id_num)
                except (ValueError, IndexError):
                    continue

            playbook.set_next_id(max_id)
            context.playbook = playbook
            logger.info("Loaded playbook with %s bullets, next_id=%s", len(playbook.bullets()), max_id)


        except Exception as e:
            logger.warning("Failed to load playbook: %s. Starting with empty playbook.", e)
            context.playbook = Playbook()


class ReflectOp(BaseOp):
    """Generate reflection from single trajectory using ACE reflector prompts.

    This operation analyzes a trajectory and generates structured reflection
    with error identification, root cause analysis, and key insights.
    """

    def __init__(self, use_ground_truth: bool = False):
        """Initialize reflection operation.

        Args:
            use_ground_truth: Whether to use ground truth in reflection
        """
        super().__init__(use_ground_truth=use_ground_truth)
        self.use_ground_truth = use_ground_truth

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute reflection generation.

        Args:
            context: Runtime context with 'query', 'trajectories', 'playbook'

        Sets:
            context.reflection: Generated reflection as dict
        """
        matts = context.get("matts", "none")
        if matts not in ["none", "sequential"]:
            logger.info("Skipping ReflectOp for matts mode: %s", matts)
            return

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        query = context.query
        trajectories = context.get("trajectories", [])
        playbook = context.get("playbook", Playbook())
        ground_truth = context.get("ground_truth", "")
        feedback = context.get("feedback", [])

        if not trajectories:
            logger.warning("No trajectories to reflect on")
            context.reflection = {}
            return

        # Format trajectory as string
        trajectory_str = trajectories[0] if isinstance(trajectories, list) else trajectories

        # Select prompt based on ground truth availability
        if self.use_ground_truth and ground_truth and feedback:
            prompt_template = ACEPrompts.ACE_REFLECTOR_PROMPT
            user_prompt = prompt_template.format(
                ground_truth=ground_truth,
                feedback=feedback[0],
                playbook=playbook.as_prompt(),
                trajectory=trajectory_str
            )
        else:
            prompt_template = ACEPrompts.ACE_REFLECTOR_NOGT_PROMPT
            user_prompt = prompt_template.format(
                playbook=playbook.as_prompt(),
                trajectory=trajectory_str
            )

        logger.debug("Generating reflection from trajectory...")

        # Call LLM
        response = await self.llm.async_generate(prompt=user_prompt)

        # Parse JSON response
        try:
            reflection = _safe_json_loads(response)
            context.reflection = reflection
            logger.info("Generated reflection successfully")
        except Exception as e:
            logger.error("Failed to parse reflection: %s", e)
            context.reflection = {}


class ParallelReflectOp(BaseOp):
    """Generate reflection from multiple trajectories (MaTTS parallel mode).

    This operation compares multiple trajectories to identify patterns and
    generate comprehensive reflections.
    """

    def __init__(self, use_ground_truth: bool = False):
        """Initialize parallel reflection operation.

        Args:
            use_ground_truth: Whether to use ground truth in reflection
        """
        super().__init__(use_ground_truth=use_ground_truth)
        self.use_ground_truth = use_ground_truth

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute parallel reflection generation.

        Args:
            context: Runtime context with 'query', 'trajectories', 'playbook'

        Sets:
            context.reflection: Generated reflection as dict
        """
        # Check matts mode - only execute for parallel
        matts = context.get("matts", "none")
        if matts not in ["parallel", "combined"]:
            logger.info("Skipping ParallelReflectOp for matts mode: %s", matts)
            return

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        query = context.query
        trajectories = context.get("trajectories", [])
        playbook = context.get("playbook", Playbook())
        ground_truth = context.get("ground_truth", "")
        feedbacks = context.get("feedbacks", [])

        if len(trajectories) < 2:
            logger.warning("Expected at least 2 trajectories for parallel mode, got %s", len(trajectories))
            context.reflection = {}
            return

        # Format trajectories dynamically
        trajectory_parts = []
        for i, traj in enumerate(trajectories, 1):
            trajectory_parts.append(f"<TRAJECTORY {i}>")
            if len(feedbacks) >= i and feedbacks[i - 1]:
                trajectory_parts.append("TEST_REPORT_START")
                trajectory_parts.append(feedbacks[i - 1])
                trajectory_parts.append("TEST_REPORT_END")
            trajectory_parts.append(traj)
            trajectory_parts.append("")  # Empty line between trajectories

        trajectories_str = "\n".join(trajectory_parts)

        # Select prompt based on ground truth availability
        if self.use_ground_truth and ground_truth:
            prompt_template = ACEPrompts.ACE_REFLECTOR_SCALING_PROMPT
            user_prompt = prompt_template.format(
                ground_truth=ground_truth,
                playbook=playbook.as_prompt(),
                trajectories=trajectories_str
            )
        else:
            prompt_template = ACEPrompts.ACE_REFLECTOR_SCALING_NOGT_PROMPT
            user_prompt = prompt_template.format(
                playbook=playbook.as_prompt(),
                trajectories=trajectories_str
            )

        logger.debug("Generating parallel reflection from %s trajectories...", len(trajectories))

        # Call LLM
        response = await self.llm.async_generate(prompt=user_prompt)

        # Parse JSON response
        try:
            reflection = _safe_json_loads(response)
            context.reflection = reflection
            logger.info("Generated parallel reflection successfully")
        except Exception as e:
            logger.error("Failed to parse reflection: %s", e)
            context.reflection = {}


class CurateOp(BaseOp):
    """Generate playbook operations from reflection using ACE curator prompts.

    This operation takes a reflection and generates ADD/UPDATE/TAG/REMOVE
    operations to update the playbook.
    """

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute curation.

        Args:
            context: Runtime context with 'reflection', 'playbook', 'query', 'trajectories'

        Sets:
            context.delta: Generated DeltaBatch
        """
        # Check matts mode - only execute for none or sequential
        matts = context.get("matts", "none")
        if matts not in ["none", "sequential"]:
            logger.info("Skipping CurateOp for matts mode: %s", matts)
            return

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        reflection = context.get("reflection", {})
        playbook = context.get("playbook", Playbook())
        query = context.query
        trajectories = context.get("trajectories", [])

        if not reflection:
            logger.warning("No reflection to curate from")
            context.delta = DeltaBatch(reasoning="", operations=[])
            return

        # Format trajectory
        trajectory_str = trajectories[0] if isinstance(trajectories, list) else trajectories

        # Build curator prompt
        user_prompt = ACEPrompts.ACE_CURATOR_PROMPT.format(
            question_context=query,
            playbook=playbook.as_prompt(),
            trajectory=trajectory_str,
            reflection=json.dumps(reflection, ensure_ascii=False)
        )

        logger.debug("Generating playbook operations from reflection...")

        # Call LLM
        response = await self.llm.async_generate(prompt=user_prompt)

        # Parse JSON response
        try:
            curation_dict = _safe_json_loads(response)
            delta = DeltaBatch.from_json(curation_dict)
            context.delta = delta
            logger.info("Generated %s playbook operations", len(delta.operations))
        except Exception as e:
            logger.error("Failed to parse curation: %s", e)
            context.delta = DeltaBatch(reasoning="", operations=[])


class ParallelCurateOp(BaseOp):
    """Generate playbook operations from parallel reflection (MaTTS parallel mode).

    This operation uses the parallel curator prompt with multiple trajectories.
    """

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute parallel curation.

        Args:
            context: Runtime context with 'reflection', 'playbook', 'query', 'trajectories'

        Sets:
            context.delta: Generated DeltaBatch
        """
        # Check matts mode - only execute for parallel
        matts = context.get("matts", "none")
        if matts not in ["parallel", "combined"]:
            logger.info("Skipping ParallelCurateOp for matts mode: %s", matts)
            return

        if not self.llm:
            raise ValueError("LLM not configured in ServiceContext")

        reflection = context.get("reflection", {})
        playbook = context.get("playbook", Playbook())
        query = context.query
        trajectories = context.get("trajectories", [])

        if not reflection:
            logger.warning("No reflection to curate from")
            context.delta = DeltaBatch(reasoning="", operations=[])
            return

        if len(trajectories) < 2:
            logger.warning("Expected at least 2 trajectories for parallel mode, got %s", len(trajectories))
            context.delta = DeltaBatch(reasoning="", operations=[])
            return

        # Format trajectories dynamically
        trajectory_parts = []
        for i, traj in enumerate(trajectories, 1):
            trajectory_parts.append(f"<TRAJECTORY {i}>")
            trajectory_parts.append(traj)
            trajectory_parts.append("")  # Empty line between trajectories

        trajectories_str = "\n".join(trajectory_parts)

        # Build parallel curator prompt
        user_prompt = ACEPrompts.ACE_CURATOR_SCALING_PROMPT.format(
            question_context=query,
            playbook=playbook.as_prompt(),
            trajectories=trajectories_str,
            reflection=json.dumps(reflection, ensure_ascii=False)
        )

        logger.debug("Generating playbook operations from parallel reflection...")

        # Call LLM
        response = await self.llm.async_generate(prompt=user_prompt)

        # Parse JSON response
        try:
            curation_dict = _safe_json_loads(response)
            delta = DeltaBatch.from_json(curation_dict)
            context.delta = delta
            logger.info("Generated %s playbook operations (parallel)", len(delta.operations))
        except Exception as e:
            logger.error("Failed to parse curation: %s", e)
            context.delta = DeltaBatch(reasoning="", operations=[])


class ApplyDeltaOp(BaseOp):
    """Apply playbook operations and persist to vector store.

    This operation:
    1. Applies delta operations to playbook
    2. Manages playbook size (max 50 bullets)
    3. Converts bullets to ACEMemory objects
    4. Stores in vector store
    """

    def __init__(self, max_bullets: int = 50):
        """Initialize apply delta operation.

        Args:
            max_bullets: Maximum number of bullets in playbook
        """
        super().__init__(max_bullets=max_bullets)
        self.max_bullets = max_bullets

    async def async_execute(self, context: RuntimeContext) -> None:
        """Execute delta application.

        Args:
            context: Runtime context with 'delta', 'playbook', 'user_id'

        Sets:
            context.memories: List of ACEMemory objects created
        """
        delta = context.get("delta")
        playbook = context.get("playbook", Playbook())
        user_id = context.get("user_id", "default")

        if not delta or not delta.operations:
            logger.info("No delta operations to apply")
            context.memories = []
            return

        if not self.vector_store:
            raise ValueError("Vector store not configured in ServiceContext")

        if not self.embedding_model:
            raise ValueError("Embedding model not configured in ServiceContext")

        # Count ADD operations
        add_count = sum(1 for op in delta.operations if op.type.upper() == "ADD")

        # Calculate how many bullets to remove
        current_count = playbook.stats()["bullets"]
        remove_count = max(0, add_count + current_count - self.max_bullets)

        # Track affected bullets during delta application
        affected_bullet_ids = set()
        removed_bullet_ids = set()

        # Track removals due to size limit
        if remove_count > 0:
            bullets_list = playbook.bullets()
            sorted_bullets = sorted(
                bullets_list,
                key=lambda b: (b.helpful - b.harmful, b.updated_at),
                reverse=False  # Lowest scores first
            )
            for bullet in sorted_bullets[:remove_count]:
                removed_bullet_ids.add(bullet.id)
                playbook.remove_bullet(bullet.id)
                logger.info("Removed low-scoring bullet: %s", bullet.id)

        # Apply delta operations and track changes
        for operation in delta.operations:
            op_type = operation.type.upper()

            if op_type == "ADD":
                # Track the count before adding
                pre_add_count = len(playbook.bullets())
                # Add new bullet
                bullet = playbook.add_bullet(
                    section=operation.section,
                    content=operation.content or "",
                    bullet_id=operation.bullet_id,
                    metadata=operation.metadata
                )
                # Track the newly added bullet
                if bullet:
                    affected_bullet_ids.add(bullet.id)

            elif op_type == "UPDATE":
                # Existing bullet modified
                if operation.bullet_id:
                    updated_bullet = playbook.update_bullet(
                        operation.bullet_id,
                        content=operation.content,
                        metadata=operation.metadata
                    )
                    # Only track if bullet actually exists
                    if updated_bullet:
                        affected_bullet_ids.add(operation.bullet_id)
                    elif operation.content:
                        # Bullet doesn't exist but we have content - convert to ADD
                        logger.info(
                            "UPDATE operation converted to ADD: "
                            "bullet %s not found, creating new bullet",
                            operation.bullet_id
                        )
                        # Try to extract section from bullet_id if operation.section is empty
                        section = operation.section
                        if not section and operation.bullet_id:
                            # bullet_id format is typically "section-0001", extract section part
                            parts = operation.bullet_id.rsplit('-', 1)
                            if len(parts) > 1:
                                section = parts[0].replace('_', ' ')
                        bullet = playbook.add_bullet(
                            section=section or "general",
                            content=operation.content,
                            metadata=operation.metadata
                        )
                        if bullet:
                            affected_bullet_ids.add(bullet.id)
                    else:
                        logger.warning(
                            "UPDATE operation failed: "
                            "bullet %s not found and no content provided",
                            operation.bullet_id
                        )

            elif op_type == "TAG":
                # Existing bullet tagged
                if operation.bullet_id:
                    # Check if bullet exists before tagging
                    if playbook.get_bullet(operation.bullet_id):
                        affected_bullet_ids.add(operation.bullet_id)
                        for tag, increment in operation.metadata.items():
                            playbook.tag_bullet(operation.bullet_id, tag, increment)
                    else:
                        logger.warning("TAG operation failed: bullet %s not found", operation.bullet_id)

            elif op_type == "REMOVE":
                # Bullet removed
                if operation.bullet_id:
                    removed_bullet_ids.add(operation.bullet_id)
                    playbook.remove_bullet(operation.bullet_id)


        # Delete removed bullets from vector store
        for bullet_id in removed_bullet_ids:
            node_id = f"ace_{user_id}_{bullet_id}"
            try:
                await self.vector_store.async_delete(node_id)
                logger.debug("Deleted bullet %s from vector store", bullet_id)
            except Exception as e:
                logger.warning("Failed to delete bullet %s: %s", bullet_id, e)

        # Update/insert affected bullets only
        memories = []
        for bullet_id in affected_bullet_ids:
            bullet = playbook.get_bullet(bullet_id)
            if bullet:
                # Create ACEMemory from Bullet
                ace_memory = ACEMemory(
                    id=bullet.id,
                    section=bullet.section,
                    content=bullet.content,
                    helpful=bullet.helpful,
                    harmful=bullet.harmful,
                    neutral=bullet.neutral,
                    created_at=bullet.created_at,
                    updated_at=bullet.updated_at,
                    workspace_id=user_id
                )

                # Convert to vector node and upsert
                vector_node = ace_memory.to_vector_node()
                embedding = await self.embedding_model.async_embed(ace_memory.content)
                vector_node.embedding = embedding
                await self.vector_store.async_upsert(vector_node)

                memories.append(ace_memory)

        context.memories = memories

        logger.info(
            f"Applied {len(delta.operations)} operations: "
            f"{len(affected_bullet_ids)} bullets updated, "
            f"{len(removed_bullet_ids)} bullets removed"
        )



__all__ = [
    "LoadPlaybookOp",
    "ReflectOp",
    "ParallelReflectOp",
    "CurateOp",
    "ParallelCurateOp",
    "ApplyDeltaOp",
]
