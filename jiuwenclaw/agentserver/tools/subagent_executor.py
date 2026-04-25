# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Fork Agent executor - Creates fork agents with inherited context for DeepAgent architecture."""

from __future__ import annotations

import asyncio
from contextvars import ContextVar, Token
from typing import TYPE_CHECKING, Any, Optional, Union

from openjiuwen.core.runner import Runner
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema
from openjiuwen.core.context_engine.base import ModelContext
from openjiuwen.core.single_agent import AgentCard, BaseAgent
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    ToolCallInputs,
)
from openjiuwen.harness.workspace.workspace import Workspace
from openjiuwen.harness import DeepAgent
from openjiuwen.harness.factory import create_deep_agent
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.core.foundation.llm import Model

from jiuwenclaw.agentserver.tools.subagent_models import (
    ForkAgentResult,
    ForkAgentTaskSpec,
    SubagentResult,
    SubagentTaskSpec,
)
from jiuwenclaw.agentserver.deep_agent.prompt_builder import build_subagent_base_prompt
from jiuwenclaw.utils import get_agent_root_dir, logger
from jiuwenclaw.config import get_config

if TYPE_CHECKING:
    pass


# Context variable to pass parent session from tool execution to executor
_subagent_parent_session: ContextVar[Optional[Session]] = ContextVar(
    "subagent_parent_session", default=None
)

# Context variable to pass current agent's context for fork context retrieval
# This stores the actual ModelContext object (not agent) to get current messages
_current_agent_context: ContextVar[Optional[ModelContext]] = ContextVar(
    "current_agent_context", default=None
)

# Context variable to store current agent's subagent_id (for nested session_id)
# Used when subagent creates fork_agent: fork's session_id includes parent subagent's id
# Format: "subagent_1222fc63" or "fork_295a9e7" (the suffix part of session_id)
_current_agent_subagent_id: ContextVar[Optional[str]] = ContextVar(
    "current_agent_subagent_id", default=None
)


