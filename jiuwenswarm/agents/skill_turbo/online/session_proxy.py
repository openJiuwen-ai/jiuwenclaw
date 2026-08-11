# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Session proxy for subagent event forwarding.

Filters and forwards stream events to the parent session for frontend display.
Used by skill_turbo_tool when binding node callbacks to the parent DeepAgent session.
"""

from __future__ import annotations

from typing import Any

from openjiuwen.core.session.agent import Session
from openjiuwen.core.session.stream.base import OutputSchema

from jiuwenswarm.common.utils import logger


class SubagentSessionProxy:
    """Session proxy that forwards execution events to parent session.

    Forward tool calls and thinking process, suppress user-facing messages.
    Fork agent's tool execution is shown alongside main Agent's fork_agent call info.

    Supports nested session_id for trace hierarchy:
    - Main agent: sess_xxx
    - Subagent from main: sess_xxx_subagent_1222fc63
    - Fork from subagent: sess_xxx_subagent_1222fc63_fork_295a9e7
    """

    # Event types to forward (tool execution + permission requests + artifacts + task tracking)
    # Include tool_update for showing tool execution status (in_progress, etc.)
    # Include artifact.generated for intermediate/final artifact preview
    # NOTE: subagent LLM text output (llm_output/content_chunk/llm_reasoning) is NOT
    # forwarded to the parent stream — otherwise stage progress text ("正在检测环境依赖..."
    # /"Stage 1 完成！") leaks into the main chat bubble. Stage progress is surfaced via
    # task.start/task.complete/task.update + tool_result summary instead. llm_usage (token
    # stats) is non-text and kept for accounting.
    FORWARD_TYPES = {
        "llm_usage",
        "tool_calls.delta",
        "tool_call", "tool_result", "tool_update",
        "retry_notification", "chat.ask_user_question",
        "context.compressed",  # Context compression info
        "artifact.generated",  # Artifact preview (subagent Write tool outputs)
        "task.start", "task.complete", "task.update",  # Task execution tracking (including full list updates)
    }
    # Event types to suppress (not forwarded to parent stream).
    # - answer/complete/start: user-facing messages only (subagent's own answer bubble)
    # - llm_output/content_chunk/llm_reasoning/thinking: subagent LLM text/reasoning output
    #   — would pollute the main chat bubble with stage progress text ("正在检测环境依赖..."
    #   /"Stage 1 完成！"); stage progress is delivered via task.* events and tool_result
    #   summary instead.
    SUPPRESS_TYPES = {
        "answer", "complete", "start",
        "llm_output", "content_chunk", "llm_reasoning", "thinking",
    }

    def __init__(
        self,
        parent_session: Session,
        subagent_id: str,
        role_id: str,
        *,
        inject_stream_source_id: bool = True,
    ) -> None:
        self._parent = parent_session
        self._subagent_id = subagent_id
        self._role_id = role_id
        # Online skill-turbo serial execute aligns with batch: route by TaskExecutionRail
        # task.start stack, not stream_source_id. Keep filtering/suppress behavior.
        self._inject_stream_source_id = inject_stream_source_id
        # Construct nested session_id for trace hierarchy
        # Simply append subagent_id to parent_session's session_id
        # Works for both direct and nested scenarios
        self._session_id = f"{parent_session.get_session_id()}_{subagent_id}"

    async def write_stream(self, data: Union[dict, OutputSchema]) -> None:
        """Forward tool execution events, suppress user-facing messages."""
        event_type = None
        output_data = None

        if isinstance(data, OutputSchema):
            event_type = data.type
            output_data = data
        elif isinstance(data, dict):
            event_type = data.get("type") or data.get("event_type", "unknown")
            payload = (
                data.get("payload")
                if "payload" in data
                else {k: v for k, v in data.items() if k not in {"type", "index"}}
            )
            output_data = OutputSchema(
                type=event_type,
                index=data.get("index", 0),
                payload=payload or {},
            )

        # Mark subagent-origin stream chunks before forwarding them into the
        # parent stream. Nested proxies preserve the innermost source id.
        # Skip when inject_stream_source_id=False (skill-turbo serial / batch-style).
        if (
            self._inject_stream_source_id
            and output_data is not None
            and isinstance(output_data.payload, dict)
        ):
            output_data.payload.setdefault("stream_source_id", self._subagent_id)

        # Forward model/tool/progress events that the parent stream already knows how to render.
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

    def get_state(self, key: str | list | dict | None = None) -> Any:
        """Proxy to parent session."""
        return self._parent.get_state(key)

    async def write_custom_stream(self, data: dict) -> None:
        """Forward custom stream (typically not user-facing, pass through)."""
        if isinstance(data, dict) and self._inject_stream_source_id:
            data.setdefault("stream_source_id", self._subagent_id)
        await self._parent.write_custom_stream(data)

    def __getattr__(self, name: str) -> Any:
        """Fallback: proxy any other attributes to parent session."""
        return getattr(self._parent, name)

    def get_parent_session(self) -> Session:
        """Return the underlying parent session.

        Useful when tools need the actual session (not the proxy).
        """
        return self._parent
