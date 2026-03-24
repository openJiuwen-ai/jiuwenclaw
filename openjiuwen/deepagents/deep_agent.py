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
from openjiuwen.core.runner import Runner
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
from openjiuwen.deepagents.rails import DeepAgentRail
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
from openjiuwen.deepagents.rails.progressive_tool_rail import ProgressiveToolRail

if TYPE_CHECKING:
    from openjiuwen.core.controller.modules.event_queue import (
        EventQueue,
    )

from openjiuwen.deepagents.prompts import (
    resolve_language,
    resolve_mode,
    PromptSection,
    SystemPromptBuilder,
)
from openjiuwen.deepagents.prompts.sections.identity import build_identity_section

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
        self.prompt_builder: Optional[SystemPromptBuilder] = None
        self.system_prompt_builder: Optional[SystemPromptBuilder] = None
        super().__init__(card)

    def configure(self, config: DeepAgentConfig) -> "DeepAgent":
        """Apply configuration and rebuild the internal ReActAgent."""
        self._deep_config = config
        self._react_agent = self._create_react_agent()
        self._pending_rails.clear()

        if config.progressive_tool_enabled:
            self._pending_rails.append(
                ProgressiveToolRail(config=config)
            )

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

        language = resolve_language(cfg.language)
        mode = resolve_mode(cfg.prompt_mode)
        prompt_builder = SystemPromptBuilder(language=language, mode=mode)
        if cfg.system_prompt:
            # Wrap the provided prompt as the identity section so all
            # rails can consistently operate on prompt_builder.
            prompt_builder.add_section(PromptSection(
                name="identity",
                content={"cn": cfg.system_prompt, "en": cfg.system_prompt},
            ))
        else:
            prompt_builder.add_section(build_identity_section(language))
        prompt = prompt_builder.build()
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

        # Store builder on both agents (same object):
        #   - DeepAgent.system_prompt_builder: rails initialized on the outer
        #     agent pick it up as the public contract.
        #   - ReActAgent.system_prompt_builder: bridged BEFORE_MODEL_CALL rails
        #     mutate the same builder object before _railed_model_call builds it.
        self.prompt_builder = prompt_builder
        self.system_prompt_builder = prompt_builder
        agent.prompt_builder = prompt_builder
        agent.system_prompt_builder = prompt_builder

        # Share ability manager so tools registered on DeepAgent are visible inside.
        agent.ability_manager = self.ability_manager

        # Inject pre-built Model to bypass lazy init.
        if cfg.model is not None:
            agent.set_llm(cfg.model)

        return agent

    async def _register_pending_mcps(self) -> None:
        """Register configured MCP servers before rails initialize."""
        cfg = self._deep_config
        if cfg is None or not cfg.mcps:
            return

        for mcp_config in cfg.mcps:
            existing_config = Runner.resource_mgr.get_mcp_server_config(mcp_config.server_id)
            if existing_config is None:
                result = await Runner.resource_mgr.add_mcp_server(
                    mcp_config,
                    tag=self.card.id,
                )
                if result.is_err():
                    raise result.msg()
            else:
                if existing_config.model_dump() != mcp_config.model_dump():
                    raise build_error(
                        StatusCode.RESOURCE_MCP_SERVER_ADD_ERROR,
                        server_config=mcp_config,
                        reason=(
                            f"server_id '{mcp_config.server_id}' is already registered "
                            "with a different config"
                        ),
                    )

                tag_result = Runner.resource_mgr.add_resource_tag(
                    mcp_config.server_id,
                    self.card.id,
                )
                if tag_result.is_err():
                    raise tag_result.msg()

                for tool_id in Runner.resource_mgr.get_mcp_tool_ids(mcp_config.server_id):
                    tag_result = Runner.resource_mgr.add_resource_tag(tool_id, self.card.id)
                    if tag_result.is_err():
                        raise tag_result.msg()

            self.ability_manager.add(mcp_config)

    async def _ensure_initialized(self) -> None:
        """Perform lazy async initialization."""
        if self._initialized:
            return

        await self._register_pending_mcps()

        for rail_inst in self._pending_rails:
            if isinstance(rail_inst, DeepAgentRail):
                rail_inst.set_sys_operation(self._deep_config.sys_operation)
                rail_inst.set_workspace(self._deep_config.workspace)
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
        if isinstance(rail, DeepAgentRail):
            rail.set_sys_operation(self.deep_config.sys_operation)
            rail.set_workspace(self.deep_config.workspace)
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
        """Stream the outer task loop with per-token chunks.

        Uses the same coroutine pattern as ReActAgent.stream():
        background task runs _run_task_loop (which triggers
        invoke with _streaming=True), foreground reads from
        session.stream_iterator().

        Session lifecycle (pre_run/post_run) is managed by the
        caller (Runner or direct caller), not by this method.
        Only the stream emitter is closed here to send END_FRAME
        and unblock stream_iterator().

        Args:
            ctx: Callback context with InvokeInputs.
            session: Current session.
            stream_modes: Stream mode filters.

        Yields:
            OutputSchema chunks (llm_reasoning / llm_output / answer).
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

        import asyncio

        async def _stream_process() -> None:
            try:
                async for result in self._run_task_loop(ctx, session):
                    await self._write_round_result_to_stream(result, session)
            except Exception as e:
                logger.error(f"Task loop stream error: {e}")
            finally:
                # Only close the stream emitter to send END_FRAME,
                # so stream_iterator() can terminate.
                # Full session cleanup (post_run) is left to the caller.
                await session.close_stream()

        task = asyncio.create_task(_stream_process())

        async for chunk in session.stream_iterator():
            yield chunk

        await task

    @staticmethod
    async def _write_round_result_to_stream(
        result: Dict[str, Any],
        session: Session,
    ) -> None:
        """Write a task-loop round result as an answer chunk."""
        from openjiuwen.core.session.stream.base import OutputSchema
        await session.write_stream(OutputSchema(
            type="answer",
            index=0,
            payload={
                "output": result.get("output", ""),
                "result_type": result.get("result_type", ""),
            },
        ))

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
