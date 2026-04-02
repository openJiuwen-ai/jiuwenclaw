# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuClawStreamEventRail — Stream event emission, pause checks, context fix.

Migrated from JiuClawReActAgent:
  - _emit_tool_call / _emit_tool_result / _emit_todo_updated / _emit_context_compression
  - _fix_incomplete_tool_context
  - Pause checkpoint logic
"""
from __future__ import annotations

import asyncio
from typing import Any, List, Optional

import tiktoken
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    ToolMessage,
)
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    InvokeInputs,
    ToolCallInputs,
)
from openjiuwen.core.runner import Runner
from openjiuwen.core.single_agent import BaseAgent
from openjiuwen.harness.rails.base import DeepAgentRail
from openjiuwen.harness.tools.todo import TodoStatus, TodoListTool
from openjiuwen.harness.workspace.workspace import WorkspaceNode

from jiuwenclaw.utils import logger

_TODO_TOOL_NAMES = frozenset(["todo_create", "todo_list", "todo_modify"])


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
        self._pause_event = asyncio.Event()
        self._pause_event.set()
        self._abort_requested = False
        self._conversation_id: str = ""
        self._stream_tasks: set[asyncio.Task] = set()

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
        await self._pause_event.wait()
        if self._abort_requested:
            raise asyncio.CancelledError("Agent abort requested")

        if not ctx.extra.get("_context_fixed") and ctx.context is not None:
            await self._fix_incomplete_tool_context(ctx.context)
            ctx.extra["_context_fixed"] = True

        await self._emit_context_compression(ctx)

    # ------------------------------------------------------------------
    # before_tool_call: pause check + emit tool_call event
    # ------------------------------------------------------------------

    async def before_tool_call(self, ctx: AgentCallbackContext) -> None:
        await self._pause_event.wait()
        if self._abort_requested:
            raise asyncio.CancelledError("Agent abort requested")

        session = ctx.session
        if session is not None and isinstance(ctx.inputs, ToolCallInputs):
            tc = ctx.inputs.tool_call
            await self._emit_tool_call(session, tc)

    # ------------------------------------------------------------------
    # after_tool_call: emit tool_result + todo.updated
    # ------------------------------------------------------------------

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        session = ctx.session
        if session is None or not isinstance(ctx.inputs, ToolCallInputs):
            return

        await self._emit_tool_result(session, ctx.inputs.tool_call, ctx.inputs.tool_result)

        tool_name = ctx.inputs.tool_name
        if tool_name in _TODO_TOOL_NAMES and self._conversation_id:
            await self._emit_todo_updated(ctx.agent, session, self._conversation_id)

    # ------------------------------------------------------------------
    # on_model_exception: attempt context repair
    # ------------------------------------------------------------------

    async def on_model_exception(self, ctx: AgentCallbackContext) -> None:
        if ctx.context is not None:
            logger.info("[StreamEventRail] Attempting context repair after model exception")
            await self._fix_incomplete_tool_context(ctx.context)

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
    async def _emit_tool_result(session: Session, tool_call: Any, result: Any) -> None:
        try:
            await session.write_stream(
                OutputSchema(
                    type="tool_result",
                    index=0,
                    payload={
                        "tool_result": {
                            "tool_name": getattr(tool_call, "name", "") if tool_call else "",
                            "tool_call_id": getattr(tool_call, "id", "") if tool_call else "",
                            "result": str(result)[:1000] if result is not None else "",
                        }
                    },
                )
            )
        except Exception:
            logger.debug("tool_result emit failed", exc_info=True)

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
            todo_tool.set_file(session_id)
            todos_data = await todo_tool.load_todos()
        except Exception as exc:
            logger.debug(
                "[StreamEventRail] Failed to load todos: %s", exc
            )
            return

        if not todos_data:
            return

        todos = self._format_todos_for_frontend(todos_data)

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
            return TodoListTool(
                operation=self.sys_operation,
                workspace=str(self.workspace.get_node_path(WorkspaceNode.TODO)),
                language=language,
            )
        except Exception as exc:
            logger.debug(
                "[StreamEventRail] Failed to create TodoListTool: %s", exc
            )
            return None

    @staticmethod
    def _format_todos_for_frontend(
        todos_data: List[Any],
    ) -> List[dict[str, Any]]:
        """Format todo items for frontend compatibility.

        Maps internal TodoStatus values to frontend-compatible status strings.
        Cancelled status is mapped to 'pending' for frontend compatibility.

        Args:
            todos_data: List of TodoItem objects from TodoListTool.

        Returns:
            List of formatted todo dictionaries.
        """
        status_mapping = {
            TodoStatus.PENDING: "pending",
            TodoStatus.IN_PROGRESS: "in_progress",
            TodoStatus.COMPLETED: "completed",
            TodoStatus.CANCELLED: "pending",
        }

        return [
            {
                "id": item.id,
                "content": item.content,
                "activeForm": item.activeForm,
                "status": status_mapping.get(item.status, item.status.value),
                "createdAt": item.createdAt,
                "updatedAt": item.updatedAt,
            }
            for item in todos_data
        ]

    @staticmethod
    async def _emit_context_compression(ctx: AgentCallbackContext) -> None:
        """Emit context compression stats if OffloadMixin messages are present."""
        session = ctx.session
        if session is None or not hasattr(ctx.inputs, "messages"):
            return

        messages = ctx.inputs.messages
        compression_to_show: List = []
        uncompressed: List = []

        for message in messages:
            if isinstance(message, OffloadMixin):
                try:
                    context = ctx.context
                    if context is not None:
                        original_message = await context.reloader_tool().invoke(
                            inputs={
                                "offload_handle": message.offload_handle,
                                "offload_type": message.offload_type,
                            }
                        )
                        compression_to_show.append((message, original_message))
                except Exception:
                    pass
            else:
                uncompressed.append(message)

        if not compression_to_show:
            return

        try:
            try:
                encoding = tiktoken.get_encoding("cl100k_base")
                tokens_compressed = 0
                tokens_full = 0
                token_uncompressed = 0
                for msg in uncompressed:
                    token_uncompressed += len(encoding.encode(getattr(msg, "content", "") or ""))
                for c, o in compression_to_show:
                    tokens_compressed += len(encoding.encode(getattr(c, "content", "") or ""))
                    tokens_full += len(encoding.encode(o if isinstance(o, str) else ""))
            except Exception:
                tokens_compressed = 0
                tokens_full = 0
                token_uncompressed = 0
                for msg in uncompressed:
                    token_uncompressed += len(getattr(msg, "content", "") or "")
                for c, o in compression_to_show:
                    tokens_compressed += len(getattr(c, "content", "") or "")
                    tokens_full += len(o if isinstance(o, str) else "")

            pre_compression = tokens_full + token_uncompressed
            post_compression = tokens_compressed + token_uncompressed
            if pre_compression > 0:
                rate = (1 - post_compression / pre_compression) * 100
            else:
                rate = 0

            await session.write_stream(
                OutputSchema(
                    type="context.compressed",
                    index=0,
                    payload={
                        "rate": rate,
                        "before_compressed": pre_compression,
                        "after_compressed": post_compression,
                    },
                )
            )
        except Exception:
            logger.debug("context_compression emit failed", exc_info=True)

    @staticmethod
    async def _fix_incomplete_tool_context(context: Any) -> None:
        """Fix incomplete context: ensure assistant messages with tool_calls have matching tool messages."""
        try:
            messages = context.get_messages()
            len_messages = len(messages)
            if len_messages == 0:
                return

            messages = context.pop_messages(size=len_messages)
            tool_message_cache: dict = {}
            tool_id_cache: list = []

            for i in range(len_messages):
                if isinstance(messages[i], AssistantMessage):
                    if not tool_id_cache:
                        await context.add_messages(messages[i])
                        tool_calls = getattr(messages[i], "tool_calls", None)
                        if tool_calls:
                            for tc in tool_calls:
                                tool_id_cache.append({
                                        "tool_call_id": getattr(tc, "id", ""),
                                        "tool_name": getattr(tc, "name", ""),
                                })
                    else:
                        logger.info("Fixed incomplete tool context with placeholder messages")
                        for tc_info in tool_id_cache:
                            tool_name = tc_info["tool_name"]
                            tool_call_id = tc_info["tool_call_id"]
                            if tool_call_id in tool_message_cache:
                                await context.add_messages(tool_message_cache[tool_call_id])
                            else:
                                await context.add_messages(ToolMessage(
                                        content=f"[工具执行被中断] 工具 {tool_name} 执行过程中被用户打断，没有执行结果。",
                                        tool_call_id=tool_call_id,
                                ))
                        tool_id_cache = []
                        await context.add_messages(messages[i])
                        tool_calls = getattr(messages[i], "tool_calls", None)
                        if tool_calls:
                            for tc in tool_calls:
                                tool_id_cache.append({
                                        "tool_call_id": getattr(tc, "id", ""),
                                        "tool_name": getattr(tc, "name", ""),
                                })
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
                            await context.add_messages(ToolMessage(
                                    content=f"[工具执行被中断] 工具 {tool_name} 执行过程中被用户打断，没有执行结果。",
                                    tool_call_id=tool_call_id,
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
                        await context.add_messages(ToolMessage(
                                content=f"[工具执行被中断] 工具 {tool_name} 执行过程中被用户打断，没有执行结果。",
                                tool_call_id=tool_call_id,
                        ))
        except Exception as e:
            logger.warning("Failed to fix incomplete tool context: %s", e)