class ForkMessageInjectionRail(DeepAgentRail):
    """Rail that injects fork_messages into agent's context.

    Key insight: ctx.context is None at before_invoke time (context is created
    inside lifecycle after BEFORE_INVOKE triggers). Also, DeepAgent and ReActAgent
    use separate ctx objects, so ctx.extra cannot pass data across agents.

    Solution: Use before_model_call to inject messages when ctx.context is available.
    The fork_messages are stored in self._fork_messages (rail instance field) and
    accessed directly without relying on ctx.extra.

    Message Order Fix:
    - ReActAgent adds UserMessage(query) BEFORE before_model_call triggers
    - ForkMessageInjectionRail injects fork_messages AFTER query is added
    - This results in wrong order: [query, fork_messages]
    - Correct order should be: [fork_messages, query]
    - Fix: Remove last UserMessage, inject fork_messages, re-add the query message

    Note: before_model_call is a BRIDGE_EVENT, registered on ReActAgent where
    the context is actually created and available.
    """

    priority = 5  # Execute before other rails

    def __init__(self, fork_messages: list[Any]) -> None:
        """Initialize with fork messages to inject.

        Args:
            fork_messages: Messages from parent agent to inject into fork agent's context
        """
        super().__init__()
        self._fork_messages = fork_messages
        self._injected = False  # Flag to ensure single injection

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Inject fork_messages into ctx.context with correct ordering."""
        # Only inject once, and only if context is available
        if self._injected or ctx.context is None or not self._fork_messages:
            return

        from openjiuwen.core.foundation.llm import UserMessage

        # Get current messages to check ordering
        messages = ctx.context.get_messages()

        # Find and remove the last UserMessage (the query added by ReActAgent)
        # This is needed to ensure correct ordering: [fork_messages, query]
        last_user_msg = None
        if messages and isinstance(messages[-1], UserMessage):
            last_user_msg = messages[-1]
            # Pop all messages and rebuild without the last UserMessage
            ctx.context.pop_messages(len(messages))
            # Re-add all except the last UserMessage
            for msg in messages[:-1]:
                await ctx.context.add_messages(msg)
            logger.debug(
                f"[ForkInjectionRail] Removed last UserMessage for reordering"
            )

        # Inject fork_messages (historical context from parent agent)
        for msg in self._fork_messages:
            await ctx.context.add_messages(msg)
        logger.info(
            f"[ForkInjectionRail] Injected {len(self._fork_messages)} fork messages into context"
        )

        # Re-add the query UserMessage if we removed it
        if last_user_msg is not None:
            await ctx.context.add_messages(last_user_msg)
            logger.debug(
                f"[ForkInjectionRail] Re-added query UserMessage after fork_messages"
            )

        self._injected = True  # Mark as injected to avoid re-injection


class SubagentContextRail(DeepAgentRail):
    """Minimal rail for spawn/fork agent to set context for nested fork_agent calls.

    This rail handles:
    - Context variable setup for fork_agent inheritance
    - Stream event emission for tool calls (so frontend can see subagent's tool execution)
    - before_tool_call: sets _current_agent_context and _subagent_parent_session, emits tool_call event
    - after_tool_call: clears context variables, emits tool_result event

    Does NOT handle pause/abort (those are handled by parent agent's JiuClawStreamEventRail).

    IMPORTANT: ctx.session is the Session created by Runner.run_agent internally, NOT the
    SubagentSessionProxy we pass to Runner. So we must store parent_session in the rail
    and use it directly for event emission.
    """

    priority = 80

    def __init__(
        self,
        subagent_id: str | None = None,
        parent_session: Session | None = None,
    ) -> None:
        """Initialize with optional subagent_id and parent_session for event forwarding.

        Args:
            subagent_id: The subagent_id of this agent (e.g., "subagent_1222fc63" or "fork_295a9e7")
            parent_session: The parent session to forward events to (NOT ctx.session which is internal)
        """
        super().__init__()
        self._subagent_id = subagent_id
        self._parent_session = parent_session  # Store parent session for event emission

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Set context variables for fork_agent to get correct messages and emit tool_call event."""
        # Set current agent context for fork_agent to get correct messages
        # This ensures fork_agent gets messages from the running context
        # (not from storage which may be empty for subagent scenarios)
        if ctx.context is not None:
            set_current_agent_context(ctx.context)

        # Set subagent_id for nested session_id construction
        if self._subagent_id:
            set_current_agent_subagent_id(self._subagent_id)

        # CRITICAL: Set parent session for nested subagent/fork tools
        # Use self._parent_session (SubagentSessionProxy) NOT ctx.session (Runner's internal Session)
        # This ensures fork_agent created inside spawn can forward events correctly
        if self._parent_session is not None:
            set_subagent_parent_session(self._parent_session)
        elif ctx.session is not None:
            # Fallback: only if no parent_session stored, use ctx.session
            actual_session = getattr(ctx.session, '_parent', ctx.session) if ctx.session else None
            set_subagent_parent_session(actual_session)

        # Emit tool_call event using stored parent_session (NOT ctx.session)
        # ctx.session is Runner's internal session, not our SubagentSessionProxy
        if self._parent_session is not None and isinstance(ctx.inputs, ToolCallInputs):
            tc = ctx.inputs.tool_call
            await self._emit_tool_call(self._parent_session, tc)
            await self._emit_tool_update(self._parent_session, tc, status="in_progress")

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        """Clear context after tool execution and emit tool_result event."""
        # Emit tool_result event using stored parent_session
        if self._parent_session is not None and isinstance(ctx.inputs, ToolCallInputs):
            tc = ctx.inputs.tool_call
            result = ctx.inputs.tool_result
            await self._emit_tool_result(self._parent_session, tc, result)

        # Clear context variables
        set_current_agent_context(None)
        set_current_agent_subagent_id(None)
        set_subagent_parent_session(None)

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        """Clear context on exception."""
        set_current_agent_context(None)
        set_current_agent_subagent_id(None)
        set_subagent_parent_session(None)

    # Static methods for emitting stream events (same as JiuClawStreamEventRail)

    @staticmethod
    async def _emit_tool_call(session: Session, tool_call: Any) -> None:
        """Emit tool_call event for frontend display."""
        try:
            await session.write_stream(
                OutputSchema(
                    type="tool_call",
                    index=0,
                    payload={
                        "tool_call": {
                            "name": getattr(tool_call, "name", ""),
                            "arguments": getattr(tool_call, "arguments", {}),
                            "tool_call_id": getattr(tool_call, "id", ""),
                        }
                    },
                )
            )
        except Exception:
            logger.debug("[SubagentContextRail] tool_call emit failed", exc_info=True)

    @staticmethod
    async def _emit_tool_update(session: Session, tool_call: Any, *, status: str) -> None:
        """Emit tool_update event for frontend display."""
        try:
            await session.write_stream(
                OutputSchema(
                    type="tool_update",
                    index=0,
                    payload={
                        "tool_update": {
                            "tool_name": getattr(tool_call, "name", "") if tool_call else "",
                            "tool_call_id": getattr(tool_call, "id", "") if tool_call else "",
                            "arguments": getattr(tool_call, "arguments", {}) if tool_call else {},
                            "status": str(status or "").strip() or "in_progress",
                        }
                    },
                )
            )
        except Exception:
            logger.debug("[SubagentContextRail] tool_update emit failed", exc_info=True)

    @staticmethod
    async def _emit_tool_result(session: Session, tool_call: Any, result: Any) -> None:
        """Emit tool_result event for frontend display."""
        try:
            # Handle structured result payload (same as JiuClawStreamEventRail)
            raw_output = None
            if isinstance(result, (dict, list)):
                raw_output = result

            tool_result_payload = {
                "tool_name": getattr(tool_call, "name", "") if tool_call else "",
                "tool_call_id": getattr(tool_call, "id", "") if tool_call else "",
                "result": str(result)[:1000] if result is not None else "",
            }
            if raw_output is not None:
                tool_result_payload["raw_output"] = raw_output

            await session.write_stream(
                OutputSchema(
                    type="tool_result",
                    index=0,
                    payload={
                        "tool_result": tool_result_payload
                    },
                )
            )
        except Exception:
            logger.debug("[SubagentContextRail] tool_result emit failed", exc_info=True)


def set_subagent_parent_session(session: Optional[Session]) -> None:
    """Set the parent session context for subagent execution."""
    _subagent_parent_session.set(session)


def get_subagent_parent_session() -> Optional[Session]:
    """Get the parent session from context."""
    return _subagent_parent_session.get()


def set_current_agent_context(context: Optional[ModelContext]) -> None:
    """Set the current agent's context for fork context retrieval.

    This stores the actual ModelContext object, so fork_agent can get messages
    directly from the running context (not from storage which may be empty for subagent).

    Args:
        context: ModelContext object from agent's invoke loop
    """
    _current_agent_context.set(context)


def get_current_agent_context() -> Optional[ModelContext]:
    """Get the current agent's context from context variable.

    Returns the ModelContext that is currently in use (main agent or spawn subagent).
    Used by fork_agent to get fork messages from the correct running context.
    """
    return _current_agent_context.get()


def set_current_agent_subagent_id(subagent_id: Optional[str]) -> None:
    """Set the current agent's subagent_id for nested session_id construction.

    This stores the suffix part of session_id (e.g., "subagent_1222fc63" or "fork_295a9e7").
    Used when subagent creates fork_agent to construct nested session_id.

    Args:
        subagent_id: The subagent_id of the current agent (suffix of session_id)
    """
    _current_agent_subagent_id.set(subagent_id)


def get_current_agent_subagent_id() -> Optional[str]:
    """Get the current agent's subagent_id from context variable.

    Returns the suffix part of session_id for the current agent.
    Used to construct nested session_id when creating fork from subagent.
    """
    return _current_agent_subagent_id.get()


def _get_llm_trace_session_id_var() -> ContextVar[str]:
    """Get LLM trace session ID context var (lazy import to avoid circular dependency)."""
    from jiuwenclaw.agentserver.deep_agent.interface_deep import _LLM_TRACE_SESSION_ID
    return _LLM_TRACE_SESSION_ID


class SubagentSessionProxy:
    """
    Session proxy that forwards execution events to parent session.

    Forward tool calls and thinking process, suppress user-facing messages.
    Fork agent's tool execution is shown alongside main Agent's fork_agent call info.

    Supports nested session_id for trace hierarchy:
    - Main agent: sess_xxx
    - Subagent from main: sess_xxx_subagent_1222fc63
    - Fork from subagent: sess_xxx_subagent_1222fc63_fork_295a9e7
    """

    # Event types to forward (tool execution + thinking process + permission requests)
    # Include tool_update for showing tool execution status (in_progress, etc.)
    FORWARD_TYPES = {
        "tool_call", "tool_result", "tool_update",
        "thinking", "llm_reasoning",
        "retry_notification", "chat.ask_user_question",
        "context.compressed",  # Context compression info
    }
    # Event types to suppress (user-facing messages only)
    SUPPRESS_TYPES = {"answer", "complete", "start"}

    def __init__(
        self,
        parent_session: Session,
        subagent_id: str,
        role_id: str,
    ) -> None:
        self._parent = parent_session
        self._subagent_id = subagent_id
        self._role_id = role_id
        # Construct nested session_id for trace hierarchy
        # Simply append subagent_id to parent_session's session_id
        # Works for both direct (parent is main session) and nested (parent is SubagentSessionProxy)
        # Examples:
        #   Direct: parent = "sess_xxx", subagent_id = "subagent_xxx" → "sess_xxx_subagent_xxx"
        #   Nested: parent = "sess_xxx_subagent_xxx", subagent_id = "fork_xxx" → "sess_xxx_subagent_xxx_fork_xxx"
        self._session_id = f"{parent_session.get_session_id()}_{subagent_id}"

    async def write_stream(self, data: Union[dict, OutputSchema]) -> None:
        """Forward tool execution events, suppress user-facing messages."""
        event_type = None
        output_data = None

        if isinstance(data, OutputSchema):
            event_type = data.type
            output_data = data
        elif isinstance(data, dict):
            event_type = data.get("type", "unknown")
            output_data = OutputSchema(
                type=event_type,
                index=data.get("index", 0),
                payload=data.get("payload", {}),
            )

        # Only forward tool execution events
        if event_type in self.FORWARD_TYPES:
            await self._parent.write_stream(output_data)
        elif event_type in self.SUPPRESS_TYPES:
            logger.debug(f"[SubagentSession] Suppressed event: {event_type}")
        else:
            # Unknown event type - forward by default for debugging
            logger.debug(f"[SubagentSession] Forwarding unknown event: {event_type}")
            await self._parent.write_stream(output_data)

    def get_session_id(self) -> str:
        """Return composite session ID."""
        return self._session_id

    def get_env(self, key: str, default: Any = None) -> Any:
        """Proxy to parent session."""
        return self._parent.get_env(key, default)

    def get_envs(self) -> dict:
        """Proxy to parent session."""
        return self._parent.get_envs()

    def update_state(self, data: dict) -> None:
        """Proxy to parent session."""
        return self._parent.update_state(data)

    def get_state(self, key: Union[str, list, dict] = None) -> Any:
        """Proxy to parent session."""
        return self._parent.get_state(key)

    async def write_custom_stream(self, data: dict) -> None:
        """Forward custom stream (typically not user-facing, pass through)."""
        await self._parent.write_custom_stream(data)

    def __getattr__(self, name: str) -> Any:
        """Fallback: proxy any other attributes to parent session."""
        return getattr(self._parent, name)

    def get_parent_session(self) -> Session:
        """Return the underlying parent session.

        Useful when tools need the actual session (not the proxy).
        """
        return self._parent


class ForkAgentExecutor:
    """
    Fork agent executor for DeepAgent architecture.

    Uses Runner.run_agent(context=...) to pass fork_messages instead of
    modifying invoke method (which is inside DeepAgent SDK).
    """

    def __init__(
        self,
        parent_agent: DeepAgent,
        model: Model,
        default_role_prompts: dict[str, str] | None = None,
    ) -> None:
        """
        Initialize the subagent executor.

        Args:
            parent_agent: Parent DeepAgent instance (for inheriting tools)
            model: Model instance for creating subagents
            default_role_prompts: Default role prompts (used when role_id not found)
        """
        self._parent_agent = parent_agent
        self._model = model
        self._default_role_prompts = default_role_prompts or {}
        self._active_fork_agents: dict[str, Any] = {}  # task_id -> subagent instance

    def resolve_permission_approval(self, request_id: str, answers: list) -> bool:
        """
        Resolve permission approval across all active fork agents.

        Called by parent agent when it cannot find the request_id in its own pending approvals.

        Args:
            request_id: Permission approval request ID
            answers: User's answers from the approval UI

        Returns:
            True if resolved successfully, False otherwise
        """
        for task_id, fork_agent in self._active_fork_agents.items():
            # Use getattr to call the internal method (works with protected member)
            resolve_method = getattr(fork_agent, "_resolve_permission_approval", None)
            if resolve_method is not None:
                try:
                    resolved = resolve_method(request_id, answers)
                    if resolved:
                        logger.info(
                            f"[ForkAgent] Resolved permission approval in "
                            f"task_id={task_id}, request_id={request_id}"
                        )
                        return True
                except Exception as e:
                    logger.warning(
                        f"[ForkAgent] Failed to resolve permission approval "
                        f"in task_id={task_id}: {e}"
                    )
        return False

    def _get_role_definition(self, role_id: str) -> Any | None:
        """Get role definition from default_role_prompts.

        Args:
            role_id: Role ID to lookup

        Returns:
            SubagentRoleDefinition or None (triggers dynamic generation)
        """
        from jiuwenclaw.agentserver.tools.subagent_models import SubagentRoleDefinition

        if role_id in self._default_role_prompts:
            return SubagentRoleDefinition(
                name=role_id,
                system_prompt=self._default_role_prompts[role_id],
            )

        return None

    @staticmethod
    def _generate_dynamic_role_prompt(role_id: str) -> str:
        """
        Generate dynamic role prompt based on role name.

        Triggered when: User specifies a role that's not predefined in
        Skill frontmatter or default roles.
        Examples: "Java架构师", "数据分析师", etc.

        Args:
            role_id: User-specified role name

        Returns:
            Role-specific prompt (will be appended to build_subagent_base_prompt)
        """
        config_base = get_config()
        language = config_base.get("preferred_language", "zh")

        # Return only role-specific prompt (base_prompt is handled in _build_spawn_agent_config)
        if language == "zh":
            return f"""你是一个 {role_id}。

以该领域的专业知识和最佳实践执行任务。你的职责包括：
- 运用领域特定的知识和最佳实践
- 提供结构化、有理据的分析和建议
- 以该领域专家应有的精确度执行任务

系统化地处理每个任务，交付高质量的结果。
"""
        else:
            return f"""You are a {role_id}.

Act with expertise and professionalism in this domain. Your responsibilities include:
- Applying domain-specific knowledge and best practices
- Providing structured, well-reasoned analysis and recommendations
- Executing tasks with the precision expected of an expert in this field

Approach each task methodically and deliver high-quality results.
"""

    async def execute_fork(
        self,
        task: ForkAgentTaskSpec,
        fork_messages: list[Any],
        parent_session: Session | None = None,
    ) -> ForkAgentResult:
        """
        Execute a fork agent task with inherited messages.

        Key mechanism:
        - ForkMessageInjectionRail injects fork_messages at before_model_call hook
        - This works around Runner.run_agent ignoring the context parameter

        Args:
            task: Fork agent task specification
            fork_messages: Messages from parent Agent context to inherit
            parent_session: Optional parent session for event forwarding
        """
        if parent_session is None:
            parent_session = get_subagent_parent_session()

        try:
            # 1. Create session proxy FIRST (needed for SubagentContextRail to emit events)
            session_proxy: SubagentSessionProxy | None = None
            if parent_session is not None:
                session_proxy = SubagentSessionProxy(
                    parent_session=parent_session,
                    subagent_id=task.task_id,
                    role_id=task.role_id,
                )

            # 2. Create fork agent with fork_messages injection rail - pass session_proxy for event forwarding
            fork_agent = await self._create_fork_agent(task, fork_messages, parent_session=session_proxy)

            # 3. Build full prompt
            full_prompt = task.objective
            if task.prompt:
                full_prompt = f"{task.objective}\n\n{task.prompt}"

            logger.info(
                f"[ForkAgent] Starting execution, task_id={task.task_id}, role_id={task.role_id}, "
                f"inherited_messages={len(fork_messages)}"
            )

            # 4. Register active agent for permission approval resolution
            self._active_fork_agents[task.task_id] = fork_agent

            # 5. Set session_id for LLM IO trace logging
            if session_proxy:
                trace_session_id = session_proxy.get_session_id()
            else:
                trace_session_id = task.task_id
            llm_trace_var = _get_llm_trace_session_id_var()
            token_trace_sid = llm_trace_var.set(trace_session_id)

            # 6. Execute fork agent
            session_id = task.session_id or task.task_id
            invoke_inputs = {"query": full_prompt, "conversation_id": session_id}

            try:
                # ForkMessageInjectionRail handles message injection via before_model_call hook
                response = await Runner.run_agent(
                    agent=fork_agent,
                    inputs=invoke_inputs,
                    session=session_proxy,
                )
            finally:
                self._active_fork_agents.pop(task.task_id, None)
                llm_trace_var.reset(token_trace_sid)

            logger.info(f"[ForkAgent] Execution completed, task_id={task.task_id}")

            # 7. Extract result and usage
            result_text = ""
            fork_usage = None
            if isinstance(response, dict):
                result_text = response.get("output", "")
                if isinstance(result_text, dict):
                    result_text = result_text.get("output", str(result_text))
                fork_usage = response.get("usage")
            elif hasattr(response, "content"):
                result_text = response.content
            elif hasattr(response, "text"):
                result_text = response.text
            else:
                result_text = str(response)

            if fork_usage:
                logger.info(
                    f"[ForkAgent] task_id={task.task_id} usage: {fork_usage}"
                )

            return ForkAgentResult(
                success=True,
                task_id=task.task_id,
                role_id=task.role_id,
                result=result_text,
                usage=fork_usage,
            )

        except asyncio.TimeoutError:
            logger.warning(
                f"[ForkAgent] Timeout after {task.timeout_seconds} seconds, task_id={task.task_id}"
            )
            return ForkAgentResult(
                success=False,
                task_id=task.task_id,
                role_id=task.role_id,
                error=f"Timeout after {task.timeout_seconds} seconds",
            )
        except Exception as e:
            logger.exception(f"[ForkAgent] Execution failed: {e}")
            return ForkAgentResult(
                success=False,
                task_id=task.task_id,
                role_id=task.role_id,
                error=str(e),
            )

    async def execute_spawn(
        self,
        task: SubagentTaskSpec,
        parent_session: Session | None = None,
    ) -> SubagentResult:
        """
        Execute a spawn subagent task with isolated context.

        Key differences from execute_fork:
        - Uses Runner.run_agent(session=None) for isolated context
        - No fork_messages passed (fresh context)
        - Supports role definition lookup and dynamic role generation

        Args:
            task: SubagentTaskSpec - Spawn task specification
            parent_session: Optional parent session for event forwarding
        """
        if parent_session is None:
            parent_session = get_subagent_parent_session()

        try:
            # 1. Get role definition
            role_def = self._get_role_definition(task.role_id)

            # 2. Determine system_prompt (priority: call param > role def > dynamic generation)
            if task.system_prompt:
                system_prompt = task.system_prompt
            elif role_def and hasattr(role_def, 'system_prompt') and role_def.system_prompt:
                system_prompt = role_def.system_prompt
            else:
                system_prompt = self._generate_dynamic_role_prompt(task.role_id)
                logger.info(
                    f"[SpawnAgent] Generated dynamic role prompt for: {task.role_id}"
                )

            # 3. Create session proxy FIRST (needed for SubagentContextRail to emit events)
            session_proxy: SubagentSessionProxy | None = None
            if parent_session is not None:
                # task.task_id already contains prefix (e.g., "subagent_xxx")
                session_proxy = SubagentSessionProxy(
                    parent_session=parent_session,
                    subagent_id=task.task_id,  # Already has prefix like "subagent_xxx"
                    role_id=task.role_id,
                )

            # 4. Create spawn agent (DeepAgent instance) - pass session_proxy for event forwarding
            spawn_agent = await self._create_spawn_agent(task, system_prompt, parent_session=session_proxy)

            # 5. Build full prompt
            full_prompt = task.objective
            if task.prompt:
                full_prompt = f"{task.objective}\n\n{task.prompt}"

            logger.info(
                f"[SpawnAgent] Starting execution, task_id={task.task_id}, role_id={task.role_id}"
            )

            # 6. Register active agent for permission approval resolution
            self._active_fork_agents[task.task_id] = spawn_agent

            # 7. Set session_id for LLM IO trace logging
            if session_proxy:
                trace_session_id = session_proxy.get_session_id()
            else:
                trace_session_id = task.task_id  # Already has prefix like "subagent_xxx"
            llm_trace_var = _get_llm_trace_session_id_var()
            token_trace_sid = llm_trace_var.set(trace_session_id)

            # 8. Execute with isolated context (key: no context passed)
            session_id = task.session_id or task.task_id
            invoke_inputs = {"query": full_prompt, "conversation_id": session_id}

            try:
                # Key difference from execute_fork: no context passed
                # Runner will create new isolated context
                response = await Runner.run_agent(
                    agent=spawn_agent,
                    inputs=invoke_inputs,
                    session=session_proxy,  # session for event forwarding
                    # context=None ← Not passed, Runner creates new isolated context
                )
            finally:
                self._active_fork_agents.pop(task.task_id, None)
                llm_trace_var.reset(token_trace_sid)

            logger.info(f"[SpawnAgent] Execution completed, task_id={task.task_id}")

            # 8. Extract result and usage
            result_text = ""
            spawn_usage = None
            if isinstance(response, dict):
                result_text = response.get("output", "")
                if isinstance(result_text, dict):
                    result_text = result_text.get("output", str(result_text))
                spawn_usage = response.get("usage")
            elif hasattr(response, "content"):
                result_text = response.content
            elif hasattr(response, "text"):
                result_text = response.text
            else:
                result_text = str(response)

            if spawn_usage:
                logger.info(
                    f"[SpawnAgent] task_id={task.task_id} usage: {spawn_usage}"
                )

            return SubagentResult(
                success=True,
                task_id=task.task_id,
                role_id=task.role_id,
                result=result_text,
                usage=spawn_usage,
            )

        except asyncio.TimeoutError:
            logger.warning(
                f"[SpawnAgent] Timeout after {task.timeout_seconds} seconds, task_id={task.task_id}"
            )
            return SubagentResult(
                success=False,
                task_id=task.task_id,
                role_id=task.role_id,
                error=f"Timeout after {task.timeout_seconds} seconds",
            )
        except Exception as e:
            logger.exception(f"[SpawnAgent] Execution failed: {e}")
            return SubagentResult(
                success=False,
                task_id=task.task_id,
                role_id=task.role_id,
                error=str(e),
            )

    async def _create_spawn_agent(
        self,
        task: SubagentTaskSpec,
        system_prompt: str,
        parent_session: Session | None = None,
    ) -> DeepAgent:
        """Create spawn agent (DeepAgent instance) with isolated context.

        Key differences from _create_fork_agent:
        - No fork_messages (isolated context)
        - Supports allowed_tools restriction
        - Uses different tool inheritance (not excluding fork_agent)

        Args:
            task: SubagentTaskSpec
            system_prompt: System prompt for the agent
            parent_session: Parent session for event forwarding (passed to SubagentContextRail)

        Returns:
            DeepAgent instance for spawn subagent
        """
        # Use task.workspace_dir or get from parent agent's deep_config
        ws = task.workspace_dir
        if ws is None:
            # Try to get workspace from parent agent's config
            parent_config = getattr(self._parent_agent, 'deep_config', None)
            if parent_config and hasattr(parent_config, 'workspace'):
                parent_ws = getattr(parent_config.workspace, 'root_path', None)
                if parent_ws:
                    ws = parent_ws
        if ws is None:
            # Fallback to default agent root dir
            ws = get_agent_root_dir()

        config_base = get_config()
        language = config_base.get("preferred_language", "zh")

        # Use build_subagent_base_prompt as base for safety rules
        # Use the actual workspace_dir for the prompt
        base_prompt = build_subagent_base_prompt(
            language=language,
            workspace_dir=ws,
            include_time=True,
        )

        # Append role-specific prompt
        augmented_prompt = base_prompt + "\n\n---\n\n# Subagent Role\n\n" + system_prompt

        # Build agent card for spawn agent
        card = AgentCard(
            name=f"spawn_{task.role_id}",
            id=task.task_id,
        )

        # Build workspace for spawn agent
        workspace_obj = Workspace(
            root_path=ws,
            language=language,
        )

        # Create DeepAgent instance using create_deep_agent
        # Use SubagentContextRail for nested fork context inheritance and event forwarding
        spawn_agent = create_deep_agent(
            model=self._model,
            card=card,
            system_prompt=augmented_prompt,
            max_iterations=config_base.get("max_iterations", 15),
            workspace=workspace_obj,
            rails=[SubagentContextRail(subagent_id=task.task_id, parent_session=parent_session)],
            language=language,
            enable_task_loop=False,  # Subagent doesn't need task loop
        )

        # Inherit tools from parent agent (allow fork_agent for nested scenarios)
        self._inherit_tools_for_spawn(spawn_agent, task.allowed_tools)

        logger.info(f"[SpawnAgent] Created spawn agent instance, task_id={task.task_id}")

        return spawn_agent

    def _inherit_tools_for_spawn(
        self,
        spawn_agent: DeepAgent,
        allowed_tools: tuple[str, ...] | None = None,
    ) -> None:
        """
        Inherit tools from parent agent for spawn agent.

        IMPORTANT: Does NOT exclude fork_agent, allowing spawn to call fork.
        fork_agent in spawn will inherit spawn's context (not main agent's).

        Excludes:
        - spawn_subagent (prevent recursive spawning)
        - todo_* tools (parent agent's task tracking)
        - office_claw_*_skills (skill loading is parent's capability)

        Args:
            spawn_agent: Spawn agent to inherit tools
            allowed_tools: Optional subset of tools to restrict to
        """
        # Note: fork_agent is NOT excluded, allowing spawn -> fork nesting
        excluded_tools = {
            "spawn_subagent",  # Prevent recursive spawning
            "todo_create",
            "todo_complete",
            "todo_insert",
            "todo_remove",
            "todo_list",
            "office_claw_list_skills",
            "office_claw_load_skill",
        }

        try:
            # Get parent's tools
            parent_tools = self._parent_agent.ability_manager.list()
            if not parent_tools:
                logger.debug("[SpawnAgent] Parent agent has no tools to inherit")
                return

            inherited_count = 0
            for tool in parent_tools:
                try:
                    tool_name = getattr(tool, "name", None)
                    if hasattr(tool, "card") and hasattr(tool.card, "name"):
                        tool_name = tool.card.name

                    # Skip excluded tools
                    if tool_name in excluded_tools:
                        logger.debug(f"[SpawnAgent] Skipping excluded tool: {tool_name}")
                        continue

                    # Skip tools not in allowed_tools (if specified)
                    if allowed_tools and tool_name not in allowed_tools:
                        logger.debug(f"[SpawnAgent] Skipping tool not in allowed_tools: {tool_name}")
                        continue

                    if hasattr(tool, "card"):
                        spawn_agent.ability_manager.add(tool.card)
                    else:
                        spawn_agent.ability_manager.add(tool)
                    inherited_count += 1
                except Exception as e:
                    logger.debug(
                        f"[SpawnAgent] Failed to inherit tool {getattr(tool, 'name', 'unknown')}: {e}"
                    )

            logger.info(
                f"[SpawnAgent] Inherited {inherited_count} tools from parent agent (fork_agent allowed)"
            )
        except Exception as e:
            logger.warning(f"[SpawnAgent] Failed to inherit tools: {e}")

    async def _create_fork_agent(
        self,
        task: ForkAgentTaskSpec,
        fork_messages: list[Any],  # Messages to inherit from parent
        parent_session: Session | None = None,
    ) -> DeepAgent:
        """Create fork agent (DeepAgent instance) with inherited messages.

        Key mechanism:
        - ForkMessageInjectionRail injects fork_messages at before_model_call hook
        - This works around Runner.run_agent ignoring the context parameter

        Args:
            task: Fork agent task specification
            fork_messages: Messages from parent agent to inherit
            parent_session: Parent session for event forwarding (passed to SubagentContextRail)

        Returns:
            DeepAgent instance configured with message injection rail
        """
        # Use task.workspace_dir FIRST (highest priority)
        ws = task.workspace_dir
        ws_source = "task.workspace_dir"
        logger.debug(
            f"[ForkAgent] Initial ws={ws}, task.workspace_dir={task.workspace_dir}"
        )

        if ws is None:
            parent_config = getattr(self._parent_agent, 'deep_config', None)
            if parent_config and hasattr(parent_config, 'workspace'):
                parent_ws = getattr(parent_config.workspace, 'root_path', None)
                if parent_ws:
                    ws = parent_ws
                    ws_source = "parent_config.workspace.root_path"
                    logger.debug(f"[ForkAgent] Using parent workspace: {parent_ws}")
        if ws is None:
            ws = get_agent_root_dir()
            ws_source = "get_agent_root_dir()"
            logger.debug(f"[ForkAgent] Using default agent root dir: {ws}")

        logger.info(
            f"[ForkAgent] Final workspace_dir={ws}, source={ws_source}"
        )

        config_base = get_config()
        language = config_base.get("preferred_language", "zh")

        base_prompt = build_subagent_base_prompt(
            language=language,
            workspace_dir=ws,
            include_time=True,
        )

        # Append fork agent role
        if language == "zh":
            role_prompt = f"""---

# Fork Agent Role

你是一个 AI 助手的 fork 子代理，角色为 {task.role_id}。
你继承了父代理的上下文，专门执行父代理分派的特定任务。
使用继承的上下文和可用工具执行给定任务。
"""
        else:
            role_prompt = f"""---

# Fork Agent Role

You are a fork subagent of an AI assistant, with role {task.role_id}.
You inherit parent agent's context and execute tasks assigned by the parent agent.
Execute the given task using inherited context and available tools.
"""

        augmented_prompt = base_prompt + role_prompt

        card = AgentCard(
            name=f"fork_{task.role_id}",
            id=task.task_id,
        )

        workspace_obj = Workspace(
            root_path=ws,
            language=language,
        )

        # Create DeepAgent with ForkMessageInjectionRail for context inheritance
        fork_agent = create_deep_agent(
            model=self._model,
            card=card,
            system_prompt=augmented_prompt,
            max_iterations=config_base.get("max_iterations", 15),
            workspace=workspace_obj,
            rails=[
                ForkMessageInjectionRail(fork_messages),  # Inject inherited messages
                SubagentContextRail(subagent_id=task.task_id, parent_session=parent_session),  # Event forwarding
            ],
            language=language,
            enable_task_loop=False,
        )

        # Inherit tools from parent agent, excluding fork_agent
        self._inherit_tools_for_fork(fork_agent, task.allowed_tools)

        logger.info(f"[ForkAgent] Created fork agent instance, task_id={task.task_id}")

        return fork_agent

    def _inherit_tools_for_fork(
        self,
        fork_agent: DeepAgent,
        allowed_tools: tuple[str, ...] | None = None,
    ) -> None:
        """
        Inherit tools from parent agent for fork agent.

        Excludes fork_agent to prevent recursive forking.
        Optionally restricts to allowed_tools subset.
        """
        excluded_tools = {
            "fork_agent",  # Prevent recursive forking
            "spawn_subagent",  # Prevent spawning new subagents from fork
            "todo_create",
            "todo_complete",
            "todo_insert",
            "todo_remove",
            "todo_list",
            "office_claw_list_skills",
            "office_claw_load_skill",
        }

        try:
            # Get parent's tools
            parent_tools = self._parent_agent.ability_manager.list()
            if not parent_tools:
                logger.debug("[ForkAgent] Parent agent has no tools to inherit")
                return

            inherited_count = 0
            for tool in parent_tools:
                try:
                    tool_name = getattr(tool, "name", None)
                    if hasattr(tool, "card") and hasattr(tool.card, "name"):
                        tool_name = tool.card.name

                    # Skip excluded tools
                    if tool_name in excluded_tools:
                        logger.debug(f"[ForkAgent] Skipping excluded tool: {tool_name}")
                        continue

                    # Skip tools not in allowed_tools (if specified)
                    if allowed_tools and tool_name not in allowed_tools:
                        logger.debug(f"[ForkAgent] Skipping tool not in allowed_tools: {tool_name}")
                        continue

                    if hasattr(tool, "card"):
                        fork_agent.ability_manager.add(tool.card)
                    else:
                        fork_agent.ability_manager.add(tool)
                    inherited_count += 1
                except Exception as e:
                    logger.debug(
                        f"[ForkAgent] Failed to inherit tool {getattr(tool, 'name', 'unknown')}: {e}"
                    )

            logger.info(
                f"[ForkAgent] Inherited {inherited_count} tools from parent agent"
            )
        except Exception as e:
            logger.warning(f"[ForkAgent] Failed to inherit tools: {e}")


# Global executor instance (initialized by init_subagent_tools)
_executor: ForkAgentExecutor | None = None


def get_fork_agent_executor() -> ForkAgentExecutor | None:
    """Get the global fork agent executor."""
    return _executor


def init_subagent_executor(
    parent_agent: DeepAgent,
    model: Model,
    default_role_prompts: dict[str, str] | None = None,
) -> None:
    """Initialize the subagent executor with parent agent and model.

    Args:
        parent_agent: Parent DeepAgent instance
        model: Model instance for creating subagents
        default_role_prompts: Default role prompts (used when role_id not found)
    """
    global _executor
    _executor = ForkAgentExecutor(
        parent_agent,
        model=model,
        default_role_prompts=default_role_prompts,
    )
    logger.info("[Subagent] Initialized subagent executor")