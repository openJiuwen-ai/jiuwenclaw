# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Workflow Task Executor

Executes workflow tasks within the new Controller framework
(EventHandler + TaskExecutor + TaskScheduler).

Responsibilities:
- Run workflow (new / resume) via Runner.run_workflow_streaming
- Collect streaming chunks and manage session writes
- Save / clear interruption state in session.state
- Yield ControllerOutputChunk to TaskScheduler
"""

import json
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, List, Optional, Tuple, Union

from openjiuwen.core.common.constants.constant import INTERACTION
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.utils.message_utils import MessageUtils
from openjiuwen.core.controller.schema.controller_output import (
    ControllerOutputChunk,
    ControllerOutputPayload,
)
from openjiuwen.core.controller.schema.dataframe import JsonDataFrame
from openjiuwen.core.controller.schema.event import EventType
from openjiuwen.core.controller.modules.task_manager import TaskFilter
from openjiuwen.core.controller.modules.task_scheduler import TaskExecutor
from openjiuwen.core.foundation.llm import AssistantMessage
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import CustomSchema, OutputSchema
from openjiuwen.core.workflow import WorkflowOutput, WorkflowExecutionState


@dataclass
class _StreamCollectResult:
    """Internal result of workflow stream collection."""

    chunks: list = field(default_factory=list)
    has_interaction: bool = False
    final_result: Any = None


class WorkflowTaskExecutor(TaskExecutor):
    """Execute workflow tasks (new or resume).

    Reads task.extensions written by WorkflowEventHandler:
        - workflow_id: str
        - resume_mode: "new" | "resume"
        - interactive_input: InteractiveInput | None
        - filtered_inputs: dict | None

    Writes back to task.extensions after execution:
        - component_id: str | list[str] | None
    """

    # ------------------------------------------------------------------
    # Public interface required by TaskExecutor ABC
    # ------------------------------------------------------------------

    async def execute_ability(
        self, task_id: str, session: Session
    ) -> AsyncIterator[ControllerOutputChunk]:
        """Dispatch to new or resume execution path.

        Args:
            task_id: Task ID managed by TaskManager.
            session: Session object.

        Yields:
            ControllerOutputChunk consumed by TaskScheduler.
        """
        tasks = await self._task_manager.get_task(TaskFilter(task_id=task_id))
        if not tasks:
            logger.error("WorkflowTaskExecutor: task not found: %s", task_id)
            return
        task = tasks[0]
        ext = task.extensions or {}
        resume_mode = ext.get("resume_mode", "new")
        workflow_id = ext.get("workflow_id", "")

        if resume_mode == "resume":
            inputs = ext.get("interactive_input")
        else:
            inputs = ext.get("filtered_inputs") or {}

        async for chunk in self._run_workflow_and_collect(task, session, workflow_id, inputs):
            yield chunk

    async def can_pause(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Workflow tasks do not support pause."""
        return False, "Workflow tasks do not support pause"

    async def pause(self, task_id: str, session: Session) -> bool:
        """Not supported."""
        logger.warning("WorkflowTaskExecutor does not support pause")
        return False

    async def can_cancel(self, task_id: str, session: Session) -> Tuple[bool, str]:
        """Workflow tasks support cancellation."""
        return True, ""

    async def cancel(self, task_id: str, session: Session) -> bool:
        """Cancel by removing task from TaskManager.

        asyncio.Task cancellation is handled by TaskScheduler.cancel_task(), not here.
        """
        await self._task_manager.remove_task(TaskFilter(task_id=task_id))
        return True

    # ------------------------------------------------------------------
    # Workflow streaming core
    # ------------------------------------------------------------------

    async def _run_workflow_and_collect(
        self, task, session: Session, workflow_id: str, inputs
    ) -> AsyncIterator[ControllerOutputChunk]:
        """Run workflow via Runner and dispatch result.

        Args:
            task: New-architecture Task.
            session: Session object.
            workflow_id: Workflow identifier.
            inputs: Workflow inputs (dict or InteractiveInput).

        Yields:
            ControllerOutputChunk
        """
        workflow = await self._find_workflow(workflow_id, session)
        if not workflow:
            return

        workflow_session = session.create_workflow_session()
        workflow_stream = Runner.run_workflow_streaming(
            workflow,
            inputs=inputs,
            session=workflow_session,
            context=await self._context_engine.create_context(
                context_id=workflow_id, session=session
            ),
        )

        collected = await self._collect_stream_chunks(workflow_stream, session)

        if collected.has_interaction:
            async for chunk in self._handle_interruption(task, session, workflow_id, collected):
                yield chunk
        else:
            async for chunk in self._handle_completion(task, session, workflow_id, collected):
                yield chunk

    async def _collect_stream_chunks(
        self, workflow_stream, session: Session
    ) -> _StreamCollectResult:
        """Consume workflow stream, write to session.

        __interaction__ chunks are held back (not written) for post-processing.
        All other OutputSchema and CustomSchema are written through immediately.

        Args:
            workflow_stream: Async iterator from Runner.
            session: Session object.

        Returns:
            _StreamCollectResult with collected state.
        """
        result = _StreamCollectResult()

        async for chunk in workflow_stream:
            if isinstance(chunk, OutputSchema):
                if chunk.type == INTERACTION:
                    result.has_interaction = True
                elif chunk.type == "workflow_final":
                    result.final_result = chunk.payload
                    await session.write_stream(chunk)
                else:
                    await session.write_stream(chunk)
            elif isinstance(chunk, CustomSchema):
                await session.write_custom_stream(chunk)
            else:
                await session.write_stream(chunk)
            result.chunks.append(chunk)

        return result

    # ------------------------------------------------------------------
    # Result handlers
    # ------------------------------------------------------------------

    async def _handle_interruption(
        self, task, session: Session, workflow_id: str, collected: _StreamCollectResult
    ) -> AsyncIterator[ControllerOutputChunk]:
        """Handle workflow interruption (INPUT_REQUIRED).

        Saves interrupt state, writes first interaction chunk to session,
        adds AI message from interaction value, and yields TASK_INTERACTION event.

        Args:
            task: Task object.
            session: Session object.
            workflow_id: Workflow identifier.
            collected: Stream collection result.

        Yields:
            ControllerOutputChunk (TASK_INTERACTION)
        """
        await self._save_interrupt_state(task, session, collected.chunks)

        workflow_output = None
        first_interaction = self._find_first_interaction(collected.chunks)
        if first_interaction:
            await session.write_stream(first_interaction)
            workflow_output = WorkflowOutput(
                result=first_interaction,
                state=WorkflowExecutionState.INPUT_REQUIRED,
            )

        interaction_value = self._extract_interaction_value(collected.chunks)
        if interaction_value is not None:
            await MessageUtils.add_ai_message(
                AssistantMessage(content=str(interaction_value)),
                self._context_engine,
                session,
            )

        yield self._build_output_chunk(
            EventType.TASK_INTERACTION, workflow_id, task.task_id, workflow_output
        )

    async def _handle_completion(
        self, task, session: Session, workflow_id: str, collected: _StreamCollectResult
    ) -> AsyncIterator[ControllerOutputChunk]:
        """Handle workflow completion.

        Clears interrupt state, adds AI message from final result,
        and yields TASK_COMPLETION event.

        Args:
            task: Task object.
            session: Session object.
            workflow_id: Workflow identifier.
            collected: Stream collection result.

        Yields:
            ControllerOutputChunk (TASK_COMPLETION)
        """
        self._clear_interrupt_state(task, session)

        workflow_output = None
        if collected.final_result is not None:
            content = (
                json.dumps(collected.final_result, ensure_ascii=False)
                if isinstance(collected.final_result, (dict, list))
                else str(collected.final_result)
            )
            await MessageUtils.add_ai_message(
                AssistantMessage(content=content), self._context_engine, session
            )
            workflow_output = WorkflowOutput(
                result=collected.final_result,
                state=WorkflowExecutionState.COMPLETED,
            )

        yield self._build_output_chunk(
            EventType.TASK_COMPLETION, workflow_id, task.task_id, workflow_output
        )

    @staticmethod
    def _build_output_chunk(
        event_type: EventType, workflow_id: str, task_id: str, result: Any
    ) -> ControllerOutputChunk:
        """Build a ControllerOutputChunk for TASK_INTERACTION or TASK_COMPLETION.

        Args:
            event_type: EventType.TASK_INTERACTION or TASK_COMPLETION.
            workflow_id: Workflow identifier.
            task_id: Task identifier.
            result: WorkflowOutput or None.

        Returns:
            ControllerOutputChunk
        """
        return ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=event_type,
                data=[JsonDataFrame(data={
                    "workflow_id": workflow_id,
                    "task_id": task_id,
                    "result": result,
                })],
            ),
        )

    # ------------------------------------------------------------------
    # Interruption state management
    # ------------------------------------------------------------------

    async def _save_interrupt_state(
        self, task, session: Session, interaction_data: Optional[list] = None
    ) -> None:
        """Save interruption state to session.state.

        Mirrors WorkflowController.interrupt_task() logic.

        Args:
            task: Task object.
            session: Session object.
            interaction_data: OutputSchema list from workflow.
        """
        ext = task.extensions or {}
        workflow_id = ext.get("workflow_id", "")
        state_key = workflow_id.replace(".", "_")

        state = session.get_state("workflow_controller") or {}
        state.setdefault("interrupted_tasks", {})

        component_id = self._extract_component_ids(interaction_data)
        interaction_value = self._extract_interaction_value(interaction_data)

        state["interrupted_tasks"][state_key] = {
            "task": task.model_dump(),
            "component_id": component_id,
            "last_interaction_value": interaction_value,
        }

        if task.extensions is None:
            task.extensions = {}
        task.extensions["component_id"] = component_id
        await self._task_manager.update_task(task)

        self._flush_session_state(session, state)

    def _clear_interrupt_state(self, task, session: Session) -> None:
        """Remove interruption state for this workflow.

        Args:
            task: Task object.
            session: Session object.
        """
        ext = task.extensions or {}
        workflow_id = ext.get("workflow_id", "")
        state_key = workflow_id.replace(".", "_")

        state = session.get_state("workflow_controller") or {}
        interrupted_tasks = state.get("interrupted_tasks", {})

        if state_key in interrupted_tasks:
            del interrupted_tasks[state_key]
            self._flush_session_state(session, state)

    @staticmethod
    def _flush_session_state(session: Session, state: dict) -> None:
        """Atomically replace workflow_controller state.

        Args:
            session: Session object.
            state: New state dict to persist.
        """
        session.update_state({"workflow_controller": None})
        session.update_state({"workflow_controller": state})

    # ------------------------------------------------------------------
    # Workflow lookup
    # ------------------------------------------------------------------

    async def _find_workflow(self, workflow_id: str, session: Session, agent_id: str = ""):
        """Find workflow object via Runner.resource_mgr.

        Args:
            workflow_id: Workflow ID (format: {id}_{version}).
            session: Session object.
            agent_id: Agent ID

        Returns:
            Workflow object or None.
        """
        try:
            workflow = await Runner.resource_mgr.get_workflow(
                workflow_id=workflow_id, tag=agent_id, session=session
            )
            if workflow:
                return workflow
        except Exception as e:
            logger.warning("Failed to find workflow %s: %s", workflow_id, e)
        logger.error("Workflow not found: %s", workflow_id)
        return None

    # ------------------------------------------------------------------
    # Chunk extraction helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _find_first_interaction(chunks: Optional[list]) -> Optional[OutputSchema]:
        """Find the first __interaction__ OutputSchema.

        Args:
            chunks: Collected stream chunks.

        Returns:
            First interaction OutputSchema, or None.
        """
        if not chunks:
            return None
        for chunk in chunks:
            if isinstance(chunk, OutputSchema) and chunk.type == INTERACTION:
                return chunk
        return None

    @staticmethod
    def _extract_component_ids(
        interaction_data: Optional[list],
    ) -> Union[str, List[str]]:
        """Extract component ID(s) from interaction data.

        Args:
            interaction_data: OutputSchema list.

        Returns:
            Single component_id str or list of IDs.
        """
        if not interaction_data:
            return ""

        component_ids: List[str] = []
        try:
            for item in interaction_data:
                if (
                    hasattr(item, "type")
                    and item.type == INTERACTION
                    and hasattr(item, "payload")
                    and hasattr(item.payload, "id")
                ):
                    component_ids.append(item.payload.id)
        except Exception as e:
            logger.warning("Failed to extract component_ids: %s", e)

        if not component_ids:
            return ""
        return component_ids[0] if len(component_ids) == 1 else component_ids

    @staticmethod
    def _extract_interaction_value(interaction_data: Optional[list]) -> Optional[object]:
        """Extract first interaction value from data.

        Args:
            interaction_data: OutputSchema list.

        Returns:
            Interaction value (str, dict, etc.) or None.
        """
        if not interaction_data:
            return None

        try:
            for item in interaction_data:
                if (
                    hasattr(item, "type")
                    and item.type == INTERACTION
                    and hasattr(item, "payload")
                    and hasattr(item.payload, "value")
                ):
                    return item.payload.value
        except Exception as e:
            logger.warning("Failed to extract interaction_value: %s", e)
        return None
