# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""DeepAgent implementation."""
from __future__ import annotations

from typing import (
    Any, AsyncIterator, Dict, List, Optional,
    TYPE_CHECKING, Tuple, cast,
)

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.common.security.user_config import UserConfig
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent.agents.react_agent import ReActAgent, ReActAgentConfig
from openjiuwen.core.single_agent.base import BaseAgent
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
    InvokeInputs,
)
from openjiuwen.core.single_agent.schema.agent_card import AgentCard
from openjiuwen.deepagents.schema.config import DeepAgentConfig
from openjiuwen.core.controller.config import ControllerConfig
from openjiuwen.core.context_engine import ContextEngine
from openjiuwen.core.controller.schema.event import (
    TaskInteractionEvent,
    FollowUpEvent,
)
from openjiuwen.deepagents.task_loop.task_loop_event_handler import (
    TaskLoopEventHandler,
)
from openjiuwen.deepagents.task_loop.task_loop_event_executor import (
    DEEP_TASK_TYPE,
    build_deep_executor,
)
from openjiuwen.deepagents.task_loop.loop_coordinator import (
    LoopCoordinator,
)
from openjiuwen.deepagents.schema.state import (
    clear_state,
    load_state,
    save_state,
)
from openjiuwen.deepagents.task_loop.loop_queues import (
    LoopQueues,
)
from openjiuwen.deepagents.task_loop.task_loop_controller import (
    TaskLoopController,
)

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.event_queue import (
        EventQueue,
    )

_DEFAULT_SYSTEM_PROMPT = (
    "你是一个通用 AI 助手。请根据用户的需求，合理使用可用工具完成任务。\n"
    "在执行过程中保持目标聚焦，遇到问题时尝试不同策略。"
)

# Events bridged to the inner ReActAgent.
_BRIDGE_EVENTS = frozenset(
    {
        AgentCallbackEvent.BEFORE_MODEL_CALL,
        AgentCallbackEvent.AFTER_MODEL_CALL,
        AgentCallbackEvent.ON_MODEL_EXCEPTION,
        AgentCallbackEvent.BEFORE_TOOL_CALL,
        AgentCallbackEvent.AFTER_TOOL_CALL,
        AgentCallbackEvent.ON_TOOL_EXCEPTION,
    }
)

# Events that stay on the outer DeepAgent only.
_OUTER_ONLY_EVENTS = frozenset(
    {
        AgentCallbackEvent.BEFORE_INVOKE,
        AgentCallbackEvent.AFTER_INVOKE,
    }
)

# Deep extension events.
_DEEP_EVENTS = frozenset(
    {
        AgentCallbackEvent.BEFORE_TASK_ITERATION,
        AgentCallbackEvent.AFTER_TASK_ITERATION,
    }
)


