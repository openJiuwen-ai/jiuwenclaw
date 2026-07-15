# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuClawStreamEventRail — Stream event emission, pause checks, context fix.

Migrated from JiuClawReActAgent:
  - _emit_tool_call / _emit_tool_result / _emit_todo_updated / _emit_context_compression
  - _fix_incomplete_tool_context
  - Pause checkpoint logic
"""
from __future__ import annotations

import asyncio
import json
import os
import time
from typing import Any, List, Optional, TYPE_CHECKING

import tiktoken
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ToolMessage,
    UserMessage,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ToolCallInputs,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.session.checkpointer import CheckpointerFactory
from openjiuwen.core.single_agent import BaseAgent
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.todo import TodoStatus, TodoListTool
from openjiuwen.harness.workspace.workspace import WorkspaceNode

from jiuwenclaw.config import get_config
from jiuwenclaw.tool_arguments_validator import validate_tool_arguments
from jiuwenclaw.utils import logger

# Import subagent context functions for fork_agent
from jiuwenclaw.agentserver.tools.subagent_executor import (
    set_subagent_parent_session,
    set_current_agent_context,
    set_current_fork_agent_executor,
    reset_current_fork_agent_executor,
)
from jiuwenclaw.agentserver.skill_turbo.skill_turbo_tools import (
    set_current_skill_turbo_adapter,
    reset_current_skill_turbo_adapter,
)

if TYPE_CHECKING:
    from jiuwenclaw.agentserver.tools.subagent_executor.executor import ForkAgentExecutor

_TODO_TOOL_NAMES = frozenset([
    "todo_create", "todo_start", "todo_complete", "todo_complete_batch",
    "todo_insert", "todo_remove", "todo_modify", "todo_list"
])
_DEFAULT_CONTEXT_WINDOW_LIMIT_TOKENS = 128000
_EARLY_CHECKPOINT_EXTRA_KEY = "_jiuwenclaw_early_checkpoint_done"
_FORK_EXECUTOR_TOKEN_EXTRA_KEY = "_jiuwenclaw_fork_executor_token"
_SKILL_TURBO_ADAPTER_TOKEN_EXTRA_KEY = "_jiuwenclaw_skill_turbo_adapter_token"
_EARLY_CHECKPOINT_ENV = "JIUWENCLAW_EARLY_CHECKPOINT"


def _reset_fork_executor_token(ctx: AgentCallbackContext) -> None:
    """Restore ForkAgentExecutor ContextVar binding for this tool call."""
    token = ctx.extra.pop(_FORK_EXECUTOR_TOKEN_EXTRA_KEY, None)
    if token is not None:
        reset_current_fork_agent_executor(token)


def _reset_skill_turbo_adapter_token(ctx: AgentCallbackContext) -> None:
    """Restore SkillTurbo adapter ContextVar binding for this tool call."""
    token = ctx.extra.pop(_SKILL_TURBO_ADAPTER_TOKEN_EXTRA_KEY, None)
    if token is not None:
        reset_current_skill_turbo_adapter(token)


def _early_checkpoint_disabled_by_env() -> bool:
    raw = (os.getenv(_EARLY_CHECKPOINT_ENV) or "").strip().lower()
    return raw in {"0", "false", "no", "off"}


def _resolve_context_window_limit_tokens() -> int:
    """Read react.context_window_limit_tokens from config with safe coercion.

    Falls back to 128000 on missing / non-positive / non-numeric values.
    """
    try:
        react_cfg = (get_config() or {}).get("react") or {}
    except Exception:
        return _DEFAULT_CONTEXT_WINDOW_LIMIT_TOKENS

    raw = react_cfg.get("context_window_limit_tokens")
    if isinstance(raw, bool):
        return _DEFAULT_CONTEXT_WINDOW_LIMIT_TOKENS
    try:
        value = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return _DEFAULT_CONTEXT_WINDOW_LIMIT_TOKENS
    if value <= 0:
        return _DEFAULT_CONTEXT_WINDOW_LIMIT_TOKENS
    return value


def _structured_tool_result_payload(result: Any) -> Any | None:
    if isinstance(result, (dict, list)):
        return result
    return None


def _session_id_from_ctx(ctx: AgentCallbackContext) -> str:
    session = ctx.session
    if session is None:
        return ""
    getter = getattr(session, "get_session_id", None)
    if callable(getter):
        try:
            return str(getter() or "")
        except Exception:
            return ""
    return ""


def _extract_skill_complete_arg(tool_call: Any, key: str) -> str:
    """Read a string arg from skill_complete tool_call.arguments (dict or JSON string)."""
    args = getattr(tool_call, "arguments", None)
    val: Any = ""
    if isinstance(args, dict):
        val = args.get(key, "")
    elif isinstance(args, str):
        try:
            parsed = json.loads(args)
        except json.JSONDecodeError:
            return ""
        if isinstance(parsed, dict):
            val = parsed.get(key, "")
    return str(val).strip() if val else ""


class JiuClawStreamEventRail(DeepAgentRail):
    """Emit frontend stream events and enforce pause/abort checkpoints.

    Pause/abort state is owned by this Rail (not DeepAgent) so that
    interface.py can call rail.pause() / rail.resume() / rail.abort()
    without requiring changes to DeepAgent.
    """

    priority = 80

    def __init__(self) -> None:
        super().__init__()
        self._deep_agent: Optional[Any] = None
        self._fork_agent_executor: Optional["ForkAgentExecutor"] = None
        self._skill_turbo_adapter: Optional[Any] = None
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._abort_requested = False
        self._conversation_id: str = ""
        self._stream_tasks: set[asyncio.Task] = set()

    def set_fork_agent_executor(
        self,
        executor: Optional["ForkAgentExecutor"],
    ) -> None:
        """Bind the adapter-local ForkAgentExecutor for main-agent tool calls."""
        self._fork_agent_executor = executor

    def set_skill_turbo_adapter(self, adapter: Optional[Any]) -> None:
        """Bind the adapter instance for skill_turbo tool."""
        self._skill_turbo_adapter = adapter

    def init(self, agent: Any) -> None:
        self._deep_agent = agent

    # -- pause / resume / abort API for interface.py --

    def pause(self) -> None:
        self._pause_event.clear()

    def resume(self) -> None:
        self._abort_requested = False
        self._pause_event.set()

    def abort(self) -> None:
        self._abort_requested = True
        self._pause_event.set()

    def reset_abort(self) -> None:
        self._abort_requested = False

    # ------------------------------------------------------------------
    # before_invoke (Outer event on DeepAgent): capture conversation_id
    # ------------------------------------------------------------------

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        if isinstance(ctx.inputs, InvokeInputs):
            self._conversation_id = ctx.inputs.conversation_id or ""

    # ------------------------------------------------------------------
    # before_model_call: pause check + context fix + compression info
    # ------------------------------------------------------------------

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        diag_start = time.perf_counter()
        session_id = _session_id_from_ctx(ctx)
        msg_count = len(ctx.context.get_messages()) if ctx.context is not None else 0
        logger.debug(
            "[StreamEventRail] before_model_call start session_id=%s messages=%s",
            session_id,
            msg_count,
        )

        await self._pause_event.wait()
        if self._abort_requested:
            raise asyncio.CancelledError("Agent abort requested")

        if not ctx.extra.get("_context_fixed") and ctx.context is not None:
            t_fix = time.perf_counter()
            await self._fix_incomplete_tool_context(ctx.context, session_id=session_id)
            ctx.extra["_context_fixed"] = True
            logger.debug(
                "[StreamEventRail] before_model_call fix_incomplete_tool_context "
                "session_id=%s elapsed_ms=%.1f",
                session_id,
                (time.perf_counter() - t_fix) * 1000,
            )

        t_emit = time.perf_counter()
        await self._emit_context_compression(ctx)
        await self._emit_context_usage(ctx)
        logger.debug(
            "[StreamEventRail] before_model_call context_stats emitted "
            "session_id=%s elapsed_ms=%.1f",
            session_id,
            (time.perf_counter() - t_emit) * 1000,
        )

        t_ckpt = time.perf_counter()
        await self._maybe_early_checkpoint(ctx)
        logger.debug(
            "[StreamEventRail] before_model_call done session_id=%s total_elapsed_ms=%.1f "
            "checkpoint_elapsed_ms=%.1f",
            session_id,
            (time.perf_counter() - diag_start) * 1000,
            (time.perf_counter() - t_ckpt) * 1000,
        )

    async def _maybe_early_checkpoint(self, ctx: AgentCallbackContext) -> None:
        """Persist context + agent state once per invoke before the first LLM call.

        Mitigates losing the user message if the process dies before ``post_run``:
        ``save_contexts`` then ``post_agent_execute`` (not ``post_run``, which closes
        the stream). Skipped on later ReAct iterations via ``ctx.extra`` flag.
        """
        if _early_checkpoint_disabled_by_env():
            return
        if ctx.extra.get(_EARLY_CHECKPOINT_EXTRA_KEY):
            return
        cid = (self._conversation_id or "").strip()
        if cid.startswith("heartbeat"):
            return
        session = ctx.session
        agent = ctx.agent
        if session is None or agent is None:
            return
        context_engine = getattr(agent, "context_engine", None)
        if context_engine is None:
            return

        actual_session = getattr(session, "_parent", session) if session else None
        if actual_session is None:
            return

        try:
            await context_engine.save_contexts(actual_session)
            inner = getattr(actual_session, "_inner", actual_session)
            await CheckpointerFactory.get_checkpointer().post_agent_execute(inner)
            ctx.extra[_EARLY_CHECKPOINT_EXTRA_KEY] = True
            sid = ""
            gs = getattr(actual_session, "get_session_id", None)
            if callable(gs):
                sid = str(gs())
            else:
                fn = getattr(actual_session, "session_id", None)
                if callable(fn):
                    sid = str(fn())
            logger.debug(
                "[StreamEventRail] early checkpoint saved session_id=%s",
                sid or "",
            )
        except Exception as exc:
            logger.warning(
                "[StreamEventRail] early checkpoint failed: %s",
                exc,
                exc_info=True,
            )

    # ------------------------------------------------------------------
    # before_tool_call: pause check + emit tool_call event + set context for fork_agent
    # ------------------------------------------------------------------

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        await self._pause_event.wait()
        if self._abort_requested:
            raise asyncio.CancelledError("Agent abort requested")

        # Set current agent context for fork_agent to get correct messages
        # This ensures fork_agent gets messages from the running context
        # (not from storage which may be empty for subagent scenarios)
        if ctx.context is not None:
            set_current_agent_context(ctx.context)

        if self._fork_agent_executor is not None:
            ctx.extra[_FORK_EXECUTOR_TOKEN_EXTRA_KEY] = set_current_fork_agent_executor(
                self._fork_agent_executor
            )

        if self._skill_turbo_adapter is not None:
            ctx.extra[_SKILL_TURBO_ADAPTER_TOKEN_EXTRA_KEY] = set_current_skill_turbo_adapter(
                self._skill_turbo_adapter
            )

        # Set parent session for subagent tools
        # Use parent session if session is a proxy (SubagentSessionProxy)
        # This ensures tools get the actual parent session, not the proxy wrapper
        session = ctx.session
        if session is not None:
            actual_session = getattr(session, '_parent', session) if session else None
            set_subagent_parent_session(actual_session)

        if session is not None and isinstance(ctx.inputs, ToolCallInputs):
            tc = ctx.inputs.tool_call
            await self._emit_tool_call(session, tc)
            await self._emit_tool_update(session, tc, status="in_progress")

    # ------------------------------------------------------------------
    # after_tool_call: emit tool_result + todo.updated + clear context
    # ------------------------------------------------------------------

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        diag_start = time.perf_counter()
        tool_name = (
            ctx.inputs.tool_name
            if isinstance(ctx.inputs, ToolCallInputs)
            else ""
        )
        session_id = _session_id_from_ctx(ctx)
        logger.debug(
            "[StreamEventRail] after_tool_call start session_id=%s tool=%s",
            session_id,
            tool_name,
        )

        # Clear context after tool execution
        set_current_agent_context(None)
        set_subagent_parent_session(None)
        _reset_fork_executor_token(ctx)
        _reset_skill_turbo_adapter_token(ctx)

        # SkillTurbo HITL：skill_turbo_tools 在 ContextVar 存了 ToolInterruptException，
        # 此处改写 ctx.inputs.tool_result 为 TIE，使 harness 原生 HITL 机制检测并暂停。
        # 用 harness 的 tool_call 构造新 TIE（原始的是 SkillTurboToolCall，Pydantic 验证不通过）。
        from jiuwenclaw.agentserver.skill_turbo.skill_turbo_tools import (
            get_skill_turbo_hitl_tic,
            set_skill_turbo_hitl_tic,
        )
        _skill_turbo_tic = get_skill_turbo_hitl_tic()
        if _skill_turbo_tic is not None:
            set_skill_turbo_hitl_tic(None)
            if isinstance(ctx.inputs, ToolCallInputs):
                from openjiuwen.core.single_agent.interrupt.exception import (
                    ToolInterruptException,
                )
                new_tic = ToolInterruptException(
                    request=_skill_turbo_tic.request,
                    tool_call=ctx.inputs.tool_call,
                )
                ctx.inputs.tool_result = new_tic
                ctx.inputs.tool_msg = None
            logger.info(
                "[StreamEventRail] SkillTurbo HITL: rewrote tool_result to TIE. "
                "original_tcid=%s harness_tcid=%s",
                _skill_turbo_tic.tool_call.id if _skill_turbo_tic.tool_call else "?",
                ctx.inputs.tool_call.id if isinstance(ctx.inputs, ToolCallInputs) else "?",
            )
            return  # 跳过 _emit_tool_result，由 harness __interaction__ 取代

        session = ctx.session
        if session is None or not isinstance(ctx.inputs, ToolCallInputs):
            logger.debug(
                "[StreamEventRail] after_tool_call skip session_id=%s tool=%s reason=no_session_or_inputs",
                session_id,
                tool_name,
            )
            return
        t_result = time.perf_counter()
        await self._emit_tool_result(session, ctx.inputs.tool_call, ctx.inputs.tool_result)
        if (
            isinstance(ctx.inputs.tool_result, dict)
            and ctx.inputs.tool_result.get("skipped") is True
        ):
            await self._emit_tool_update(session, ctx.inputs.tool_call, status="failed")
        logger.debug(
            "[StreamEventRail] after_tool_call tool_result emitted session_id=%s tool=%s elapsed_ms=%.1f",
            session_id,
            tool_name,
            (time.perf_counter() - t_result) * 1000,
        )
        tool_name = ctx.inputs.tool_name

        if tool_name in _TODO_TOOL_NAMES and self._conversation_id:
            t_todo = time.perf_counter()
            await self._emit_todo_updated(ctx.agent, session, self._conversation_id)
            logger.debug(
                "[StreamEventRail] after_tool_call todo.updated emitted session_id=%s tool=%s "
                "elapsed_ms=%.1f",
                session_id,
                tool_name,
                (time.perf_counter() - t_todo) * 1000,
            )

        logger.debug(
            "[StreamEventRail] after_tool_call done session_id=%s tool=%s total_elapsed_ms=%.1f",
            session_id,
            tool_name,
            (time.perf_counter() - diag_start) * 1000,
        )

    # ------------------------------------------------------------------
    # on_model_exception: attempt context repair + clear context
    # ------------------------------------------------------------------

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        # Clear context on exception
        set_current_agent_context(None)
        set_subagent_parent_session(None)
        _reset_fork_executor_token(ctx)
        _reset_skill_turbo_adapter_token(ctx)

        if ctx.context is not None:
            logger.info("[StreamEventRail] Attempting context repair after model exception")
            await self._fix_incomplete_tool_context(ctx.context, session_id=_session_id_from_ctx(ctx))

    # ------------------------------------------------------------------
    # Private helpers (migrated from JiuClawReActAgent)
    # ------------------------------------------------------------------

    @staticmethod
    async def _emit_tool_call(session: Session, tool_call: Any) -> None:
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
            logger.debug("tool_call emit failed", exc_info=True)

    @staticmethod
    async def _emit_user_visible_text(session: Session, content: str) -> None:
        """Emit assistant-visible text for tool-driven user-visible output (e.g. skill_complete)."""
        text = str(content or "").strip()
        if not text:
            return
        try:
            await session.write_stream(
                OutputSchema(
                    type="llm_output",
                    index=0,
                    payload={"content": text, "result_type": "answer"},
                )
            )
        except Exception:
            logger.debug("ask_user_question user-visible text emit failed", exc_info=True)

    @staticmethod
    async def _emit_tool_result(session: Session, tool_call: Any, result: Any) -> None:
        try:
            raw_output = _structured_tool_result_payload(result)
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
            logger.debug("tool_result emit failed", exc_info=True)

    @staticmethod
    async def _emit_tool_update(session: Session, tool_call: Any, *, status: str) -> None:
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
            logger.debug("tool_update emit failed", exc_info=True)

    async def _emit_todo_updated(
        self, agent: BaseAgent, session: Session, session_id: str
    ) -> None:
        """Emit todo list update event to frontend.

        Loads current todos using TodoListTool, maps internal status to
        frontend-compatible values, and emits a 'todo.updated' stream event.

        Args:
            agent: The agent instance to access ability_manager for tool lookup.
            session: Session object for writing stream events.
            session_id: Session ID used to locate the todo JSON file.
        """
        todo_tool = self._get_todo_tool(agent)
        if todo_tool is None:
            logger.debug("[StreamEventRail] TodoListTool not available")
            return

        try:
            file_path = todo_tool.file_path_for_session(session_id)
            todos_data = await todo_tool.load_todos(file_path)
        except Exception as exc:
            logger.debug(
                "[StreamEventRail] Failed to load todos: %s", exc
            )
            return

        if not todos_data:
            return

        todos = self.format_todos_for_frontend(todos_data)

        try:
            await session.write_stream(
                OutputSchema(
                    type="todo.updated",
                    index=0,
                    payload={"todos": todos},
                )
            )
        except Exception:
            logger.debug("todo.updated emit failed", exc_info=True)

    def _get_todo_tool(self, agent: BaseAgent) -> TodoListTool | None:
        """Get TodoListTool from agent's ability_manager or create new instance.

        First attempts to retrieve the registered tool from the agent's
        ability_manager and Runner's resource_mgr. If not found, falls back
        to creating a new TodoListTool instance with rail's workspace config.

        Args:
            agent: The agent instance to access ability_manager.

        Returns:
            TodoListTool instance or None if unavailable.
        """
        # Try to get registered tool from agent's ability_manager
        try:
            tool_card = agent.ability_manager.get("todo_list")
            registered_tool = Runner.resource_mgr.get_tool(tool_card.id)
            if isinstance(registered_tool, TodoListTool):
                return registered_tool
        except Exception:
            pass

        # Fallback: create new tool instance
        try:
            language = getattr(
                getattr(self._deep_agent, "system_prompt_builder", None),
                "language", "cn",
            ) or "cn"
            agent_id = self._deep_agent.card.id if self._deep_agent else None 
            return TodoListTool(
                operation=self.sys_operation,
                workspace=str(self.workspace.get_node_path(WorkspaceNode.TODO)),
                language=language,
                agent_id=agent_id
            )
        except Exception as exc:
            logger.debug(
                "[StreamEventRail] Failed to create TodoListTool: %s", exc
            )
            return None

    @staticmethod
    def format_todos_for_frontend(
        todos_data: List[Any],
    ) -> List[dict[str, Any]]:
        """Format todo items for frontend compatibility.

        Args:
            todos_data: List of TodoItem objects from TodoListTool.

        Returns:
            List of formatted todo dictionaries.
        """
        # 社区前端代码暂时不支持cancelled状态。
        return [
            {
                "id": item.id,
                "content": item.content,
                "activeForm": item.activeForm,
                "status": item.status.value,
                "createdAt": item.createdAt,
                "updatedAt": item.updatedAt,
            }
            for item in todos_data
        ]

    @staticmethod
    async def _emit_context_compression(ctx: AgentCallbackContext) -> None:
        """Emit context compression stats based on raw_total_tokens and current context tokens."""
        session = ctx.session
        if session is None:
            return

        context = ctx.context
        if context is None:
            return

        try:
            stat = context.statistic()
            raw_total_tokens = stat.raw_total_tokens
            current_context_tokens = stat.single_messages_token

            if raw_total_tokens > 0:
                rate = (raw_total_tokens - current_context_tokens) / raw_total_tokens * 100
            else:
                rate = 0

            await session.write_stream(
                OutputSchema(
                    type="context.compressed",
                    index=0,
                    payload={
                        "rate": rate,
                        "before_compressed": raw_total_tokens,
                        "after_compressed": current_context_tokens,
                    },
                )
            )
        except Exception:
            logger.debug("context_compression emit failed", exc_info=True)

    @staticmethod
    async def _emit_context_usage(ctx: AgentCallbackContext) -> None:
        """Emit context window usage stats for the frontend status panel.

        Numerator: current effective context tokens (post-compression).
        Denominator: react.context_window_limit_tokens from config.
        """
        session = ctx.session
        if session is None:
            return

        context = ctx.context
        if context is None:
            return

        try:
            stat = context.statistic()
            used_tokens_raw = getattr(stat, "single_messages_token", None)
            used_tokens = (
                max(int(used_tokens_raw), 0)
                if isinstance(used_tokens_raw, (int, float))
                else None
            )

            limit_tokens = _resolve_context_window_limit_tokens()
            usage_percent = (
                round(used_tokens / limit_tokens * 100, 1)
                if used_tokens is not None and limit_tokens > 0
                else None
            )

            await session.write_stream(
                OutputSchema(
                    type="context.usage",
                    index=0,
                    payload={
                        "used_tokens": used_tokens,
                        "limit_tokens": limit_tokens,
                        "usage_percent": usage_percent,
                    },
                )
            )
        except Exception:
            logger.debug("context_usage emit failed", exc_info=True)

    @staticmethod
    def _ensure_json_arguments(
        arguments: Any,
        *,
        finish_reason: str | None = None,
        tool_name: str = "",
        tool_call_id: str = "",
        session_id: str = "",
    ) -> str:
        validation = validate_tool_arguments(arguments, finish_reason=finish_reason)
        if validation.ok:
            return validation.normalized
        logger.warning(
            "[tool_args_validation] action=context_sanitize tool=%s tool_call_id=%s "
            "kind=%s length=%s reason=%s session_id=%s",
            tool_name,
            tool_call_id,
            validation.kind,
            validation.length,
            validation.reason,
            session_id,
        )
        return "{}"

    @staticmethod
    def _placeholder_tool_message(tool_name: str, tool_call_id: str, reason: str | None = None) -> ToolMessage:
        if reason:
            content = (
                f"[工具调用参数已修复] 工具 {tool_name} 的调用参数 JSON {reason}，"
                "历史上下文已归一化为合法空 JSON object，并补充该 ToolMessage 以保持上下文有效。"
            )
        else:
            content = f"[工具执行结果缺失] 工具 {tool_name} 缺少对应执行结果，已补充占位 ToolMessage 以保持上下文有效。"
        return ToolMessage(content=content, tool_call_id=tool_call_id)

    async def _fix_incomplete_tool_context(self, context: Any, *, session_id: str = "") -> None:
        """Fix incomplete context: ensure assistant messages with tool_calls have matching tool messages."""
        original_messages: list = []
        try:
            messages = context.get_messages()
            len_messages = len(messages)
            if len_messages == 0:
                return

            original_messages = list(messages)
            messages = context.pop_messages(size=len_messages)
            tool_message_cache: dict = {}
            tool_id_cache: list = []

            for i in range(len_messages):
                if isinstance(messages[i], AssistantMessage):
                    if not tool_id_cache:
                        tool_calls = getattr(messages[i], "tool_calls", None)
                        finish_reason = getattr(messages[i], "finish_reason", None)
                        if tool_calls:
                            for tc in tool_calls:
                                raw_arguments = getattr(tc, "arguments", '{}')
                                validation = validate_tool_arguments(raw_arguments, finish_reason=finish_reason)
                                arguments = validation.normalized if validation.ok else "{}"
                                if not validation.ok:
                                    logger.warning(
                                        "[tool_args_validation] action=context_sanitize tool=%s tool_call_id=%s "
                                        "kind=%s length=%s reason=%s session_id=%s",
                                        getattr(tc, "name", ""),
                                        getattr(tc, "id", ""),
                                        validation.kind,
                                        validation.length,
                                        validation.reason,
                                        session_id,
                                    )
                                if hasattr(tc, "arguments"):
                                    tc.arguments = arguments
                                tool_id_cache.append({
                                        "tool_call_id": getattr(tc, "id", ""),
                                        "tool_name": getattr(tc, "name", ""),
                                        "invalid_reason": None if validation.ok else validation.reason,
                                })
                        await context.add_messages(messages[i])
                    else:
                        logger.info("Fixed incomplete tool context with placeholder messages")
                        for tc_info in tool_id_cache:
                            tool_name = tc_info["tool_name"]
                            tool_call_id = tc_info["tool_call_id"]
                            if tool_call_id in tool_message_cache:
                                await context.add_messages(tool_message_cache[tool_call_id])
                            else:
                                await context.add_messages(self._placeholder_tool_message(
                                    tool_name,
                                    tool_call_id,
                                    tc_info.get("invalid_reason"),
                                ))
                        tool_id_cache = []
                        tool_calls = getattr(messages[i], "tool_calls", None)
                        finish_reason = getattr(messages[i], "finish_reason", None)
                        if tool_calls:
                            for tc in tool_calls:
                                raw_arguments = getattr(tc, "arguments", {})
                                validation = validate_tool_arguments(raw_arguments, finish_reason=finish_reason)
                                arguments = validation.normalized if validation.ok else "{}"
                                if not validation.ok:
                                    logger.warning(
                                        "[tool_args_validation] action=context_sanitize tool=%s tool_call_id=%s "
                                        "kind=%s length=%s reason=%s session_id=%s",
                                        getattr(tc, "name", ""),
                                        getattr(tc, "id", ""),
                                        validation.kind,
                                        validation.length,
                                        validation.reason,
                                        session_id,
                                    )
                                if hasattr(tc, "arguments"):
                                    tc.arguments = arguments
                                tool_id_cache.append({
                                        "tool_call_id": getattr(tc, "id", ""),
                                        "tool_name": getattr(tc, "name", ""),
                                        "invalid_reason": None if validation.ok else validation.reason,
                                })
                        await context.add_messages(messages[i])
                elif isinstance(messages[i], ToolMessage):
                    if not tool_id_cache:
                        tool_message_cache[messages[i].tool_call_id] = messages[i]
                        continue
                    if messages[i].tool_call_id == tool_id_cache[0]["tool_call_id"]:
                        await context.add_messages(messages[i])
                        tool_id_cache.pop(0)
                    else:
                        tool_message_cache[messages[i].tool_call_id] = messages[i]
                        continue
                else:
                    logger.info("Fixed incomplete tool context with placeholder messages")
                    for tc_info in tool_id_cache:
                        tool_name = tc_info["tool_name"]
                        tool_call_id = tc_info["tool_call_id"]
                        if tool_call_id in tool_message_cache:
                            await context.add_messages(tool_message_cache[tool_call_id])
                        else:
                            await context.add_messages(self._placeholder_tool_message(
                                tool_name,
                                tool_call_id,
                                tc_info.get("invalid_reason"),
                            ))
                    tool_id_cache = []
                    await context.add_messages(messages[i])

            if tool_id_cache:
                for tc_info in tool_id_cache:
                    tool_name = tc_info["tool_name"]
                    tool_call_id = tc_info["tool_call_id"]
                    if tool_call_id in tool_message_cache:
                        await context.add_messages(tool_message_cache[tool_call_id])
                    else:
                        await context.add_messages(self._placeholder_tool_message(
                            tool_name,
                            tool_call_id,
                            tc_info.get("invalid_reason"),
                        ))

            rebuilt = context.get_messages()
            if rebuilt and not any(
                isinstance(m, (UserMessage, ToolMessage))
                or getattr(m, "role", None) in ("user", "tool")
                for m in rebuilt
            ):
                logger.error(
                    "[StreamEventRail] context repair rejected: missing user/tool role "
                    "session_id=%s before=%s after=%s; restoring original messages",
                    session_id,
                    len(original_messages),
                    len(rebuilt),
                )
                remaining = context.get_messages()
                if remaining:
                    context.pop_messages(size=len(remaining))
                await context.add_messages(original_messages)
        except Exception as e:
            logger.warning("Failed to fix incomplete tool context: %s", e)
            try:
                remaining = context.get_messages()
                if remaining:
                    context.pop_messages(size=len(remaining))
                if original_messages:
                    await context.add_messages(original_messages)
            except Exception:
                logger.warning(
                    "Failed to restore original messages after context repair error",
                    exc_info=True,
                )
