# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""Workflow Event Handler

Decision layer for the new Controller framework
(EventHandler + TaskExecutor + TaskScheduler).

Responsibilities:
- Detect user intent (new / resume / cancel / default)
- Route to appropriate handler
- Create new-architecture Task and add to TaskManager
- Handle short-circuit paths (return_interruption,
  default_response) without creating a Task

All data types use controller.schema. IntentDetector and
IntentDetectionConfig are self-contained in the local
intent_detector module (zero controller.legacy imports).
"""
import json
import re
import secrets
import uuid
from typing import Any, Dict, List, Optional, Union

from pydantic import BaseModel, Field

from openjiuwen.core.common.constants.constant import (
    INTERACTION,
)
from openjiuwen.core.common.constants.enums import TaskType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.json_utils import JsonUtils
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.common.utils.hash_util import generate_key
from openjiuwen.core.common.utils.message_utils import (
    MessageUtils,
)
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.foundation.llm import AssistantMessage, SystemMessage, UserMessage, BaseMessage, ModelConfig, \
    ModelClientConfig, ModelRequestConfig
from openjiuwen.core.controller.modules.event_handler import (
    EventHandler,
    EventHandlerInput,
)
from openjiuwen.core.controller.modules.task_manager import (
    TaskFilter,
)
from openjiuwen.core.controller import (
    ControllerOutputChunk,
    ControllerOutputPayload,
    Event,
    InputEvent,
    Intent,
    IntentType,
    Task,
    TaskStatus,
    TextDataFrame,
    ControllerConfig, JsonDataFrame,
)
from openjiuwen.core.controller.schema.controller_output import ALL_TASKS_PROCESSED

from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.session import InteractionOutput, InteractiveInput
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent import AbilityManager
from openjiuwen.core.single_agent.legacy import (
    WorkflowSchema,
)
from openjiuwen.core.workflow import WorkflowCard


# Internal dataclass-like container for _detect_intent
# to pass workflow + interrupted task info alongside Intent.
# Avoids polluting Intent.metadata with large serialized
# objects that don't belong there.
class _DetectResult:
    """Bundle _detect_intent output: intent + workflow + task data."""

    __slots__ = ("intent", "workflow", "task_data")

    def __init__(
        self,
        intent: Intent,
        workflow: Optional[WorkflowSchema] = None,
        task_data: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.intent = intent
        self.workflow = workflow
        # task_data keys: target_id, target_name, arguments
        self.task_data = task_data


class WorkflowEventHandler(EventHandler):
    """Handle input events for workflow-based agents.

    Uses new-architecture Intent/IntentType for routing.
    IntentDetector is used for LLM-based workflow selection
    when multiple workflows exist.
    """

    def __init__(self) -> None:
        super().__init__()
        self._intent_detector: Optional[IntentDetector] = None

    def _get_workflows(self) -> List[WorkflowCard]:
        """Return all WorkflowCard abilities registered with this agent."""
        return [item for item in self.ability_manager.list() if isinstance(item, WorkflowCard)]

    @staticmethod
    def _workflow_state_key(workflow_id: str) -> str:
        """Convert workflow_id to session state key (dots -> underscores)."""
        return workflow_id.replace(".", "_")

    # ------------------------------------------------------------------
    # EventHandler ABC implementation
    # ------------------------------------------------------------------

    async def handle_input(
        self, inputs: EventHandlerInput
    ) -> Optional[Dict]:
        """Main entry: intent detection -> route.

        Args:
            inputs: EventHandlerInput with event + session.

        Returns:
            Optional response dict.
        """
        event = inputs.event
        session = inputs.session

        # 1. Detect intent
        result = await self._detect_intent(event, session)
        intent = result.intent

        # 2. Add user message to context
        display_content = self._extract_display_text(event)
        if isinstance(display_content, dict):
            display_content = JsonUtils.safe_json_dumps(display_content, ensure_ascii=False)

        await MessageUtils.add_user_message(
            display_content,
            self._context_engine,
            session,
        )

        # 3. Route by intent type
        intent_type = intent.intent_type
        if intent_type == IntentType.CREATE_TASK:
            await self._route_new_task(
                event, result, session
            )
        elif intent_type in (
            IntentType.SUPPLEMENT_TASK,
            IntentType.RESUME_TASK,
        ):
            await self._route_resume(
                event, result, session
            )
        elif intent_type == IntentType.CANCEL_TASK:
            await self._route_cancel(
                event, result, session
            )
        elif intent_type == IntentType.UNKNOWN_TASK:
            await self._route_default_response(
                event, result, session
            )
        else:
            logger.warning(
                "Unknown intent type: %s", intent_type
            )
        return {"status": "success"}

    async def handle_task_interaction(
        self, inputs: EventHandlerInput
    ) -> Optional[Dict]:
        """No-op: TaskScheduler handles interaction."""
        return {"status": "success"}

    async def handle_task_completion(
        self, inputs: EventHandlerInput
    ) -> Optional[Dict]:
        """No-op: TaskScheduler handles completion."""
        return {"status": "success"}

    async def handle_task_failed(
        self, inputs: EventHandlerInput
    ) -> Optional[Dict]:
        """No-op: TaskScheduler handles failure."""
        return {"status": "success"}

    # ------------------------------------------------------------------
    # Routing methods
    # ------------------------------------------------------------------

    async def _route_new_task(
        self,
        event: Event,
        result: _DetectResult,
        session: Session,
    ) -> None:
        """Create a new Task and add to TaskManager.

        Args:
            event: InputEvent from user.
            result: Detection result with workflow + task_data.
            session: Session object.

        Returns:
            None (TaskScheduler picks up the task).
        """
        workflow = result.workflow
        task_data = result.task_data
        if not workflow or not task_data:
            logger.error(
                "_route_new_task: missing workflow or "
                "task_data"
            )
            return None

        workflow_id = task_data["target_id"]
        ext = {
            "agent_id": self.task_scheduler.card.id,
            "workflow_id": workflow_id,
            "workflow_version": workflow.version,
            "resume_mode": "new",
            "interactive_input": None,
            "filtered_inputs": task_data.get("arguments"),
        }

        new_task = Task(
            session_id=session.get_session_id(),
            task_id=f"wf_{uuid.uuid4().hex[:12]}",
            task_type=TaskType.WORKFLOW.value,
            description=task_data.get("target_name"),
            status=TaskStatus.SUBMITTED,
            extensions=ext,
        )

        await self._task_manager.add_task(new_task)
        logger.info(
            "_route_new_task: added task %s, workflow=%s",
            new_task.task_id,
            workflow_id,
        )
        return None

    async def _route_resume(
        self,
        event: Event,
        result: _DetectResult,
        session: Session,
    ) -> None:
        """Handle resume: short-circuit or create Task.

        Two paths:
        1. return_interruption: rebuild InteractionOutput,
           write to session, send completion_signal.
        2. Normal resume: build InteractiveInput, create
           Task with resume_mode="resume".

        Args:
            event: InputEvent from user.
            result: Detection result.
            session: Session object.

        Returns:
            None
        """
        intent = result.intent

        # Path 1: return_interruption short-circuit
        if intent.metadata and intent.metadata.get(
            "return_interruption"
        ):
            return await self._handle_return_interruption(
                result, session
            )

        # Path 2: normal resume
        workflow = result.workflow
        task_data = result.task_data
        if not workflow or not task_data:
            logger.error(
                "_route_resume: missing workflow or "
                "task_data"
            )
            return None

        workflow_id = task_data["target_id"]
        interactive_input = self._build_interactive_input(
            event, workflow_id, session
        )

        ext = {
            "workflow_id": workflow_id,
            "workflow_version": workflow.version,
            "resume_mode": "resume",
            "interactive_input": interactive_input,
            "filtered_inputs": None,
        }

        new_task = Task(
            session_id=session.get_session_id(),
            task_id=f"wf_{uuid.uuid4().hex[:12]}",
            task_type=TaskType.WORKFLOW.value,
            description=task_data.get("target_name"),
            status=TaskStatus.SUBMITTED,
            extensions=ext,
        )

        await self._task_manager.add_task(new_task)
        logger.info(
            "_route_resume: added resume task %s, "
            "workflow=%s",
            new_task.task_id,
            workflow_id,
        )
        return None

    async def _route_cancel(
        self,
        event: Event,
        result: _DetectResult,
        session: Session,
    ) -> None:
        """Cancel running task via TaskScheduler.

        Args:
            event: InputEvent from user.
            result: Detection result.
            session: Session object.

        Returns:
            None.
        """
        logger.info("_route_cancel: cancelling tasks")
        # Query active tasks and mark as CANCELED
        active_statuses = (
            TaskStatus.SUBMITTED,
            TaskStatus.WORKING,
            TaskStatus.INPUT_REQUIRED,
        )
        for status in active_statuses:
            tasks = await self._task_manager.get_task(
                TaskFilter(
                    session_id=session.get_session_id(),
                    status=status,
                )
            )
            for task in tasks:
                await self._task_manager.update_task_status(
                    task.task_id, TaskStatus.CANCELED
                )
        return None

    async def _route_default_response(
        self,
        event: Event,
        result: _DetectResult,
        session: Session,
    ) -> None:
        """Write default response to session and send
        completion signal (short-circuit, no Task).

        Args:
            event: InputEvent from user.
            result: Detection result with default_response_text.
            session: Session object.

        Returns:
            None.
        """
        intent = result.intent
        metadata = intent.metadata or {}
        default_text = metadata.get(
            "default_response_text", ""
        )
        logger.info(
            "_route_default_response: %s", default_text
        )

        # Write workflow_final frame
        workflow_final = OutputSchema(
            type="workflow_final",
            index=0,
            payload={
                "response": default_text,
                "output": {},
                "status": "default_response",
            },
        )
        await session.write_stream(workflow_final)

        # Add assistant message to context
        await MessageUtils.add_ai_message(
            AssistantMessage(content=default_text),
            self._context_engine,
            session,
        )

        # Send completion signal
        await self._send_completion_signal(session)

        return None

    # ------------------------------------------------------------------
    # Short-circuit helpers
    # ------------------------------------------------------------------

    async def _handle_return_interruption(
        self,
        result: _DetectResult,
        session: Session,
    ) -> None:
        """Return saved interruption directly (dict-type).

        Rebuilds InteractionOutput from session.state and
        writes it to session stream, then sends completion
        signal. No Task is created.

        Args:
            result: Detection result with task_data.
            session: Session object.

        Returns:
            None
        """
        task_data = result.task_data
        if not task_data:
            return None

        workflow_id = task_data["target_id"]
        state = session.get_state("workflow_controller")
        if not state:
            logger.warning(
                "_handle_return_interruption: "
                "no workflow_controller state"
            )
            return None

        state_key = workflow_id.replace(".", "_")
        interrupted_info = state.get(
            "interrupted_tasks", {}
        ).get(state_key)
        if not interrupted_info:
            logger.warning(
                "_handle_return_interruption: "
                "no interrupted task info"
            )
            return None

        component_id = interrupted_info.get(
            "component_id", "questioner"
        )
        last_value = interrupted_info.get(
            "last_interaction_value"
        )
        if last_value is None:
            logger.warning(
                "_handle_return_interruption: "
                "no last_interaction_value"
            )
            return None

        # Rebuild and write __interaction__ OutputSchema
        interaction_output = InteractionOutput(
            id=component_id, value=last_value
        )
        schema = OutputSchema(
            type=INTERACTION,
            index=0,
            payload=interaction_output,
        )
        await session.write_stream(schema)

        # Send completion signal
        await self._send_completion_signal(session)

        logger.info(
            "_handle_return_interruption: "
            "returned interruption for %s",
            workflow_id,
        )
        return None

    async def _send_completion_signal(
        self, session: Session
    ) -> None:
        """Write all_tasks_processed chunk to session.

        Used by short-circuit paths (default_response,
        return_interruption) where TaskScheduler does not
        run and therefore cannot emit the signal itself.

        Args:
            session: Session object.
        """
        chunk = ControllerOutputChunk(
            index=0,
            type="controller_output",
            payload=ControllerOutputPayload(
                type=ALL_TASKS_PROCESSED,
                data=[
                    TextDataFrame(
                        text="All tasks have been "
                        "successfully processed"
                    )
                ],
            ),
            last_chunk=True,
        )
        await session.write_stream(chunk)

    # ------------------------------------------------------------------
    # InteractiveInput builder
    # ------------------------------------------------------------------

    def _build_interactive_input(
        self,
        event: Event,
        workflow_id: str,
        session: Session,
    ):
        """Build InteractiveInput for resume path.

        Mirrors IntentDetectionController._handle_resume()
        logic: check provided InteractiveInput, remap if
        component_id mismatch, or create from query text.

        Args:
            event: InputEvent from user.
            workflow_id: Target workflow identifier.
            session: Session object.

        Returns:
            InteractiveInput object.
        """
        target_component_id = self._get_component_id(
            workflow_id, session
        )

        # Normalize to list
        target_ids = (
            target_component_id
            if isinstance(target_component_id, list)
            else [target_component_id]
        )

        # Check if event carries InteractiveInput via
        provided = self._extract_interactive_input(event)
        if provided is not None:
            if provided.user_inputs:
                provided_keys = list(
                    provided.user_inputs.keys()
                )
                matches = any(
                    k in target_ids
                    for k in provided_keys
                )
                if not matches:
                    # Remap to first target component
                    user_value = list(
                        provided.user_inputs.values()
                    )[0]
                    logger.info(
                        "Component ID mismatch: "
                        "provided=%s, target=%s. "
                        "Remapping.",
                        provided_keys,
                        target_component_id,
                    )
                    ii = InteractiveInput()
                    ii.update(target_ids[0], user_value)
                    return ii
                return provided
            return provided

        # Create from query text
        query_text = self._extract_display_text(event)
        ii = InteractiveInput()
        ii.update(target_ids[0], query_text)
        logger.info(
            "Created InteractiveInput: "
            "component=%s, query=%s",
            target_component_id,
            query_text,
        )
        return ii

    def _get_component_id(
        self, workflow_id: str, session: Session
    ) -> Any:
        """Get interrupted component_id from state.

        Args:
            workflow_id: Workflow identifier.
            session: Session object.

        Returns:
            str or list[str], defaults to "questioner".
        """
        state = session.get_state("workflow_controller")
        if not state:
            return "questioner"

        state_key = workflow_id.replace(".", "_")
        info = state.get("interrupted_tasks", {}).get(
            state_key
        )
        if info:
            return info.get(
                "component_id", "questioner"
            )
        return "questioner"

    # ------------------------------------------------------------------
    # Intent detection (migrated from WorkflowController)
    # ------------------------------------------------------------------

    async def _detect_intent(
        self,
        event: Event,
        session: Session,
    ) -> _DetectResult:
        """Detect user intent: select workflow + check interruption state.

        Args:
            event: InputEvent from user.
            session: Session object.

        Returns:
            _DetectResult
        """
        controller_config = self._get_controller_config()
        workflows = self._get_workflows()

        if not workflows:
            raise ValueError("No workflows configured")

        # Fast path: InteractiveInput with node_id
        provided_interactive_input = self._extract_interactive_input(event, session)
        if provided_interactive_input is not None and provided_interactive_input.user_inputs:
            resume_result = self._find_interrupted_task_by_node_id(
                provided_interactive_input, session
            )
            if resume_result:
                wf, task_data = resume_result
                intent = self._make_intent(
                    IntentType.SUPPLEMENT_TASK,
                    event,
                    target_task_id=task_data["target_id"],
                    supplementary_info="interactive_input",
                )
                return _DetectResult(intent, wf, task_data)

        # Select workflow
        if len(workflows) == 1:
            detected_workflow = workflows[0]
        else:
            detected_workflow = await self._detect_workflow_via_llm(event, session)
            if detected_workflow is None:
                default_text = controller_config.default_response.text
                intent = self._make_intent(
                    IntentType.UNKNOWN_TASK,
                    event,
                    clarification_prompt=default_text,
                    metadata={"default_response_text": default_text},
                )
                return _DetectResult(intent)

        # Check for interrupted task
        interrupted = self._find_interrupted_task(detected_workflow, session)

        if interrupted:
            should_resume = self._should_resume(interrupted, event, session)
            if should_resume:
                intent = self._make_intent(
                    IntentType.SUPPLEMENT_TASK,
                    event,
                    target_task_id=interrupted["target_id"],
                    supplementary_info="resume",
                )
            else:
                intent = self._make_intent(
                    IntentType.SUPPLEMENT_TASK,
                    event,
                    target_task_id=interrupted["target_id"],
                    supplementary_info="return_interruption",
                    metadata={"return_interruption": True},
                )
            return _DetectResult(intent, detected_workflow, interrupted)

        # No interruption: create new task
        task_data = self._build_new_task_data(event, detected_workflow)
        intent = self._make_intent(
            IntentType.CREATE_TASK,
            event,
            target_task_description=detected_workflow.name,
        )
        return _DetectResult(intent, detected_workflow, task_data)

    async def _detect_workflow_via_llm(
        self,
        event: Event,
        session: Session,
    ) -> Optional[WorkflowSchema]:
        """Use LLM to detect which workflow to run.

        Args:
            event: InputEvent from user.
            session: Session object.

        Returns:
            WorkflowSchema or None (for default_response).
        """
        controller_config = self._get_controller_config()
        workflows = self._get_workflows()

        try:
            self._ensure_intent_detection_initialized(session)

            if not self._intent_detector:
                return workflows[0]

            detected_tasks = await self._intent_detector.process_message(event)

            if not detected_tasks:
                default_response = controller_config.default_response
                if default_response and default_response.text:
                    return None
                return workflows[0]

            workflow_name = detected_tasks[0].input.target_name
            for wf in workflows:
                if wf.name == workflow_name:
                    return wf

            logger.warning("Workflow '%s' not found, using first", workflow_name)
            return workflows[0]

        except Exception as e:
            logger.error("Intent detection failed: %s, using first workflow", e)
            return workflows[0]

    def _ensure_intent_detection_initialized(
        self, session: Session
    ) -> None:
        """Lazily initialize IntentDetector.

        Args:
            session: Session object.
        """
        controller_config = self._get_controller_config()
        if not controller_config.enable_intent_recognition:
            return

        if self._intent_detector:
            self._intent_detector.session = session
            return

        workflows = self._get_workflows()
        category_list = [wf.description or wf.name for wf in workflows]
        intent_config = IntentDetectionConfig(
            category_list=category_list,
            category_info="\n".join(f"- {wf.description or wf.name}" for wf in workflows),
            enable_history=True,
            enable_input=True,
        )

        self._intent_detector = IntentDetector(
            intent_config=intent_config,
            controller_config=controller_config,
            context_engine=self._context_engine,
            session=session,
            ability_manager=self.ability_manager,
        )

    # ------------------------------------------------------------------
    # Interrupted task lookup
    # ------------------------------------------------------------------

    def _find_interrupted_task_by_node_id(
        self,
        interactive_input,
        session: Session,
    ) -> Optional[tuple]:
        """Find interrupted workflow by node_id.

        Args:
            interactive_input: InteractiveInput with user_inputs.
            session: Session object.

        Returns:
            (WorkflowCard, task_data dict) or None.
        """
        state = session.get_state("workflow_controller")
        if not state:
            return None

        interrupted_tasks = state.get("interrupted_tasks", {})
        if not interrupted_tasks:
            return None

        node_ids = list(interactive_input.user_inputs.keys())
        if not node_ids:
            return None

        workflows = self._get_workflows()
        for wf_key, task_info in interrupted_tasks.items():
            component_id = task_info.get("component_id")
            if isinstance(component_id, list):
                matched = any(nid in component_id for nid in node_ids)
            else:
                matched = component_id in node_ids

            if matched:
                task_extensions = task_info.get("task", {}).get("extensions", {})
                for wf in workflows:
                    if wf_key == wf.id.replace(".", "_"):
                        return wf, {
                            "target_id": task_extensions.get("workflow_id", wf.id),
                            "target_name": wf.name,
                            "workflow_version": wf.version,
                            "agent_id": task_extensions.get("agent_id", ""),
                            "arguments": task_extensions.get("filtered_inputs", {}),
                        }

        return None

    def _find_interrupted_task(
        self,
        workflow: WorkflowSchema,
        session: Session,
    ) -> Optional[Dict[str, Any]]:
        """Find interrupted task for a workflow.

        Uses dual-key fallback for state_key lookup.

        Args:
            workflow: WorkflowSchema.
            session: Session object.

        Returns:
            task_data dict or None.
        """
        state = session.get_state("workflow_controller")
        if not state:
            return None

        interrupted_tasks = state.get(
            "interrupted_tasks", {}
        )
        base_id = workflow.id.replace('.', '_')
        possible_ids = [base_id, workflow.id]

        for wf_id in possible_ids:
            if wf_id in interrupted_tasks:
                task_dict = interrupted_tasks[wf_id].get("task", {})
                task_extensions = task_dict.get("extensions", {})
                return {
                    "target_id": task_extensions.get("workflow_id", wf_id),
                    "target_name": "",
                    "workflow_version": task_extensions.get("workflow_version", ""),
                    "agent_id": task_extensions.get("agent_id", ""),
                    "arguments": task_extensions.get("filtered_inputs", {}),
                }
        return None

    def _should_resume(
        self,
        task_data: Dict[str, Any],
        event: Event,
        session: Session,
    ) -> bool:
        """Check if interrupted task should resume or
        return interruption again.

        Logic:
        1. InteractiveInput provided -> always resume
        2. last_interaction_value is dict/list -> return
        3. last_interaction_value is str -> resume

        Args:
            task_data: Interrupted task data dict.
            event: InputEvent from user.
            session: Session object.

        Returns:
            True to resume, False to return interruption.
        """
        provided_ii = self._extract_interactive_input(event, session)
        if provided_ii is not None and provided_ii.user_inputs:
            return True

        state = session.get_state("workflow_controller")
        if not state:
            return True

        workflow_id = task_data["target_id"]
        state_key = workflow_id.replace(".", "_")
        info = state.get("interrupted_tasks", {}).get(state_key)
        if not info:
            return True

        last_val = info.get("last_interaction_value")
        if last_val is None:
            return True

        if isinstance(last_val, (dict, list)):
            logger.info(
                "last_interaction_value is structured "
                "data, returning interruption again"
            )
            return False

        return True

    # ------------------------------------------------------------------
    # Task data builder (replaces legacy _create_new_task)
    # ------------------------------------------------------------------

    def _build_new_task_data(
        self,
        event: Event,
        workflow: WorkflowSchema,
    ) -> Dict[str, Any]:
        """Build task_data dict for a new workflow task.

        Mirrors WorkflowController._create_new_task().
        Returns a plain dict instead of a legacy Task.

        Args:
            event: InputEvent from user.
            workflow: Detected WorkflowSchema.

        Returns:
            Dict with target_id, target_name, arguments.
        """
        query = self._extract_display_text(event)

        schema = workflow.input_params or {}
        required_key = self._get_required_input_key(
            schema
        )
        if not required_key:
            required_key = "query"

        user_data = {required_key: query}

        # Merge extensions from event metadata
        event_meta = event.metadata or {}
        extensions = event_meta.get("extensions", {})
        if extensions and isinstance(extensions, dict):
            user_data.update(extensions)

        filtered_inputs = self._filter_workflow_inputs(
            schema, user_data
        )

        return {
            "target_id": workflow.id,
            "target_name": workflow.name,
            "arguments": filtered_inputs,
        }

    @staticmethod
    def _get_required_input_key(
        schema: Dict,
    ) -> Optional[str]:
        """Get the required input key from schema.

        Args:
            schema: Workflow input_params schema.

        Returns:
            Key name or None.
        """
        if not schema:
            return None

        properties = schema.get("properties", {})
        if not properties:
            return None

        if "query" in properties:
            return "query"

        required = schema.get("required", [])
        if required and isinstance(required, list):
            for key in required:
                if key in properties:
                    return key

        if "input" in properties:
            return "input"

        return None

    @staticmethod
    def _filter_workflow_inputs(
        schema: Dict, user_data: Dict
    ) -> Dict:
        """Filter inputs based on workflow schema.

        Args:
            schema: Workflow input_params schema.
            user_data: User-provided data.

        Returns:
            Filtered dict.
        """
        properties = schema.get("properties", {})

        if not properties and schema:
            if any(
                isinstance(v, dict) and "type" in v
                for v in schema.values()
            ):
                properties = schema

        filtered = {}
        for key, value in user_data.items():
            if key in properties or not properties:
                filtered[key] = value
        return filtered

    # ------------------------------------------------------------------
    # Extraction helpers (new-arch Event -> usable data)
    # ------------------------------------------------------------------

    @staticmethod
    def _extract_display_text(event: Event) -> str:
        """Extract display text from InputEvent.

        Maps legacy EventContent.get_query() to new
        InputEvent.input_data[0].text.

        Args:
            event: Event (expected InputEvent).

        Returns:
            str, empty string if unavailable.
        """
        if not isinstance(event, InputEvent):
            return ""
        if (
            event.input_data
            and hasattr(event.input_data[0], "type")
        ):
            if event.input_data[0].type == "json":
                data = event.input_data[0].data
                if isinstance(data, dict):
                    query = data.get("query", "")
                    if isinstance(query, str):
                        return query
                    elif isinstance(query, InteractiveInput):
                        user_inputs = query.user_inputs
                        feedback_list = list(user_inputs.values())
                        return feedback_list[0] if feedback_list else ""
        return ""

    def _extract_interactive_input(self, event: Event, session: Session = None) -> Optional[InteractiveInput]:
        """Extract InteractiveInput from event if present.

        Checks event.metadata for interactive_input key.
        Legacy events carried this on event.content; new
        events carry it in metadata.

        Args:
            event: Event object.

        Returns:
            InteractiveInput or None.
        """
        if event.metadata:
            interactive_input = event.metadata.get("interactive_input")
            if interactive_input is not None:
                return interactive_input

        workflows = self._get_workflows()
        interactive_input = None
        if isinstance(event, InputEvent):
            input_data = event.input_data
            if input_data:
                first_input_data = input_data[0]
                if isinstance(first_input_data, JsonDataFrame):
                    input_query = first_input_data.data.get("query")
                    if isinstance(input_query, InteractiveInput):
                        interactive_input = input_query
                    elif isinstance(input_query, str) and session is not None:
                        interactive_comp_id = self._recover_comp_id_from_session(
                            session, workflows
                        )
                        if interactive_comp_id:
                            interactive_input = InteractiveInput()
                            interactive_input.update(interactive_comp_id, input_query)

        return interactive_input

    @staticmethod
    def _recover_comp_id_from_session(session: Session, workflows: list) -> str:
        """Recover component_id from session state for single-workflow agents.

        Args:
            session: Session object.
            workflows: List of registered WorkflowCard items.

        Returns:
            component_id str, or empty string if not found.
        """
        state = session.get_state("workflow_controller")
        if not state:
            return ""
        interrupted_info = state.get("interrupted_tasks", {})
        if len(workflows) == 1 and isinstance(interrupted_info, dict) and len(interrupted_info) > 0:
            return list(interrupted_info.values())[0].get("component_id", "")
        return ""

    # ------------------------------------------------------------------
    # Intent factory
    # ------------------------------------------------------------------

    @staticmethod
    def _make_intent(
        intent_type: IntentType,
        event: Event,
        *,
        target_task_id: Optional[str] = None,
        target_task_description: Optional[str] = None,
        supplementary_info: Optional[str] = None,
        clarification_prompt: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> Intent:
        """Create a new-schema Intent.

        Args:
            intent_type: IntentType enum value.
            event: Associated Event.
            target_task_id: Target task ID (if applicable).
            target_task_description: Description for CREATE_TASK.
            supplementary_info: Info for SUPPLEMENT_TASK.
            clarification_prompt: Prompt for UNKNOWN_TASK.
            metadata: Extra metadata dict.

        Returns:
            Intent instance.
        """
        return Intent(
            intent_type=intent_type,
            event=event,
            target_task_id=target_task_id,
            target_task_description=target_task_description,
            supplementary_info=supplementary_info,
            clarification_prompt=clarification_prompt,
            metadata=metadata,
        )

    def _get_controller_config(self) -> ControllerConfig:
        """Get ControllerConfig for this agent."""
        return self._config


class IntentDetectionConstants:
    """Intent detection string constants."""

    USER_PROMPT = "user_prompt"
    CATEGORY_LIST = "category_list"
    DEFAULT_CLASS = "default_class"
    ENABLE_HISTORY = "enable_history"
    ENABLE_INPUT = "enable_input"
    EXAMPLE_CONTENT = "example_content"
    CHAT_HISTORY_MAX_TURN = "chat_history_max_turn"
    CHAT_HISTORY = "chat_history"
    INPUT = "input"
    ROLE_MAP = {"user": '用户', 'assistant': '助手', 'system': '系统'}


DEFAULT_SYSTEM_PROMPT = """你是一个意图分类助手，擅长判断用户的输入属于哪个分类。
当用户输入没有明确意图或者你无法判断用户输入意图时请选择 {{default_class}}。
以下是给定的意图分类列表：
{{category_list}}
{{example_content}}
请根据上述要求判断用户输入意图分类，输出要求如下：
直接以JSON格式输出分类ID，不进行任何解释。JSON格式如下：
 {"result": int}"""
DEFAULT_USER_PROMPT = """用户与助手的对话历史：
{{chat_history}}
当前输入：
{{input}}"""


def _get_default_template() -> PromptTemplate:
    return PromptTemplate(
        content=[
            SystemMessage(content=DEFAULT_SYSTEM_PROMPT),
            UserMessage(content=DEFAULT_USER_PROMPT),
        ]
    )


class IntentDetectionConfig(BaseModel):
    """Config for intent detection component."""

    category_info: str = Field(default="")
    category_list: List[str] = Field(default_factory=list)
    intent_detection_template: PromptTemplate = Field(default_factory=_get_default_template)
    user_prompt: str = Field(default=DEFAULT_USER_PROMPT)
    chat_history_max_turn: int = Field(default=100)
    default_class: str = Field(default="分类0")
    enable_history: bool = Field(default=False)
    enable_input: bool = Field(default=True)
    example_content: List[str] = Field(default_factory=list)


class _TaskInput:
    """Minimal replacement for legacy TaskInput."""

    __slots__ = ("target_id", "target_name", "arguments")

    def __init__(
        self,
        target_id: str = "",
        target_name: str = "",
        arguments: Any = None,
    ) -> None:
        self.target_id = target_id
        self.target_name = target_name
        self.arguments = arguments


class _TaskResult:
    """Minimal replacement for legacy Task.

    Only carries the fields that WorkflowEventHandler
    reads: task_id, task_type, input (_TaskInput).
    """

    __slots__ = ("task_id", "task_type", "input")

    def __init__(
        self,
        task_id: str,
        task_type: TaskType,
        task_input: _TaskInput,
    ) -> None:
        self.task_id = task_id
        self.task_type = task_type
        self.input = task_input


class IntentDetector:
    """LLM-based intent detection for workflow selection.

    Accepts new-arch Event objects. Returns List[_TaskResult]
    from process_message().
    """

    def __init__(
        self,
        intent_config: IntentDetectionConfig,
        controller_config: ControllerConfig,
        context_engine: ContextEngine,
        session: Session,
        ability_manager: AbilityManager
    ) -> None:
        self.intent_config = intent_config
        self.agent_config = controller_config
        self.context_engine = context_engine
        self.session = session
        self.ability_manager = ability_manager

    async def process_message(
        self, event: Any
    ) -> List[_TaskResult]:
        """Detect intent and generate task results.

        Args:
            event: InputEvent (new-arch) or legacy Event.

        Returns:
            List of _TaskResult objects.
        """
        llm_inputs = self._prepare_detection_input(event)
        sid = self.session.get_session_id()
        if UserConfig.is_sensitive():
            logger.info("[%s] <LLM Input>", sid)
        else:
            logger.info(
                "[%s] <LLM Input>: %s", sid, llm_inputs
            )

        llm_output = await self._invoke_llm_get_output(
            llm_inputs
        )
        if UserConfig.is_sensitive():
            logger.info("[%s] <LLM Output>", sid)
        else:
            logger.info(
                "[%s] <LLM Output>: %s", sid, llm_output
            )

        detected_id = self._parse_intent_from_output(
            llm_output
        )
        return self._generate_tasks_from_intent(
            detected_id, event
        )

    def _generate_tasks_from_intent(
        self, intent_id: str, event: Any
    ) -> List[_TaskResult]:
        """Create _TaskResult objects from detected intent."""
        tasks: List[_TaskResult] = []
        sid = self.session.get_session_id()
        uid = (
            f"{sid}_intent_{intent_id}_"
            f"{secrets.token_hex(4)}"
        )

        if intent_id == IntentDetectionConstants.DEFAULT_CLASS:
            return tasks

        workflows = [
            item for item in self.ability_manager.list() if isinstance(item, WorkflowCard)
        ]

        if not workflows:
            tasks.append(_TaskResult(
                task_id=uid,
                task_type=TaskType.WORKFLOW,
                task_input=_TaskInput(target_id=intent_id, target_name=intent_id),
            ))
            return tasks

        for workflow in workflows:
            if workflow.id == intent_id:
                tasks.append(_TaskResult(
                    task_id=uid,
                    task_type=TaskType.WORKFLOW,
                    task_input=_TaskInput(target_id=workflow.id, target_name=workflow.name),
                ))
                logger.info("[%s] created task for intent: %s", sid, intent_id)
                break
        return tasks

    def _parse_intent_from_output(self, llm_output: str) -> str:
        """Parse intent from LLM JSON output."""
        sid = self.session.get_session_id()
        try:
            cleaned = re.sub(
                r"^\s*```json\s*|\s*```\s*$", "", llm_output.strip(), flags=re.IGNORECASE
            )
            cleaned = re.sub(
                r"^\s*'''json\s*|\s*'''\s*$", "", cleaned, flags=re.IGNORECASE
            )
            output_data = json.loads(cleaned, strict=False)
            class_num = int(output_data.get("result", ""))
            cat_list = self.intent_config.category_list
            if class_num <= 0 or class_num > len(cat_list):
                logger.warning("get unknown class")
                return IntentDetectionConstants.DEFAULT_CLASS

            name = cat_list[class_num - 1]
            workflows = [
                item for item in self.ability_manager.list() if isinstance(item, WorkflowCard)
            ]
            if not workflows:
                return name

            for wf in workflows:
                if (wf.description or wf.name) == name:
                    logger.info("[%s] get intent: %s", sid, wf.id)
                    return wf.id

        except Exception:
            logger.error("failed to parse JSON from LLM output")

        return IntentDetectionConstants.DEFAULT_CLASS

    async def _invoke_llm_get_output(
        self, llm_inputs: Union[List[BaseMessage], str]
    ) -> str:
        """Call LLM and return content string."""
        try:
            from openjiuwen.core.runner import Runner
            model = await Runner.resource_mgr.get_model(
                model_id=self.agent_config.intent_llm_id, session=self.session
            )
            llm_output = await model.invoke(llm_inputs)
            return llm_output.content.strip()
        except Exception as e:
            raise build_error(
                StatusCode.AGENT_CONTROLLER_INVOKE_CALL_FAILED,
                error_msg=str(e),
            ) from e

    def _prepare_detection_input(
        self, event: Any
    ) -> Union[List[BaseMessage], str]:
        """Build LLM input messages for intent detection."""
        category_list = "分类0：意图不明\n" + "\n".join(
            f"分类{i + 1}：{c}"
            for i, c in enumerate(self.intent_config.category_list)
        )
        current_inputs = {
            IntentDetectionConstants.USER_PROMPT: (
                self.intent_config.user_prompt
            ),
            IntentDetectionConstants.CATEGORY_LIST: (
                category_list
            ),
            IntentDetectionConstants.DEFAULT_CLASS: (
                self.intent_config.default_class
            ),
            IntentDetectionConstants.ENABLE_HISTORY: self.intent_config.enable_history,
            IntentDetectionConstants.ENABLE_INPUT: self.intent_config.enable_input,
            IntentDetectionConstants.EXAMPLE_CONTENT: "\n\n".join(
                self.intent_config.example_content
            ),
            IntentDetectionConstants.CHAT_HISTORY_MAX_TURN: (
                self.intent_config.chat_history_max_turn
            ),
            IntentDetectionConstants.CHAT_HISTORY: "",
        }

        if self.intent_config.enable_history:
            chat_history = self.get_chat_history(
                self.context_engine,
                self.session,
                self.intent_config.chat_history_max_turn,
            )
            current_inputs[IntentDetectionConstants.CHAT_HISTORY] = "\n".join(
                f"{IntentDetectionConstants.ROLE_MAP.get(h.role, '用户')}: {h.content}"
                for h in chat_history
            )

        if self.intent_config.enable_input:
            current_inputs[IntentDetectionConstants.INPUT] = self._extract_query(event)

        return self.intent_config.intent_detection_template.format(current_inputs).to_messages()

    @staticmethod
    def _extract_query(event: Any) -> str:
        """Extract query text from InputEvent."""
        if not isinstance(event, InputEvent):
            return ""
        if event.input_data and hasattr(event.input_data[0], "type"):
            if event.input_data[0].type == "json":
                data = event.input_data[0].data
                if isinstance(data, dict):
                    return data.get("query", "")
        return ""

    @staticmethod
    def get_chat_history(
            context_engine: ContextEngine,
            session: Session,
            chat_history_max_turn: int,
    ) -> List[BaseMessage]:
        """Get history by max conversation rounds."""
        agent_context = context_engine.get_context(
            session_id=session.get_session_id()
        )
        chat_history = agent_context.get_messages()
        return chat_history[-2 * chat_history_max_turn:]

    @staticmethod
    async def get_model(
            model_config: ModelConfig,
            session: Optional[Session] = None,
    ):
        """Get model instance by config."""
        from openjiuwen.core.runner import Runner

        model_id = generate_key(
            model_config.model_info.api_key,
            model_config.model_info.api_base,
            model_config.model_provider,
        )
        model = await Runner.resource_mgr.get_model(
            model_id=model_id, session=session
        )
        if model is None:
            client_cfg = ModelClientConfig(
                client_id=model_id,
                client_provider=model_config.model_provider,
                api_key=model_config.model_info.api_key,
                api_base=model_config.model_info.api_base,
                timeout=model_config.model_info.timeout,
                verify_ssl=False,
                ssl_cert=None,
            )
            request_cfg = ModelRequestConfig(
                model=model_config.model_info.model_name,
                temperature=(
                    model_config.model_info.temperature
                ),
                top_p=model_config.model_info.top_p,
                **(model_config.model_info.model_extra or {}),
            )

            from openjiuwen.core.foundation.llm import Model

            def create_model():
                return Model(
                    model_client_config=client_cfg,
                    model_config=request_cfg,
                )

            Runner.resource_mgr.add_model(
                model_id=model_id, model=create_model
            )
            model = await Runner.resource_mgr.get_model(
                model_id=model_id, session=session
            )
        return model