class DeepAgent(BaseAgent):
    """High-level agent that delegates to an internal ReActAgent."""

    def __init__(self, card: AgentCard):
        self._deep_config: Optional[DeepAgentConfig] = None
        self._react_agent: Optional[ReActAgent] = None
        self._pending_rails: List[AgentRail] = []
        self._loop_coordinator: Optional[LoopCoordinator] = None
        self._loop_controller: Optional[TaskLoopController] = None
        self._loop_session: Optional[Session] = None
        self._initialized = False
        super().__init__(card)

    def configure(self, config: DeepAgentConfig) -> "DeepAgent":
        """Apply configuration and rebuild the internal ReActAgent."""
        self._deep_config = config
        self._react_agent = self._create_react_agent()
        self._initialized = False
        return self

    def set_react_agent(
        self,
        react_agent: Any,
        initialized: bool = False,
    ) -> "DeepAgent":
        """Inject an inner agent implementation for runtime wiring/tests."""
        self._react_agent = cast(ReActAgent, react_agent)
        self._initialized = initialized
        return self

    @property
    def is_initialized(self) -> bool:
        """Whether lazy rail initialization is completed."""
        return self._initialized

    @property
    def deep_config(self) -> DeepAgentConfig:
        """deep_config carried by this agent."""
        return self._deep_config

    @property
    def react_agent(self) -> Optional[ReActAgent]:
        """The internal ReActAgent instance."""
        return self._react_agent

    @property
    def loop_coordinator(self) -> Optional[LoopCoordinator]:
        """LoopCoordinator for the outer task loop."""
        return self._loop_coordinator

    @property
    def event_queue(self) -> Optional["EventQueue"]:
        """EventQueue for the current task loop."""
        if self._loop_controller is None:
            return None
        return self._loop_controller.event_queue

    @property
    def event_handler(self) -> Optional[TaskLoopEventHandler]:
        """EventHandler for the current task loop."""
        if self._loop_controller is None:
            return None
        return cast(
            TaskLoopEventHandler,
            self._loop_controller.event_handler,
        )

    def _create_react_agent(self) -> ReActAgent:
        """Build the internal ReActAgent from current DeepAgentConfig."""
        cfg = self._deep_config
        if cfg is None:
            raise build_error(
                StatusCode.DEEPAGENT_CONFIG_PARAM_ERROR,
                error_msg="DeepAgentConfig is required. Call configure() first.",
            )

        inner_card = AgentCard(
            name=f"{self.card.name}_react",
            description=self.card.description or "",
        )

        react_config = ReActAgentConfig()
        react_config.max_iterations = cfg.max_iterations

        prompt = cfg.system_prompt or _DEFAULT_SYSTEM_PROMPT
        react_config.prompt_template = [{"role": "system", "content": prompt}]

        if cfg.model is not None:
            model = cfg.model
            if model.model_client_config is not None:
                react_config.model_client_config = model.model_client_config
            if model.model_config is not None:
                react_config.model_config_obj = model.model_config
                if model.model_config.model_name:
                    react_config.model_name = model.model_config.model_name

        agent = ReActAgent(inner_card)
        agent.configure(react_config)

        # Share ability manager so tools registered on DeepAgent are visible inside.
        agent.ability_manager = self.ability_manager

        # Inject pre-built Model to bypass lazy init.
        if cfg.model is not None:
            agent.set_llm(cfg.model)

        return agent

    async def _ensure_initialized(self) -> None:
        """Perform lazy async initialization."""
        if self._initialized:
            return

        for rail_inst in self._pending_rails:
            rail_inst.init(self)
            await self._register_rail_selective(rail_inst)
        self._pending_rails.clear()
        self._initialized = True

    def _normalize_inputs(self, inputs: Any) -> InvokeInputs:
        """Parse user inputs into typed InvokeInputs."""
        if isinstance(inputs, dict):
            query = inputs.get("query", "")
            conversation_id = inputs.get("conversation_id")
        elif isinstance(inputs, str):
            query = inputs
            conversation_id = None
        else:
            raise build_error(
                StatusCode.DEEPAGENT_INPUT_PARAM_ERROR,
                error_msg="Input must be dict with 'query' or str.",
            )

        return InvokeInputs(query=query, conversation_id=conversation_id)

    @staticmethod
    def _to_effective_inputs(invoke_inputs: InvokeInputs) -> Dict[str, Any]:
        """Convert typed invoke inputs to ReAct input dict."""
        effective_inputs: Dict[str, Any] = {"query": invoke_inputs.query}
        if invoke_inputs.conversation_id is not None:
            effective_inputs["conversation_id"] = invoke_inputs.conversation_id
        return effective_inputs

    def add_rail(self, rail: AgentRail) -> "DeepAgent":
        """Synchronously queue a rail for registration."""
        self._pending_rails.append(rail)
        return self

    async def register_rail(self, rail: AgentRail) -> "DeepAgent":
        """Register a rail with selective routing."""
        rail.init(self)
        await self._register_rail_selective(rail)
        return self

    async def unregister_rail(self, rail: AgentRail) -> "DeepAgent":
        """Unregister a rail from pending/outer/inner."""
        # Remove queued instances first so lazy-init does not re-register stale rails.
        self._pending_rails = [queued for queued in self._pending_rails if queued is not rail]

        # Remove from outer DeepAgent.
        await self._agent_callback_manager.unregister_rail(rail, self)

        # Remove bridged callbacks from inner agent.
        if self._react_agent is not None:
            await self._react_agent.agent_callback_manager.unregister_rail(
                rail, self._react_agent
            )
        rail.uninit(self)
        return self

    async def _register_rail_selective(self, rail: AgentRail) -> None:
        """Route rail callbacks to the correct agent."""
        callbacks = rail.get_callbacks()

        for event, callback in callbacks.items():
            if event in _BRIDGE_EVENTS:
                if self._react_agent is not None:
                    await self._react_agent.register_callback(event, callback, rail.priority)
                continue

            if event in _OUTER_ONLY_EVENTS or event in _DEEP_EVENTS:
                await self.register_callback(event, callback, rail.priority)
                continue

            logger.warning(
                f"Unknown rail event {event}, registering on outer DeepAgent"
            )
            await self.register_callback(event, callback, rail.priority)

    async def _run_single_round_invoke(
        self, ctx: AgentCallbackContext, session: Optional[Session]
    ) -> Dict[str, Any]:
        """Invoke inner ReActAgent exactly once."""
        modified = ctx.inputs
        if not isinstance(modified, InvokeInputs):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="ctx.inputs must be InvokeInputs for single-round invoke.",
            )

        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        return await self._react_agent.invoke(
            self._to_effective_inputs(modified),
            session,
        )

    async def _setup_task_loop(
        self,
        session: Session,
    ) -> Tuple[LoopCoordinator, TaskLoopController]:
        """Create Controller infrastructure for a task loop.

        Sets instance attributes and returns the key objects
        needed by the loop body.

        Args:
            session: Current session.

        Returns:
            (coordinator, controller)
        """
        coordinator = LoopCoordinator(
            stop_condition=(
                self._deep_config.stop_condition
                if self._deep_config
                else None
            ),
        )
        coordinator.reset()

        queues = LoopQueues()

        config = ControllerConfig(suppress_completion_signal=True)
        context_engine = ContextEngine()

        controller = TaskLoopController()
        controller.init(
            card=self.card,
            config=config,
            ability_manager=self.ability_manager,
            context_engine=context_engine,
        )
        controller.add_task_executor(
            DEEP_TASK_TYPE, build_deep_executor(self),
        )

        handler = TaskLoopEventHandler(self)
        handler.interaction_queues = queues
        controller.set_event_handler(handler)

        self._loop_coordinator = coordinator
        self._loop_controller = controller
        self._loop_session = session
        return coordinator, controller

    def _has_remaining_tasks(self, session: Session) -> bool:
        """Check whether the task plan still has pending tasks."""
        state_ctx = AgentCallbackContext(agent=self, session=session)
        st = load_state(state_ctx)
        if st.task_plan is None:
            return False
        return st.task_plan.get_next_task() is not None

    @staticmethod
    def _log_loop(msg: str, detail: str = "") -> None:
        """Log an outer-loop event respecting sensitivity."""
        if UserConfig.is_sensitive():
            logger.info(f"[OuterLoop] {msg}")
        else:
            logger.info(f"[OuterLoop] {msg}{detail}")

    async def _run_task_loop(
        self, ctx: AgentCallbackContext, session: Session,
    ) -> AsyncIterator[Dict[str, Any]]:
        """Async generator for the outer task loop. Shared by invoke and stream.

        Args:
            ctx: Callback context with InvokeInputs.
            session: Current session.

        Yields:
            Result dict from each iteration.
        """
        modified = ctx.inputs
        if not isinstance(modified, InvokeInputs):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="ctx.inputs must be InvokeInputs for task-loop.",
            )

        coordinator, controller = await self._setup_task_loop(session)
        timeout = self._deep_config.completion_timeout if self._deep_config else 600.0

        await controller.bind_session(session)
        try:
            current_query = modified.query
            outer_round = 0

            while coordinator.should_continue():
                outer_round += 1
                follow_ups = controller.drain_follow_up()
                if follow_ups:
                    current_query = follow_ups[-1]

                self._log_loop(
                    f"round={outer_round} started",
                    f", query={current_query[:120]}",
                )

                await controller.submit_round(
                    session, current_query, is_follow_up=bool(follow_ups),
                )
                result = await controller.wait_round_completion(timeout=timeout)

                result_type = result.get("result_type", "N/A")
                output_preview = str(result.get("output", ""))[:200]
                self._log_loop(
                    f"round={outer_round} completed, result_type={result_type}",
                    f", output={output_preview}",
                )

                yield result

                if coordinator.is_aborted:
                    self._log_loop(f"round={outer_round} aborted")
                    break
                if controller.has_follow_up():
                    continue
                if not self._has_remaining_tasks(session):
                    self._log_loop("no remaining tasks, loop finished")
                    break

                current_query = modified.query
        finally:
            await controller.unbind_session(session)
            await controller.stop()
            self._loop_coordinator = None
            self._loop_controller = None
            self._loop_session = None

    async def _run_task_loop_invoke(
        self,
        ctx: AgentCallbackContext,
        session: Optional[Session],
    ) -> Dict[str, Any]:
        """Run the outer task loop, return last result.

        Args:
            ctx: Callback context with InvokeInputs.
            session: Current session.

        Returns:
            Result dict from the last iteration.
        """
        if session is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg=(
                    "session is required for "
                    "task-loop mode."
                ),
            )

        last_result: Dict[str, Any] = {}
        async for result in self._run_task_loop(
            ctx, session
        ):
            last_result = result
        return last_result

    async def _run_task_loop_stream(
        self,
        ctx: AgentCallbackContext,
        session: Optional[Session],
        stream_modes: Optional[List[StreamMode]],
    ) -> AsyncIterator[Any]:
        """Stream the outer task loop, yield each result.

        Args:
            ctx: Callback context with InvokeInputs.
            session: Current session.
            stream_modes: Stream mode filters.

        Yields:
            Chunks from the task loop execution.
        """
        _ = stream_modes
        if session is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg=(
                    "session is required for "
                    "task-loop mode."
                ),
            )

        async for result in self._run_task_loop(
            ctx, session
        ):
            yield result

    async def _run_single_round_stream(
        self,
        ctx: AgentCallbackContext,
        session: Optional[Session],
        stream_modes: Optional[List[StreamMode]],
    ) -> AsyncIterator[Any]:
        """Stream from inner ReActAgent exactly once."""
        modified = ctx.inputs
        if not isinstance(modified, InvokeInputs):
            raise build_error(
                StatusCode.DEEPAGENT_CONTEXT_PARAM_ERROR,
                error_msg="ctx.inputs must be InvokeInputs for single-round stream.",
            )

        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        async for chunk in self._react_agent.stream(
            self._to_effective_inputs(modified),
            session,
            stream_modes,
        ):
            yield chunk

    async def invoke(  # type: ignore[override]
        self,
        inputs: Any,
        session: Optional[Session] = None,
    ) -> Dict[str, Any]:
        """Execute DeepAgent in single-round or task-loop mode."""
        await self._ensure_initialized()

        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        invoke_inputs = self._normalize_inputs(inputs)
        ctx = AgentCallbackContext(agent=self, inputs=invoke_inputs, session=session)

        result: Dict[str, Any]
        async with ctx.lifecycle(
            AgentCallbackEvent.BEFORE_INVOKE,
            AgentCallbackEvent.AFTER_INVOKE,
        ):
            if self._deep_config is not None and self._deep_config.enable_task_loop:
                result = await self._run_task_loop_invoke(ctx, session)
            else:
                result = await self._run_single_round_invoke(ctx, session)
            invoke_inputs.result = result

        save_state(ctx)
        clear_state(ctx)
        return result

    async def stream(  # type: ignore[override]
        self,
        inputs: Any,
        session: Optional[Session] = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        """Stream execute DeepAgent in single-round or task-loop mode."""
        await self._ensure_initialized()

        if self._react_agent is None:
            raise build_error(
                StatusCode.DEEPAGENT_RUNTIME_ERROR,
                error_msg="DeepAgent not configured. Call configure() first.",
            )

        invoke_inputs = self._normalize_inputs(inputs)
        ctx = AgentCallbackContext(agent=self, inputs=invoke_inputs, session=session)

        async with ctx.lifecycle(
            AgentCallbackEvent.BEFORE_INVOKE,
            AgentCallbackEvent.AFTER_INVOKE,
        ):
            if self._deep_config is not None and self._deep_config.enable_task_loop:
                async for chunk in self._run_task_loop_stream(ctx, session, stream_modes):
                    yield chunk
            else:
                async for chunk in self._run_single_round_stream(ctx, session, stream_modes):
                    yield chunk

        save_state(ctx)
        clear_state(ctx)

    async def follow_up(
        self,
        msg: str,
        task_id: Optional[str] = None,
        session: Optional[Session] = None,
    ) -> None:
        """Publish a FollowUpEvent to the FOLLOW_UP topic.

        The event is handled concurrently with INPUT
        and does not block the current iteration.
        The handler pushes the message into
        LoopQueues.follow_up, which the outer
        loop drains after each iteration.

        Args:
            msg: Follow-up message text.
            task_id: Optional task to associate.
            session: Current session.
        """
        controller = self._loop_controller
        if controller is None:
            return
        sess = session or self._loop_session
        if sess is None:
            return
        event = FollowUpEvent.from_text(msg)
        if task_id:
            event.metadata = {"task_id": task_id}
        await controller.event_queue.publish_event_async(
            self.card.id, sess, event
        )

    async def steer(
        self,
        msg: str,
        session: Optional[Session] = None,
    ) -> None:
        """Publish a TaskInteractionEvent to TASK_INTERACTION topic.

        The event is handled concurrently with INPUT.
        The handler pushes the message into
        LoopQueues.steering, which the executor
        drains before the next inner invoke.

        Args:
            msg: Steering instruction text.
            session: Current session.
        """
        controller = self._loop_controller
        if controller is None:
            return
        sess = session or self._loop_session
        if sess is None:
            return
        from openjiuwen.core.controller.schema.dataframe import (
            TextDataFrame,
        )
        event = TaskInteractionEvent(
            interaction=[TextDataFrame(text=msg)]
        )
        await controller.event_queue.publish_event_async(
            self.card.id, sess, event
        )

    async def abort(
        self,
        session: Optional[Session] = None,
    ) -> None:
        """Request immediate abort of the task loop.

        Sets the abort flag on the coordinator and
        calls on_abort() on the event handler so the
        outer loop can exit promptly.

        Args:
            session: Current session (unused).
        """
        _ = session
        coordinator = self._loop_coordinator
        controller = self._loop_controller
        if coordinator is None or controller is None:
            return
        coordinator.request_abort()
        handler = cast(
            TaskLoopEventHandler,
            controller.event_handler,
        )
        await handler.on_abort()


__all__ = [
    "DeepAgent",
    "DeepAgentConfig",
]
