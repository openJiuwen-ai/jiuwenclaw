# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""JiuClawReActAgent - Inherits openjiuwen ReActAgent, overrides invoke/stream.

Emits todo.updated events after todo tool calls for frontend real-time sync.
Sends evolution approval requests to user via chat.ask_user_question (keep/undo).
"""
from __future__ import annotations

import asyncio
import importlib.util
import sys
import uuid
from dataclasses import dataclass, field
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

from jiuwenclaw.agentserver.tools.todo_toolkits import TodoToolkit
from jiuwenclaw.agentserver.prompt_builder import build_system_prompt
from jiuwenclaw.evolution.skill_call_operator import SkillCallOperator
from jiuwenclaw.evolution.skill_optimizer import SkillOptimizer
from jiuwenclaw.utils import logger, USER_WORKSPACE_DIR
from jiuwenclaw.config import get_config


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

_EVOLUTION_APPROVAL_TIMEOUT = 300  # Auto-keep after 5 minute timeout


@dataclass
class _InvokeEvolutionContext:
    """Context passed from invoke() to stream() for auto-scan."""
    history_snapshot: List[Any] = field(default_factory=list)
    should_auto_scan: bool = False


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
        self._online_optimizers: List[Any] = []
        self._pending_approvals: Dict[str, asyncio.Future] = {}  # request_id -> Future[bool]
        self._pending_evolution_context: Optional[_InvokeEvolutionContext] = None
        super().__init__(card)
        self._stream_tasks: set[asyncio.Task] = set()
        self._pause_events: dict[str, asyncio.Event] = {}  # task_key -> event
        self._workspace_dir = USER_WORKSPACE_DIR / "workspace"
        self._memory_dir = self._workspace_dir / "agent"
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

    def register_online_optimizer(self, optimizer: Any) -> "JiuClawReActAgent":
        """Register online evolution Optimizer (chainable).

        Args:
            optimizer: SkillOptimizer instance.
        """
        self._online_optimizers.append(optimizer)
        logger.info("register optimizer: %s", type(optimizer).__name__)
        return self

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
        # Intercept slash commands (skip ReAct reasoning loop to save tokens)
        if stripped.startswith(_CMD_EVOLVE):
            return await self._handle_evolve_command(stripped, session)
        if stripped.startswith(_CMD_SOLIDIFY):
            return await self._handle_solidify_command(stripped)

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

                # Add assistant message to context before tool execution
                ai_msg_for_context = AssistantMessage(
                    content=ai_message.content,
                    tool_calls=ai_message.tool_calls,
                )
                await context.add_messages(ai_msg_for_context)

                tool_messages_added = False
                try:
                    results = await self.ability_manager.execute(
                        ai_message.tool_calls, session
                    )

                    for i, (_result, tool_msg) in enumerate(results):
                        await context.add_messages(tool_msg)
                        # Emit tool_result event
                        if session is not None:
                            tc = ai_message.tool_calls[i] if i < len(ai_message.tool_calls) else None
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
                has_auto_scan = any(
                    getattr(opt, "auto_scan", False) for opt in self._online_optimizers
                )
                if has_auto_scan and history_snapshot:
                    self._pending_evolution_context = _InvokeEvolutionContext(
                        history_snapshot=list(history_snapshot),
                        should_auto_scan=True,
                    )

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
                self._pending_evolution_context = None
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

                # Handle auto-scan approval after answer
                ctx = self._pending_evolution_context
                if ctx is not None and ctx.should_auto_scan and session is not None:
                    try:
                        await self._run_auto_evolution_with_approval(session, ctx)
                    except Exception as e:
                        logger.warning("[ReActAgent] auto_scan approval error: %s", e)
                self._pending_evolution_context = None
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
            
    def get_operators(self) -> Dict[str, SkillCallOperator]:
        """Returns single SkillCallOperator (aligned with ToolCallOperator pattern: one manages all Skills).

        Returns:
            { "skill_call": SkillCallOperator } or {} (when no optimizer registered).
        """
        opt = self._get_skill_optimizer()
        if opt is None or not opt.skills_base_dir:
            return {}
        op = SkillCallOperator(
            skills_base_dir=opt.skills_base_dir,
            evolution_manager=opt.evolution_manager,
        )
        return {op.operator_id: op}

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

    async def _request_evolution_approval(
        self,
        session: Session,
        skill_name: str,
        entry: Any,
    ) -> bool:
        """Request user approval via chat.ask_user_question.

        Returns:
            True = keep, False = discard.
            Timeout (5 min) auto-returns True.
        """
        request_id = f"evolve_approve_{uuid.uuid4().hex[:8]}"
        loop = asyncio.get_event_loop()
        future: asyncio.Future = loop.create_future()
        self._pending_approvals[request_id] = future

        content_preview = getattr(getattr(entry, "change", None), "content", "")[:1000]
        section = getattr(getattr(entry, "change", None), "section", "")

        try:
            await session.write_stream(
                OutputSchema(
                    type="chat.ask_user_question",
                    index=0,
                    payload={
                        "request_id": request_id,
                        "questions": [
                            {
                                "question": (
                                    f"**Skill '{skill_name}' 演进生成了新内容：**\n\n"
                                    f"{content_preview}"
                                ),
                                "header": "演进审批",
                                "options": [
                                    {"label": "接收", "description": "保留此演进经验"},
                                    {"label": "拒绝", "description": "丢弃此演进经验"},
                                ],
                                "multi_select": False,
                            }
                        ],
                    },
                )
            )
        except Exception:
            logger.debug("_request_evolution_approval: popup send failed", exc_info=True)
            self._pending_approvals.pop(request_id, None)
            return True  # Default keep on send failure

        try:
            return await asyncio.wait_for(future, timeout=_EVOLUTION_APPROVAL_TIMEOUT)
        except asyncio.TimeoutError:
            logger.info(
                "[ReActAgent] Evolution approval timeout (skill=%s, id=%s), auto-keeping",
                skill_name,
                request_id,
            )
            return True  # Auto-keep on timeout
        finally:
            self._pending_approvals.pop(request_id, None)

    def resolve_evolution_approval(self, request_id: str, answers: list) -> bool:
        """Parse user answer and resolve corresponding Future.

        Called by interface.py when receiving chat.user_answer.

        Returns:
            True = successfully resolved, False = request not found or already completed.
        """
        future = self._pending_approvals.get(request_id)
        if future is None or future.done():
            return False

        keep = (
            "接收" in answers[0].get("selected_options", [])
            if answers and isinstance(answers[0], dict)
            else False
        )
        future.set_result(keep)
        logger.info(
            "[ReActAgent] Evolution approval resolved: request_id=%s 接收=%s",
            request_id,
            keep,
        )
        return True

    async def _run_auto_evolution_with_approval(
        self,
        session: Session,
        ctx: _InvokeEvolutionContext,
    ) -> None:
        """Execute auto-scan + generate + approval flow in stream()."""
        skill_ops = self.get_operators()
        if not skill_ops:
            logger.info("[ReActAgent] _run_auto_evolution_with_approval: no skill_ops, skip")
            return

        messages = self._parse_messages(ctx.history_snapshot)
        skill_names = self._get_skill_names()

        for opt in self._online_optimizers:
            auto_scan = getattr(opt, "auto_scan", False)
            if not auto_scan or not callable(getattr(opt, "auto_scan_generate", None)):
                continue

            try:
                entries = await opt.auto_scan_generate(messages, skill_ops, skill_names)
            except Exception as exc:
                logger.warning("[ReActAgent] auto_scan_generate error: %s", exc)
                continue

            for skill_name, entry in entries.items():
                try:
                    keep = await self._request_evolution_approval(session, skill_name, entry)
                    if keep:
                        opt.evolution_manager.append_entry(skill_name, entry)
                        logger.info(
                            "[ReActAgent] Evolution kept: skill=%s id=%s",
                            skill_name,
                            entry.id,
                        )
                    else:
                        logger.info(
                            "[ReActAgent] Evolution discarded: skill=%s id=%s",
                            skill_name,
                            entry.id,
                        )
                except Exception as exc:
                    logger.warning(
                        "[ReActAgent] Approval flow error (skill=%s): %s", skill_name, exc
                    )

    def _get_skill_optimizer(self) -> Optional[SkillOptimizer]:
        """Returns first registered SkillOptimizer (with signal_backward method)."""
        for opt in self._online_optimizers:
            if callable(getattr(opt, "signal_backward", None)):
                return opt
        return None

    def _get_skill_names(self) -> List[str]:
        """Dynamically scan existing Skill names from optimizer.skills_base_dir.

        No manual registration needed - optimizer automatically senses all subdirs under skills_base_dir.
        Skills created at runtime via new_skill are also automatically included.
        """
        opt = self._get_skill_optimizer()
        if opt is None:
            return []
        base = Path(opt.skills_base_dir)
        if not base.exists():
            return []
        return [d.name for d in base.iterdir() if d.is_dir() and not d.name.startswith("_")]

    def _get_skill_messages(self) -> List[SystemMessage]:
        """Build Skill summary SystemMessage list.

        Includes:
          1. skill_prompt
          2. evolution summaries
        """
        prompt_parts: List[str] = []

        # 1. skill_prompt (skill list description)
        if self._skill_util is not None and self._skill_util.has_skill():
            skill_info = self._skill_util.get_skill_prompt()
            # skill 列表在 prompt 最后一段（双换行后）
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
                prompt_parts.append(header + "\n".join(f"- {line}" for line in skill_lines))

        # 2. Skill evolution summary
        skill_ops = self.get_operators()
        op = skill_ops.get(SkillCallOperator.OPERATOR_ID)
        if op is not None:
            summaries = op.get_all_evolution_summaries(self._get_skill_names())
            if summaries:
                prompt_parts.append(summaries)

        if not prompt_parts:
            return []

        return [SystemMessage(content="\n\n".join(prompt_parts))]

    async def _get_session_messages(self, session: Optional[Any]) -> List[dict]:
        """Get historical message list from session (safely compatible with different Session implementations).

        Retrieves via context_window with fallback.
        """
        if session is None:
            return []
        try:
            # Prefer: get context window via context_engine
            context = await self._init_context(session)
            cw = await context.get_context_window(system_messages=[], tools=None)
            msgs = cw.get_messages() if hasattr(cw, "get_messages") else []
            return self._parse_messages(msgs)
        except Exception as exc:
            logger.warning(" Failed to get session messages: %s", exc)
            return []

    @staticmethod
    def _parse_messages(messages: List[BaseMessage]) -> List[dict]:
        result = []
        for msg in messages:
            if isinstance(msg, dict):
                result.append(msg)
            elif hasattr(msg, "role"):
                d: dict = {
                    "role": getattr(msg, "role", ""),
                    "content": str(getattr(msg, "content", "") or ""),
                }
                if tool_calls := getattr(msg, "tool_calls", None):
                    d["tool_calls"] = [
                        {
                            "id": getattr(tc, "id", ""),
                            "name": getattr(tc, "name", ""),
                            "arguments": getattr(tc, "arguments", ""),
                        }
                        for tc in tool_calls
                    ]
                if name := getattr(msg, "name", None):
                    d["name"] = name
                result.append(d)
        return result

    async def _handle_evolve_command(
        self,
        query: str,
        session: Optional[Any],
    ) -> Dict[str, Any]:
        """/evolve [list | <skill_name>] command handler."""
        skill_opt = self._get_skill_optimizer()
        if skill_opt is None:
            return {
                "output": (
                    "SkillOptimizer not registered, cannot execute online evolution.\n"
                    "Please set `evolution.enabled: true` in config.yaml and restart service,\n"
                    "or call agent.register_online_optimizer(SkillOptimizer(...))."
                ),
                "result_type": "error",
            }

        skill_names = self._get_skill_names()

        # Parse arguments
        parts = query.split(maxsplit=1)
        skill_arg = parts[1].strip() if len(parts) > 1 else ""

        # /evolve or /evolve list -> list all Skill evolution records
        if not skill_arg or skill_arg == "list":
            if not skill_names:
                return {
                    "output": "当前 skills_base_dir 下未找到任何 Skill 目录。",
                    "result_type": "answer",
                }
            summary = skill_opt.list_summary(skill_names)
            return {
                "output": f"**Skills 演进记录：**\n\n{summary}",
                "result_type": "answer",
            }

        # /evolve <skill_name>
        skill_name = skill_arg
        if skill_name not in skill_names:
            available = "、".join(skill_names) or "（无可用 Skill）"
            return {
                "output": (
                    f"在 skills_base_dir 下未找到 Skill '{skill_name}'。\n"
                    f"当前可用 Skill：{available}\n"
                    f"可使用 /evolve list 查看所有记录。"
                ),
                "result_type": "error",
            }

        messages = await self._get_session_messages(session)
        entry = await skill_opt.evolve_generate(skill_name, messages)

        if entry is None:
            return {
                "output": (
                    f"当前对话未发现明确的演进信号（无工具执行失败、无用户纠正）。\n"
                ),
                "result_type": "answer",
            }

        if session is not None:
            # Request user approval via popup
            keep = await self._request_evolution_approval(session, skill_name, entry)
            if keep:
                skill_opt.evolution_manager.append_entry(skill_name, entry)
                return {
                    "output": (
                        f"✓ 已记录演进经验到 Skill '{skill_name}'：\n"
                        f"  **[{entry.change.section}]** {entry.change.content[:200]}\n\n"
                        f"（evolutions.json 已更新，自动生效；"
                        f"可使用 `/solidify {skill_name}` 将经验固化到 SKILL.md 本体）"
                    ),
                    "result_type": "answer",
                }
            else:
                return {
                    "output": f"已丢弃 Skill '{skill_name}' 的演进内容，evolutions.json 未变更。",
                    "result_type": "answer",
                }
        else:
            # Fallback for no session (non-streaming call): save directly
            skill_opt.evolution_manager.append_entry(skill_name, entry)
            return {
                "output": (
                    f"✓ 已记录演进经验到 Skill '{skill_name}'：\n"
                    f"  **[{entry.change.section}]** {entry.change.content[:200]}"
                ),
                "result_type": "answer",
            }

    async def _handle_solidify_command(self, query: str) -> Dict[str, Any]:
        """/solidify <skill_name> command handler."""
        skill_opt = self._get_skill_optimizer()
        if skill_opt is None:
            return {
                "output": "演进功能未启用，无法执行 solidify。",
                "result_type": "error",
            }
        parts = query.split(maxsplit=1)
        skill_name = parts[1].strip() if len(parts) > 1 else ""
        if not skill_name:
            return {
                "output": "请指定 Skill 名称：`/solidify <skill_name>`",
                "result_type": "error",
            }
        result_msg = skill_opt.solidify(skill_name)
        return {"output": result_msg, "result_type": "answer"}

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

        # 2. workspace_prompt
        workspace = self._workspace_dir
        content_parts.append(f"# Workspace\nYour temporal working directory is: {workspace}\n"
                             "Write or save all files under this dir.")

        # 3. memory_prompt
        memory_prompt = build_system_prompt(
            workspace_dir=str(self._memory_dir),
            agent_id=self._agent_id,
        )
        if memory_prompt:
            content_parts.append(memory_prompt)

        # 4. skill_prompt + evolution summary
        skill_msgs = self._get_skill_messages()
        if skill_msgs:
            content_parts.extend(m.content for m in skill_msgs if m.content)

        # Merge all content into the last system message
        merged_content = "\n\n".join([base[-1].content or ""] + content_parts)
        merged = SystemMessage(role=base[-1].role, content=merged_content)
        return [*base[:-1], merged] if len(base) > 1 else [merged]