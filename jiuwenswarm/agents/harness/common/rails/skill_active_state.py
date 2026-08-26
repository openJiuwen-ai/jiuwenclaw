# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Track the active skill per session for credential injection."""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Any, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_ID = "default"
_current_session_var: contextvars.ContextVar[Optional[str]] = contextvars.ContextVar(
    "skill_active_session_id",
    default=None,
)


@dataclass
class _SkillSessionState:
    active_skill: Optional[str] = None

    def reset(self) -> None:
        self.active_skill = None


_sessions: dict[str, _SkillSessionState] = {}


def _get_or_create_state(session_id: str) -> _SkillSessionState:
    state = _sessions.get(session_id)
    if state is None:
        state = _SkillSessionState()
        _sessions[session_id] = state
    return state


def _drop_session_state(session_id: str) -> None:
    if session_id:
        _sessions.pop(session_id, None)


def get_session_active_skill(session_id: str) -> Optional[str]:
    if not session_id:
        return None
    state = _sessions.get(session_id)
    return state.active_skill if state is not None else None


def _extract_session_id(ctx: AgentCallbackContext) -> Optional[str]:
    inputs = getattr(ctx, "inputs", None)
    if inputs is None:
        return None
    conv_id = getattr(inputs, "conversation_id", None)
    return str(conv_id) if conv_id else None


def resolve_skill_session_id(
    ctx: AgentCallbackContext,
    preset: Optional[str] = None,
) -> str:
    return (
        preset
        or _extract_session_id(ctx)
        or _current_session_var.get()
        or _DEFAULT_SESSION_ID
    )


def _str_content(msg: Any) -> str:
    content = getattr(msg, "content", "")
    return content if isinstance(content, str) else str(content)


def _get_arg(tool_call: Any, name: str, default: str = "") -> str:
    arguments = getattr(tool_call, "arguments", None)
    if isinstance(arguments, dict):
        value = arguments.get(name, default)
        return value if isinstance(value, str) else str(value or default)
    if isinstance(arguments, str):
        try:
            import json

            parsed = json.loads(arguments)
        except (TypeError, ValueError):
            return default
        if isinstance(parsed, dict):
            value = parsed.get(name, default)
            return value if isinstance(value, str) else str(value or default)
    return default


class SkillActiveStateRail(DeepAgentRail):
    """Track which skill is active after skill_tool loads SKILL.md."""

    priority = 25

    def __init__(self, session_id: Optional[str] = None) -> None:
        super().__init__()
        self._preset_session_id = session_id

    def _resolve_session_id(self, ctx: AgentCallbackContext) -> str:
        return resolve_skill_session_id(ctx, self._preset_session_id)

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        _current_session_var.set(self._resolve_session_id(ctx))

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        _drop_session_state(self._resolve_session_id(ctx))

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return
        tool_msg = getattr(inputs, "tool_msg", None)
        tool_call = getattr(inputs, "tool_call", None)
        if tool_msg is None or tool_call is None:
            return
        tool_name = getattr(inputs, "tool_name", "") or ""
        session_id = self._resolve_session_id(ctx)
        state = _get_or_create_state(session_id)

        if tool_name == "skill_complete":
            skill_name = (_get_arg(tool_call, "skill_name", "") or "").strip()
            if skill_name and state.active_skill == skill_name:
                logger.info(
                    "[SkillActiveStateRail] skill_complete(%s) session=%s -> idle",
                    skill_name,
                    session_id,
                )
                state.reset()
            return

        if tool_name != "skill_tool":
            return

        meta = getattr(tool_msg, "metadata", None) or {}
        if meta.get("is_directory_listing"):
            return
        skill_name = (meta.get("skill_name") or _get_arg(tool_call, "skill_name", "") or "").strip()
        if not skill_name:
            return
        state.active_skill = skill_name
        logger.info(
            "[SkillActiveStateRail] activated '%s' session=%s",
            skill_name,
            session_id,
        )


__all__ = [
    "SkillActiveStateRail",
    "_DEFAULT_SESSION_ID",
    "_current_session_var",
    "get_session_active_skill",
    "resolve_skill_session_id",
]
