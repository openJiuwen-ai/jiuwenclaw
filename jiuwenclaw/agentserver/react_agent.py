# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuClawReActAgent - Inherits openjiuwen ReActAgent, overrides invoke/stream.

Emits todo.updated events after todo tool calls for frontend real-time sync.
Sends evolution approval requests to user via chat.ask_user_question (keep/undo).
"""
from __future__ import annotations

import asyncio
import importlib.util
import logging
import re
import sys
import uuid
from pathlib import Path
from typing import Any, AsyncIterator, Dict, List, Optional

import tiktoken
from openjiuwen.core.context_engine.schema.messages import OffloadMixin
from openjiuwen.core.foundation.llm import (
    AssistantMessage,
    SystemMessage,
    UserMessage,
    BaseMessage,
    Model
)
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream import OutputSchema
from openjiuwen.core.session.stream.base import StreamMode
from openjiuwen.core.single_agent import AgentCard, ReActAgent

from jiuwenclaw.agentserver.permissions import (
    assess_command_risk_with_llm,
    check_tool_permissions,
    persist_external_directory_allow,
    persist_permission_allow_rule,
)
from jiuwenclaw.agentserver.permissions.checker import TOOL_PERMISSION_CHANNEL_ID
from jiuwenclaw.agentserver.permissions.models import PermissionLevel
from jiuwenclaw.agentserver.tools.todo_toolkits import TodoToolkit
from jiuwenclaw.evolution.service import EvolutionService
from jiuwenclaw.utils import get_agent_memory_dir, get_workspace_dir
from jiuwenclaw.config import get_config
from openjiuwen.core.single_agent.rail import InvokeInputs, AgentCallbackContext

logger = logging.getLogger(__name__)


# 加载流式输出配置
_react_config = get_config().get("react", {})
ANSWER_CHUNK_SIZE = _react_config.get("answer_chunk_size", 500)
STREAM_CHUNK_THRESHOLD = _react_config.get("stream_chunk_threshold", 50)
STREAM_CHARACTER_THRESHOLD = _react_config.get("stream_character_threshold", 2000)

_TODO_TOOL_NAMES = frozenset(
    ["todo_create", "todo_complete", "todo_insert", "todo_remove", "todo_list"]
)
_CMD_EVOLVE = "/evolve"
_CMD_SOLIDIFY = "/solidify"

_PERMISSION_APPROVAL_TIMEOUT = 300  # Auto-reject after 5 minute timeout


def _deduplicate_tools_by_name(tools: List[Any]) -> List[Any]:
    """Deduplicate tool infos by tool name while preserving order."""
    seen: set[str] = set()
    unique: List[Any] = []
    for tool in tools:
        name = getattr(tool, "name", None)
        if not name:
            unique.append(tool)
            continue
        if name in seen:
            continue
        seen.add(name)
        unique.append(tool)
    return unique


def _chunk_text(text: str, chunk_size: int) -> List[str]:
    """Split text into chunks of specified size at word/char boundaries.

    Args:
        text: Input text to chunk.
        chunk_size: Maximum characters per chunk.

    Returns:
        List of text chunks.
    """
    if not text or len(text) <= chunk_size:
        return [text] if text else []

    chunks: List[str] = []
    start = 0
    text_len = len(text)

    while start < text_len:
        end = start + chunk_size
        if end >= text_len:
            chunks.append(text[start:])
            break

        # Try to break at whitespace for cleaner chunks
        chunk = text[start:end]
        last_space = chunk.rfind(" ")
        last_newline = chunk.rfind("\n")
        break_point = max(last_space, last_newline)

        if break_point > chunk_size // 2:
            chunks.append(chunk[:break_point])
            start += break_point + 1
        else:
            chunks.append(chunk)
            start += chunk_size

    return chunks


class JiuClawReActAgent(ReActAgent):
    """Inherits ReActAgent, overrides invoke/stream to support todo.updated events."""

    def __init__(self, card: AgentCard) -> None:
        self._evolution_service: Optional[EvolutionService] = None
        self._pending_auto_evolution_history: Optional[List[Any]] = None
        self._pending_approvals: Dict[str, asyncio.Future] = {}  # request_id -> Future (权限审批)
        self._pending_permission_meta: Dict[str, dict] = {}  # request_id -> {tool_name, tool_args}
        super().__init__(card)
        self._stream_tasks: set[asyncio.Task] = set()
        self._pause_events: dict[str, asyncio.Event] = {}  # task_key -> event
        self._workspace_dir = get_workspace_dir()
        self._memory_dir = get_agent_memory_dir()
        self._agent_id: str = "main_agent"

    def set_workspace(self, workspace_dir: str, agent_id: str) -> None:
        """Set workspace directory and Agent ID."""
        self._workspace_dir = workspace_dir
        self._agent_id = agent_id

    async def _call_llm(
        self,
        messages: List,
        tools: Optional[List[ToolInfo]] = None,
        session: Optional[Session] = None,
        chunk_threshold: int = 10
    ) -> AssistantMessage:
        """Call LLM with messages and optional tools (streaming if session provided)

        Args:
            messages: Message list (BaseMessage or dict)
            tools: Optional tool definitions (List[ToolInfo])
            session: Optional Session for streaming output
            chunk_threshold: Number of chunks to accumulate before sending (default: 10)

        Returns:
            AssistantMessage from LLM
        """
        llm = self._get_llm()

        # If session provided, use streaming mode for real-time output
        if session is not None:
            return await self._call_llm_stream(
                llm, messages, tools, session, chunk_threshold
            )
        else:
            # Non-streaming mode for backward compatibility
            return await llm.invoke(
                model=self._config.model_name,
                messages=messages,
                tools=tools
            )

    async def _call_llm_stream(
        self,
        llm: Model,
        messages: List,
        tools: Optional[List[ToolInfo]],
        session: Session,
        chunk_threshold: int
    ) -> AssistantMessage:
        """Stream LLM invocation and send partial answers when content exceeds threshold

        Args:
            llm: Model instance
            messages: LLM input messages
            tools: Available tools
            session: Session context for streaming output
            chunk_threshold: Number of chunks to accumulate before sending

        Returns:
            AssistantMessage: Accumulated complete message from all chunks
        """
        accumulated_chunk = None
        chunk_count = 0
        last_sent_length = 0  # Track last sent content length

        try:
            async for chunk in llm.stream(messages, tools=tools, model=self._config.model_name):
                # Accumulate chunks using AssistantMessageChunk's __add__ method
                if accumulated_chunk is None:
                    accumulated_chunk = chunk
                else:
                    accumulated_chunk = accumulated_chunk + chunk

                # Stream output for reasoning content (always send)
                if chunk.reasoning_content:
                    stream_output = OutputSchema(
                        type="llm_reasoning",
                        index=chunk_count,
                        payload={
                            "output": chunk.reasoning_content,
                            "result_type": "answer"
                        }
                    )
                    await session.write_stream(stream_output)
                    chunk_count += 1

                # Check if accumulated content exceeds threshold
                if accumulated_chunk is not None and accumulated_chunk.content:
                    current_length = len(accumulated_chunk.content)
                    # Send partial answer only when threshold exceeded
                    if current_length - last_sent_length >= STREAM_CHARACTER_THRESHOLD:
                        # Send new content since last send
                        new_content = accumulated_chunk.content[last_sent_length:]
                        if new_content:
                            await session.write_stream(
                                OutputSchema(
                                    type="answer",
                                    index=chunk_count,
                                    payload={
                                        "output": {
                                            "output": new_content,
                                            "result_type": "answer",
                                            "partial": True,  # Mark as partial response
                                        },
                                        "result_type": "answer",
                                    },
                                )
                            )
                            chunk_count += 1
                            last_sent_length = current_length

            # Send any remaining content that didn't reach threshold
            if accumulated_chunk is not None and accumulated_chunk.content:
                current_length = len(accumulated_chunk.content)
                if current_length > last_sent_length:
                    remaining_content = accumulated_chunk.content[last_sent_length:]
                    if remaining_content:
                        await session.write_stream(
                            OutputSchema(
                                type="answer",
                                index=chunk_count,
                                payload={
                                    "output": {
                                        "output": remaining_content,
                                        "result_type": "answer",
                                        "partial": True,  # Mark as partial response
                                    },
                                    "result_type": "answer",
                                },
                            )
                        )
                        chunk_count += 1

            # Check for empty response
            if accumulated_chunk is None:
                raise ValueError("LLM returned empty response")

            # Convert accumulated chunk to AssistantMessage
            return AssistantMessage(
                role=accumulated_chunk.role or "assistant",
                content=accumulated_chunk.content or "",
                tool_calls=accumulated_chunk.tool_calls or [],
                usage_metadata=getattr(accumulated_chunk, 'usage_metadata', None),
                finish_reason=getattr(accumulated_chunk, 'finish_reason', None) or "stop",
                parser_content=getattr(accumulated_chunk, 'parser_content', None),
                reasoning_content=getattr(accumulated_chunk, 'reasoning_content', None),
            )

        except Exception as e:
            logger.error(f"Failed to stream LLM output: {e}")
            raise

    def pause(self) -> None:
        """Pause all running tasks (blocks at next checkpoint)."""
        for event in self._pause_events.values():
            event.clear()

    def resume(self) -> None:
        """Resume all paused tasks."""
        for event in self._pause_events.values():
            event.set()

    def set_evolution_service(self, service: Any) -> None:
        """Set the EvolutionService instance for online evolution."""
        self._evolution_service = service
        logger.info("[ReActAgent] evolution service set")

    async def invoke(
        self,
        inputs: Any,
        session: Optional[Session] = None,
        *,
        _pause_event: Optional[asyncio.Event] = None,
    ) -> Dict[str, Any]:
        """Custom ReAct loop implementation, replacing parent invoke().

        Same logic as openjiuwen ReActAgent.invoke(), additionally writes
        todo.updated OutputSchema after todo tool calls.
        """
        # Parse inputs
        if isinstance(inputs, dict):
            user_input = inputs.get("query")
            session_id = inputs.get("conversation_id", "")
            if user_input is None:
                raise ValueError("Input dict must contain 'query'")
        elif isinstance(inputs, str):
            user_input = inputs
            session_id = ""
        else:
            raise ValueError("Input must be dict with 'query' or str")
        
        stripped = user_input.strip()
        stripped = EvolutionService.extract_user_content(stripped)
        # Intercept slash commands (skip ReAct reasoning loop to save tokens)
        if stripped.startswith(_CMD_EVOLVE):
            if self._evolution_service is None:
                return {"output": "演进功能未启用。", "result_type": "error"}
            messages = await self._get_session_messages(session)
            return await self._evolution_service.handle_evolve_command(stripped, session, messages)
        if stripped.startswith(_CMD_SOLIDIFY):
            if self._evolution_service is None:
                return {"output": "演进功能未启用。", "result_type": "error"}
            return self._evolution_service.handle_solidify_command(stripped)

        # Build callback context for ability_manager.execute()
        invoke_inputs = InvokeInputs(query=user_input, conversation_id=session_id)
        ctx = AgentCallbackContext(agent=self, inputs=invoke_inputs, session=session)

        # Initialize context
        context = await self._init_context(session)
        await context.add_messages(UserMessage(content=user_input))

        # Build system messages once before loop
        system_messages = self._build_system_messages(session_id)

        tools = _deduplicate_tools_by_name(
            await self.ability_manager.list_tool_info()
        )

        # Validate and fix incomplete context before entering ReAct loop
        await self._fix_incomplete_tool_context(context)

        # ReAct loop
        for iteration in range(self._config.max_iterations):
            # Pause checkpoint: block here if paused until resume
            if _pause_event is not None:
                await _pause_event.wait()

            logger.info(
                "session %s, ReAct iteration %d/%d",
                session_id,
                iteration + 1,
                self._config.max_iterations,
            )

            context_window = await context.get_context_window(
                system_messages=[],
                tools=tools if tools else None,
            )

            history_messages = context_window.get_messages()
            history_snapshot = list(history_messages)
            # Filter out SystemMessage from history to avoid "System message must be at the beginning" error
            history_messages = [m for m in history_messages if not isinstance(m, SystemMessage)]
            messages = [*system_messages, *history_messages]

            compression_to_show = []
            uncompressed = []
            for message in messages:
                if isinstance(message, OffloadMixin):
                    original_message = await context.reloader_tool().invoke(
                        inputs={
                            "offload_handle": message.offload_handle,
                            "offload_type": message.offload_type
                        }
                    )
                    compression_to_show.append((message, original_message))
                else:
                    uncompressed.append(message)
            await self._emit_context_compression(session, compression_to_show, uncompressed)

            try:
                ai_message = await self._call_llm(
                    messages,
                    context_window.get_tools() or None,
                    session,  # Pass session for streaming
                )
            except Exception as e:
                logger.error(f"[JiuwenClaw] 尝试修复上下文")
                await self._fix_incomplete_tool_context(context)
                context_window = await context.get_context_window(
                    system_messages=[],
                    tools=tools if tools else None,
                )
                history_messages = context_window.get_messages()
                history_snapshot = list(history_messages)
                # Filter out SystemMessage from history to avoid "System message must be at the beginning" error
                history_messages = [m for m in history_messages if not isinstance(m, SystemMessage)]
                messages = [*system_messages, *history_messages]
                ai_message = await self._call_llm(
                    messages,
                    context_window.get_tools() or None,
                    session,  # Pass session for streaming
                )

            # Pause checkpoint: after LLM returns, before tool execution
            if _pause_event is not None:
                await _pause_event.wait()

            if ai_message.tool_calls:
                # Emit tool_call event
                if session is not None:
                    for tc in ai_message.tool_calls:
                        await self._emit_tool_call(session, tc)

                # ---- 权限检查：在执行工具前逐一检查权限 ----

                allowed_tool_calls, denied_results = await check_tool_permissions(
                    ai_message.tool_calls,
                    channel_id=TOOL_PERMISSION_CHANNEL_ID.get(),
                    session_id=session_id or None,
                    session=session,
                    request_approval_callback=self._request_permission_approval,
                )

                # Add assistant message to context before tool execution
                ai_msg_for_context = AssistantMessage(
                    content=ai_message.content,
                    tool_calls=ai_message.tool_calls,
                )
                await context.add_messages(ai_msg_for_context)

                tool_messages_added = False
                try:
                    # 先把被拒绝的工具调用写入 ToolMessage
                    from openjiuwen.core.foundation.llm import ToolMessage as _ToolMsg
                    for tc, deny_msg in denied_results:
                        tool_call_id = getattr(tc, "id", "")
                        await context.add_messages(_ToolMsg(
                            content=deny_msg,
                            tool_call_id=tool_call_id,
                        ))
                        if session is not None:
                            await self._emit_tool_result(session, tc, deny_msg)

                    # 执行被允许的工具调用
                    if allowed_tool_calls:
                        results = await self.ability_manager.execute(
                            ctx=ctx, tool_call=allowed_tool_calls, session=session
                        )

                        for i, (_result, tool_msg) in enumerate(results):
                            tc = allowed_tool_calls[i] if i < len(allowed_tool_calls) else None
                            if tc is not None:
                                tool_msg = self._maybe_inject_body_experience(tc, tool_msg)
                            await context.add_messages(tool_msg)
                            if session is not None:
                                await self._emit_tool_result(session, tc, _result)
                    
                    tool_messages_added = True

                    # Detect if todo tool was called, emit todo.updated if so
                    todo_called = any(
                        tc.name in _TODO_TOOL_NAMES for tc in ai_message.tool_calls
                    )
                    if todo_called and session is not None and session_id:
                        await self._emit_todo_updated(session, session_id)
                except (Exception, asyncio.CancelledError):
                    # On exception or cancellation, add placeholder tool messages to keep context valid
                    if not tool_messages_added:
                        from openjiuwen.core.foundation.llm import ToolMessage
                        for tc in ai_message.tool_calls:
                            tool_call_id = getattr(tc, "id", "")
                            error_msg = f"Tool execution interrupted or failed: {tc.name}"
                            await context.add_messages(ToolMessage(
                                content=error_msg,
                                tool_call_id=tool_call_id
                            ))
                    raise
            else:
                # No tool calls: add assistant message directly to context
                ai_msg_for_context = AssistantMessage(
                    content=ai_message.content,
                    tool_calls=ai_message.tool_calls,
                )
                await context.add_messages(ai_msg_for_context)

                # Store auto-scan context for stream() to handle
                if (
                    self._evolution_service is not None
                    and self._evolution_service.auto_scan
                    and history_snapshot
                ):
                    self._pending_auto_evolution_history = list(history_snapshot)

                return {
                    "output": ai_message.content,
                    "result_type": "answer",
                    "_streamed": session is not None,  # Mark if content was streamed
                }

        return {
            "output": "Max iterations reached without completion",
            "result_type": "error",
        }

    async def stream(
        self,
        inputs: Any,
        session: Optional[Session] = None,
        stream_modes: Optional[List[StreamMode]] = None,
    ) -> AsyncIterator[Any]:
        """Override stream to support todo.updated events in ReAct loop.

        Args:
            inputs: {"query": "...", "conversation_id": "..."} or str.
            session: Session object for streaming pipeline.
            stream_modes: Stream output modes (optional).

        Yields:
            OutputSchema objects.
        """
        if session is not None:
            await session.pre_run()

        # Create independent pause event for this stream call (new tasks unaffected by previous pauses)
        task_key = f"stream_{id(asyncio.current_task())}"
        pause_event = asyncio.Event()
        pause_event.set()  # Initially set to running state
        self._pause_events[task_key] = pause_event

        async def stream_process() -> None:
            try:
                self._pending_auto_evolution_history = None
                final_result = await self.invoke(inputs, session, _pause_event=pause_event)

                if session is not None:
                    # Extract content and check if it was already streamed
                    output_content = ""
                    was_streamed = False

                    if isinstance(final_result, dict):
                        output_content = final_result.get("output", "")
                        if isinstance(output_content, dict):
                            output_content = output_content.get("output", "")
                        was_streamed = final_result.get("_streamed", False)

                    if was_streamed:
                        # Content was already streamed via _call_llm_stream
                        # Send final answer marker only
                        await session.write_stream(
                            OutputSchema(
                                type="answer",
                                index=0,
                                payload={
                                    "output": {
                                        "output": "",
                                        "result_type": "answer",
                                        "streamed": True,  # Mark that content was already streamed
                                    },
                                    "result_type": "answer",
                                },
                            )
                        )
                    elif output_content and len(output_content) > ANSWER_CHUNK_SIZE:
                        # Short content that wasn't streamed: split into chunks and send
                        chunks = _chunk_text(output_content, ANSWER_CHUNK_SIZE)
                        for i, chunk in enumerate(chunks):
                            if i == 0:
                                # First chunk: send as answer type
                                await session.write_stream(
                                    OutputSchema(
                                        type="answer",
                                        index=0,
                                        payload={
                                            "output": {
                                                "output": chunk,
                                                "result_type": "answer",
                                                "chunked": True,
                                                "chunk_index": i,
                                                "total_chunks": len(chunks),
                                            },
                                            "result_type": "answer",
                                        },
                                    )
                                )
                            else:
                                # Subsequent chunks: send as content_chunk
                                await session.write_stream(
                                    OutputSchema(
                                        type="content_chunk",
                                        index=0,
                                        payload={"content": chunk},
                                    )
                                )
                    else:
                        # Short content: send as single answer
                        await session.write_stream(
                            OutputSchema(
                                type="answer",
                                index=0,
                                payload={
                                    "output": final_result,
                                    "result_type": "answer",
                                },
                            )
                        )

                # Handle auto-scan evolution after answer
                history = self._pending_auto_evolution_history

                # If no auto evolution, send processing_complete directly
                if history is None and self._evolution_service is not None and session is not None:
                    await session.write_stream(
                        OutputSchema(
                            type="processing_complete",
                            index=0,
                            payload={},
                        )
                    )

                if history is not None and self._evolution_service is not None and session is not None:
                    # Signal frontend that main processing is done before evolution starts,
                    # so new user input is treated as a normal submit (not interrupt).
                    await session.write_stream(
                        OutputSchema(
                            type="processing_complete",
                            index=0,
                            payload={},
                        )
                    )
                    try:
                        await self._evolution_service.run_auto_evolution(session, history)
                    except Exception as e:
                        logger.warning("[ReActAgent] auto evolution error: %s", e)
                self._pending_auto_evolution_history = None
            except asyncio.CancelledError:
                logger.info("stream_process cancelled")
            except Exception as e:
                logger.exception("stream error: %s", e)
                await session.write_stream(
                            OutputSchema(
                                type="answer",
                                index=0,
                                payload={
                                    "output": str(e),
                                    "result_type": "error",
                                },
                            )
                        )
            finally:
                if session is not None:
                    await self.context_engine.save_contexts(session)
                    await session.post_run()

        task = asyncio.create_task(stream_process())
        self._stream_tasks.add(task)

        try:
            if session is not None:
                async for result in session.stream_iterator():
                    yield result

            await task
        finally:
            self._stream_tasks.discard(task)
            self._pause_events.pop(task_key, None)

    async def _request_permission_approval(
        self,
        session: Session,
        tool_call: Any,
        result: Any,
    ) -> str:
        """Request user approval for a tool call via chat.ask_user_question.

        Returns:
            "allow_once" | "allow_always" | "deny"
            Timeout auto-returns "deny".
        """
        import json as _json

        request_id = f"perm_approve_{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_approvals[request_id] = future

        tool_name = getattr(tool_call, "name", "")
        tool_args = getattr(tool_call, "arguments", {})
        if isinstance(tool_args, str):
            try:
                tool_args = _json.loads(tool_args)
            except Exception:
                tool_args = {}

        #risk = assess_command_risk_static(tool_name, tool_args)
        risk = await assess_command_risk_with_llm(
            self._get_llm(), self._config.model_name, tool_name, tool_args
        )

        args_preview = ""
        try:
            raw = _json.dumps(tool_args, ensure_ascii=False, indent=2)
            args_preview = raw[:500] if len(raw) > 500 else raw
        except Exception:
            args_preview = str(tool_args)[:500]

        always_allow_hint = ""
        #shell_injection_warning = ""
        if tool_name == "mcp_exec_command":
            cmd = tool_args.get("command", tool_args.get("cmd", "")) if isinstance(tool_args, dict) else ""
            if cmd:
                # import re as _re
                # _ops_re = _re.compile(r'[;&|`<>]|\$[({]|\r?\n')
                # if _ops_re.search(str(cmd)):
                #     shell_injection_warning = (
                #         "\n\n> **⚠ 安全警告：** 该命令包含 shell 操作符"
                #         "（如 `&&` `;` `|` 等），可能存在命令注入风险，请仔细核查\n"
                #     )
                always_allow_hint = (
                    f"\n\n> 选择「总是允许」将自动放行 `{cmd}` 命令"
                )
        elif tool_name:
            always_allow_hint = f"\n\n> 选择「总是允许」将自动放行所有 `{tool_name}` 调用"

        question_text = (
            f"**工具 `{tool_name}` 需要授权才能执行**\n\n"
            f"**安全风险评估：** {risk['icon']} **{risk['level']}风险**\n\n"
            f"> {risk['explanation']}\n\n"
        )
        #question_text += shell_injection_warning
        if args_preview and args_preview != "{}":
            question_text += f"参数：\n```json\n{args_preview}\n```\n"
        question_text += f"\n匹配规则：`{result.matched_rule or 'N/A'}`"
        question_text += always_allow_hint

        meta: dict = {
            "tool_name": tool_name,
            "tool_args": tool_args,
        }
        if result.matched_rule and "external_directory" in result.matched_rule:
            meta["external_paths"] = getattr(result, "external_paths", None) or []
        self._pending_permission_meta[request_id] = meta

        try:
            await session.write_stream(
                OutputSchema(
                    type="chat.ask_user_question",
                    index=0,
                    payload={
                        "request_id": request_id,
                        "questions": [
                            {
                                "question": question_text,
                                "header": "权限审批",
                                "options": [
                                    {"label": "本次允许", "description": "仅本次授权执行"},
                                    {"label": "总是允许", "description": "记住该规则，以后自动放行"},
                                    {"label": "拒绝", "description": "拒绝执行此工具"},
                                ],
                                "multi_select": False,
                            }
                        ],
                    },
                )
            )
        except Exception:
            logger.debug("_request_permission_approval: popup send failed", exc_info=True)
            self._pending_approvals.pop(request_id, None)
            self._pending_permission_meta.pop(request_id, None)
            return "deny"

        try:
            return await asyncio.wait_for(future, timeout=_PERMISSION_APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.info(
                "[ReActAgent] Permission approval timeout (tool=%s, id=%s), auto-rejecting",
                tool_name, request_id,
            )
            return "deny"
        finally:
            self._pending_approvals.pop(request_id, None)
            self._pending_permission_meta.pop(request_id, None)

    async def _emit_tool_call(self, session: Session, tool_call: Any) -> None:
        """Emit tool_call OutputSchema, notify frontend of tool call start."""
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

    async def _emit_tool_result(self, session: Session, tool_call: Any, result: Any) -> None:
        """Emit tool_result OutputSchema, notify frontend of tool execution result."""
        try:
            # todo 工具结果待优化
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
            await session.write_stream(
                OutputSchema(
                    type="thinking",
                    index=0,
                    payload={},
                )
            )
        except Exception:
            logger.debug("tool_result emit failed", exc_info=True)

    async def _emit_todo_updated(self, session: Session, session_id: str) -> None:
        """Read current todo list and emit todo.updated OutputSchema."""
        try:
            from datetime import datetime, timezone

            todo_toolkit = TodoToolkit(session_id=session_id)
            tasks = todo_toolkit._load_tasks()

            # Map backend TodoTask fields to frontend TodoItem format
            status_mapping = {
                "waiting": "pending",
                "running": "in_progress",
                "completed": "completed",
                "cancelled": "pending",
            }

            now = datetime.now(timezone.utc).isoformat()

            todos = []
            for t in tasks:
                todos.append({
                    "id": str(t.idx),
                    "content": t.tasks,
                    "activeForm": t.tasks,
                    "status": status_mapping.get(t.status.value, "pending"),
                    "createdAt": now,
                    "updatedAt": now,
                })

            await session.write_stream(
                OutputSchema(
                    type="todo.updated",
                    index=0,
                    payload={"todos": todos},
                )
            )
        except Exception:
            logger.debug("todo.updated emit failed", exc_info=True)

    async def _emit_context_compression(self, session: Session, compression_to_show, uncompressed) -> None:
        """Emit current context compression content."""
        try:
            try:
                encoding = tiktoken.get_encoding("cl100k_base")
                tokens_compressed = 0
                tokens_full = 0
                token_uncompressed = 0
                for message in uncompressed:
                    token_uncompressed += len(encoding.encode(message.content))

                for c, o in compression_to_show:
                    tokens_compressed += len(encoding.encode(c.content))
                    tokens_full += len(encoding.encode(o))
                pre_compression = tokens_full + token_uncompressed
                post_compression = tokens_compressed + token_uncompressed
                rate = (1 - post_compression / pre_compression) * 100
            except Exception:
                tokens_compressed = 0
                tokens_full = 0
                token_uncompressed = 0
                for message in uncompressed:
                    token_uncompressed += len(message.content)

                for c, o in compression_to_show:
                    tokens_compressed += len(c.content)
                    tokens_full += len(o)

                pre_compression = tokens_full + token_uncompressed
                post_compression = tokens_compressed + token_uncompressed
                rate = (1 - post_compression / pre_compression) * 100

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

    async def _fix_incomplete_tool_context(self, context: Any) -> None:
        """Validate and fix incomplete context messages before entering ReAct loop.

        If an assistant message with tool_calls exists without corresponding tool messages,
        add placeholder tool messages to keep context valid for OpenAI API.
        """
        from openjiuwen.core.foundation.llm import ToolMessage, AssistantMessage

        try:
            messages = context.get_messages()
            len_messages = len(messages)
            messages = context.pop_messages(size=len_messages)
            tool_message_cache = {}
            tool_id_cache = []  # 与assistant一致
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
                        for tc in tool_id_cache:
                            tool_name = tc["tool_name"]
                            tool_call_id = tc["tool_call_id"]
                            if tool_call_id in tool_message_cache:
                                await context.add_messages(tool_message_cache[tool_call_id])
                            else:
                                await context.add_messages(ToolMessage(
                                    content=f"[工具执行被中断] 工具 {tool_name} 执行过程中被用户打断，没有执行结果。",
                                    tool_call_id=tool_call_id
                                ))
                        tool_id_cache = []
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
                    for tc in tool_id_cache:
                        tool_name = tc["tool_name"]
                        tool_call_id = tc["tool_call_id"]
                        if tool_call_id in tool_message_cache:
                            await context.add_messages(tool_message_cache[tool_call_id])
                        else:
                            await context.add_messages(ToolMessage(
                                content=f"[工具执行被中断] 工具 {tool_name} 执行过程中被用户打断，没有执行结果。",
                                tool_call_id=tool_call_id
                            ))
                    tool_id_cache = []
                    await context.add_messages(messages[i])
        except Exception as e:
            logger.warning("Failed to fix incomplete tool context: %s", e)

    def resolve_evolution_approval(self, request_id: str, answers: list) -> bool:
        """解析用户审批：权限审批由本 agent 处理，演进审批委托 EvolutionService."""
        if request_id.startswith("perm_approve_"):
            return self._resolve_permission_approval(request_id, answers)
        if self._evolution_service is not None:
            return self._evolution_service.resolve_approval(request_id, answers)
        return False

    def _resolve_permission_approval(self, request_id: str, answers: list) -> bool:
        """解析权限审批（总是允许/本次允许/拒绝）并 resolve Future."""
        future = self._pending_approvals.get(request_id)
        if future is None or future.done():
            return False
        selected = (
            answers[0].get("selected_options", [])
            if answers and isinstance(answers[0], dict)
            else []
        )
        if "总是允许" in selected:
            meta = self._pending_permission_meta.get(request_id, {})
            if meta:
                external_paths = meta.get("external_paths") or []
                if external_paths:
                    persist_external_directory_allow(external_paths)
                else:
                    persist_permission_allow_rule(
                        meta.get("tool_name", ""),
                        meta.get("tool_args", {}),
                    )
            future.set_result("allow_always")
            logger.info("[ReActAgent] Permission approval: request_id=%s decision=allow_always", request_id)
        elif "本次允许" in selected:
            future.set_result("allow_once")
            logger.info("[ReActAgent] Permission approval: request_id=%s decision=allow_once", request_id)
        else:
            future.set_result("deny")
            logger.info("[ReActAgent] Permission approval: request_id=%s decision=deny", request_id)
        return True

    def _get_skill_messages(self) -> List[SystemMessage]:
        """Build Skill summary SystemMessage list.

        For each skill, its description is listed, and any pending description
        experiences are appended directly after it.  Body experiences are NOT
        included here (they are solidified into SKILL.md).
        """
        prompt_parts: List[str] = []

        if self._skill_util is not None and self._skill_util.has_skill():
            skill_info = self._skill_util.get_skill_prompt()
            lines = skill_info.split("\n\n")[-1].strip().split("\n")
            skill_lines = [line for line in lines[1:-1] if line.strip()]

            if skill_lines:
                header = (
                    "# Skills\n"
                    "You are equipped with a set of skills that include instructions may help you "
                    "with current task. Before attempting any task, read the relevant skill document "
                    "(SKILL.MD) using view_file and follow its workflow.\n\n"
                    "Here are the skills available:\n"
                )
                augmented: List[str] = []
                for line in skill_lines:
                    aug_line = f"- {line}"
                    if self._evolution_service is not None:
                        m = re.search(r"Skill name:\s*(\S+?);", line)
                        if m:
                            skill_name = m.group(1)
                            desc_text = self._evolution_service.store.format_desc_experience_text(skill_name)
                            if desc_text:
                                aug_line += f"\n  Skill description patch: {desc_text}"
                    augmented.append(aug_line)
                prompt_parts.append(header + "\n".join(augmented))

        if not prompt_parts:
            return []

        return [SystemMessage(content="\n\n".join(prompt_parts))]

    _SKILL_MD_RE = re.compile(r"[/\\]([^/\\]+)[/\\]SKILL\.md", re.IGNORECASE)

    def _maybe_inject_body_experience(self, tc: Any, tool_msg: Any) -> Any:
        """Append body-experience text when the agent reads a SKILL.md via view_file."""
        if self._evolution_service is None:
            return tool_msg
        if getattr(tc, "name", "") != "view_file":
            return tool_msg

        try:
            import json as _json
            args = _json.loads(tc.arguments) if isinstance(tc.arguments, str) else tc.arguments
            file_path: str = args.get("file_path", "")
        except Exception:
            return tool_msg

        m = self._SKILL_MD_RE.search(file_path)
        if not m:
            return tool_msg

        skill_name = m.group(1)
        body_text = self._evolution_service.store.format_body_experience_text(skill_name)
        if not body_text:
            return tool_msg

        original = tool_msg.content if isinstance(tool_msg.content, str) else str(tool_msg.content)
        tool_msg.content = original + body_text
        logger.info("[ReActAgent] injected body experience for skill=%s", skill_name)
        return tool_msg

    async def _get_session_messages(self, session: Optional[Any]) -> List[Any]:
        """Get raw historical message list from session.

        Returns unprocessed BaseMessage objects.
        """
        if session is None:
            return []
        try:
            context = await self._init_context(session)
            context_window = await context.get_context_window(system_messages=[], tools=None)
            return list(context_window.get_messages()) if hasattr(context_window, "get_messages") else []
        except Exception as exc:
            logger.warning("Failed to get session messages: %s", exc)
            return []

    def _build_system_messages(self, session_id: str) -> List[SystemMessage]:
        """Build system messages: prompt_template + workspace + memory + skill summary.

        Order:
          1. prompt_template
          2. workspace_prompt
          3. memory_prompt
          4. skill_prompt + evolution summary
        """
        # 1. base system messages
        base: List[SystemMessage] = [
            SystemMessage(role=msg["role"], content=msg["content"])
            for msg in (self._config.prompt_template or [])
            if msg.get("role") == "system"
        ]

        if not base:
            return []

        # Build append content
        content_parts: List[str] = []

        # 4. skill_prompt + evolution summary
        skill_msgs = self._get_skill_messages()
        if skill_msgs:
            content_parts.extend(m.content for m in skill_msgs if m.content)

        # Merge all content into the last system message
        merged_content = "\n\n".join([base[-1].content or ""] + content_parts)
        merged = SystemMessage(role=base[-1].role, content=merged_content)
        return [*base[:-1], merged] if len(base) > 1 else [merged]