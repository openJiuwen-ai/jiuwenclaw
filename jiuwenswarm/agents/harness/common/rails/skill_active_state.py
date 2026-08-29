# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Track the active skill per session for credential injection."""

from __future__ import annotations

import contextvars
import logging
from dataclasses import dataclass
from typing import Any, Optional

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext
from openjiuwen.harness.rails.base import DeepAgentRail

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    is_interrupt_resume_source,
)

logger = logging.getLogger(__name__)

_DEFAULT_SESSION_ID = "default"
# Shared with StreamEventRail so tool callbacks can recover the real session id
# when ToolCallInputs has no conversation_id (contextvars alone are not enough
# across gather / nested callbacks).
_SESSION_ID_EXTRA_KEY = "__jiuwenswarm_session_id__"
# Written by _build_inputs / before_invoke so after_invoke is not required to
# re-parse chat.send params. When true, active skill survives this invoke.
_PRESERVE_SKILL_ACTIVE_EXTRA_KEY = "__jiuwenswarm_preserve_skill_active__"
_CHAT_SEND_SOURCE_EXTRA_KEY = "__jiuwenswarm_chat_send_source__"

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


def clear_session_skill_state(session_id: str) -> None:
    """Drop in-memory active-skill state for a session (adapter cleanup)."""
    _drop_session_state(session_id)


def get_session_active_skill(session_id: str) -> Optional[str]:
    if not session_id:
        return None
    state = _sessions.get(session_id)
    return state.active_skill if state is not None else None


def adopt_default_active_skill(session_id: str) -> Optional[str]:
    """If *session_id* has no active skill, migrate one left under ``default``.

    ToolCallInputs often lack ``conversation_id``; without a rail preset the
    skill can be recorded under the ``default`` sentinel. Credential injection
    then looks up the real ``officeclaw_…`` id and misses. Migrate once so
    bash/HITL resume can still inject ``skill_envs``.
    """
    sid = _nonempty_str(session_id)
    if not sid or sid == _DEFAULT_SESSION_ID:
        return get_session_active_skill(sid) if sid else None
    current = get_session_active_skill(sid)
    if current:
        return current
    orphan = get_session_active_skill(_DEFAULT_SESSION_ID)
    if not orphan:
        return None
    _get_or_create_state(sid).active_skill = orphan
    _drop_session_state(_DEFAULT_SESSION_ID)
    logger.info(
        "[SkillActiveStateRail] adopted active '%s' from default -> session=%s",
        orphan,
        sid,
    )
    return orphan


def _nonempty_str(value: Any) -> Optional[str]:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def should_preserve_skill_active_from_params(params: Any) -> bool:
    """Keep prior active skill across permission / confirm / ask_user HITL.

    Source alone is enough: a partial resume payload must not clear the skill
    (otherwise hwocr credentials are not injected after security approval).
    Evolution / legacy approval sources are intentionally out of scope.
    """
    if not isinstance(params, dict):
        return False
    return is_interrupt_resume_source(params.get("source"))


def _extract_session_id(ctx: AgentCallbackContext) -> Optional[str]:
    inputs = getattr(ctx, "inputs", None)
    if inputs is not None:
        conv_id = _nonempty_str(getattr(inputs, "conversation_id", None))
        if conv_id:
            return conv_id
        if isinstance(inputs, dict):
            conv_id = _nonempty_str(inputs.get("conversation_id"))
            if conv_id:
                return conv_id

    session = getattr(ctx, "session", None)
    if session is not None:
        getter = getattr(session, "get_session_id", None)
        if callable(getter):
            conv_id = _nonempty_str(getter())
            if conv_id:
                return conv_id
        conv_id = _nonempty_str(getattr(session, "session_id", None))
        if conv_id:
            return conv_id

    extra = getattr(ctx, "extra", None)
    if isinstance(extra, dict):
        conv_id = _nonempty_str(extra.get(_SESSION_ID_EXTRA_KEY))
        if conv_id and conv_id != _DEFAULT_SESSION_ID:
            return conv_id

    return None


def resolve_skill_session_id(
    ctx: AgentCallbackContext,
    preset: Optional[str] = None,
) -> str:
    return (
        _nonempty_str(preset)
        or _extract_session_id(ctx)
        or _nonempty_str(_current_session_var.get())
        or _DEFAULT_SESSION_ID
    )


def _run_context_extra(inputs: Any) -> dict[str, Any]:
    run_context = getattr(inputs, "run_context", None)
    if run_context is None and isinstance(inputs, dict):
        run = inputs.get("run")
        if isinstance(run, dict):
            context = run.get("context")
            if isinstance(context, dict):
                extra = context.get("extra")
                return extra if isinstance(extra, dict) else {}
        return {}
    extra = getattr(run_context, "extra", None) if run_context is not None else None
    return extra if isinstance(extra, dict) else {}


def _chat_send_source_from_ctx(ctx: AgentCallbackContext) -> Optional[str]:
    extra = getattr(ctx, "extra", None)
    if isinstance(extra, dict):
        source = _nonempty_str(
            extra.get(_CHAT_SEND_SOURCE_EXTRA_KEY) or extra.get("chat_send_source")
        )
        if source:
            return source
    run_extra = _run_context_extra(getattr(ctx, "inputs", None))
    return _nonempty_str(
        run_extra.get(_CHAT_SEND_SOURCE_EXTRA_KEY) or run_extra.get("chat_send_source")
    )


def _should_preserve_skill_active(ctx: AgentCallbackContext) -> bool:
    # Permission/confirm/ask_user source wins over an explicit False flag:
    # _build_inputs used to write preserve=False on incomplete payloads and
    # cleared active skill on every security HITL resume.
    if is_interrupt_resume_source(_chat_send_source_from_ctx(ctx)):
        return True

    extra = getattr(ctx, "extra", None)
    if isinstance(extra, dict) and _PRESERVE_SKILL_ACTIVE_EXTRA_KEY in extra:
        return bool(extra.get(_PRESERVE_SKILL_ACTIVE_EXTRA_KEY))

    inputs = getattr(ctx, "inputs", None)
    run_extra = _run_context_extra(inputs)
    if _PRESERVE_SKILL_ACTIVE_EXTRA_KEY in run_extra:
        return bool(run_extra.get(_PRESERVE_SKILL_ACTIVE_EXTRA_KEY))
    return False


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
    """Track which skill is active after skill_tool loads SKILL.md.

    Active skill survives HITL interrupt continuations (permission / confirm /
    ask_user / …). It is cleared when a real new user task starts, on
    ``skill_complete``, or when the session adapter is torn down.
    """

    priority = 25

    def __init__(self, session_id: Optional[str] = None) -> None:
        super().__init__()
        self._preset_session_id = _nonempty_str(session_id)

    def _resolve_session_id(self, ctx: AgentCallbackContext) -> str:
        return resolve_skill_session_id(ctx, self._preset_session_id)

    def _bind_session_id(self, ctx: AgentCallbackContext, session_id: str) -> None:
        _current_session_var.set(session_id)
        extra = getattr(ctx, "extra", None)
        if not isinstance(extra, dict):
            return
        # Prefer a real id already written by StreamEventRail; never overwrite
        # it with the "default" sentinel.
        existing = _nonempty_str(extra.get(_SESSION_ID_EXTRA_KEY))
        if session_id == _DEFAULT_SESSION_ID and existing and existing != _DEFAULT_SESSION_ID:
            return
        if not existing or existing == _DEFAULT_SESSION_ID or session_id != _DEFAULT_SESSION_ID:
            extra[_SESSION_ID_EXTRA_KEY] = session_id

    def _sync_preserve_flag_to_ctx_extra(self, ctx: AgentCallbackContext) -> bool:
        preserve = _should_preserve_skill_active(ctx)
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            extra[_PRESERVE_SKILL_ACTIVE_EXTRA_KEY] = preserve
            run_extra = _run_context_extra(getattr(ctx, "inputs", None))
            source = _nonempty_str(
                run_extra.get(_CHAT_SEND_SOURCE_EXTRA_KEY)
                or run_extra.get("chat_send_source")
            )
            if source:
                extra[_CHAT_SEND_SOURCE_EXTRA_KEY] = source
        return preserve

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        session_id = self._resolve_session_id(ctx)
        preserve = self._sync_preserve_flag_to_ctx_extra(ctx)
        if not preserve:
            prior = get_session_active_skill(session_id)
            if prior:
                logger.info(
                    "[SkillActiveStateRail] clear active '%s' for new user task "
                    "session=%s",
                    prior,
                    session_id,
                )
            _drop_session_state(session_id)
        self._bind_session_id(ctx, session_id)

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        # Do not drop here: the invoke that raises permission/ask_user interrupt
        # is a normal user turn, but the skill must survive until the HITL
        # resume chat.send. Cleanup happens in before_invoke(new task),
        # skill_complete, or session teardown.
        return

    def _prefer_real_session_id(self, ctx: AgentCallbackContext, session_id: str) -> str:
        """Avoid recording active skill under the ``default`` sentinel when possible."""
        if session_id != _DEFAULT_SESSION_ID:
            return session_id
        extra = getattr(ctx, "extra", None)
        if isinstance(extra, dict):
            better = _nonempty_str(extra.get(_SESSION_ID_EXTRA_KEY))
            if better and better != _DEFAULT_SESSION_ID:
                return better
        if self._preset_session_id and self._preset_session_id != _DEFAULT_SESSION_ID:
            return self._preset_session_id
        return session_id

    async def after_tool_call(self, ctx: AgentCallbackContext) -> None:
        inputs = getattr(ctx, "inputs", None)
        if inputs is None:
            return
        tool_msg = getattr(inputs, "tool_msg", None)
        tool_call = getattr(inputs, "tool_call", None)
        if tool_msg is None or tool_call is None:
            return
        tool_name = getattr(inputs, "tool_name", "") or ""
        session_id = self._prefer_real_session_id(ctx, self._resolve_session_id(ctx))
        self._bind_session_id(ctx, session_id)
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
        # Drop a stale default entry so injection never reads the wrong key.
        if session_id != _DEFAULT_SESSION_ID:
            _drop_session_state(_DEFAULT_SESSION_ID)
        logger.info(
            "[SkillActiveStateRail] activated '%s' session=%s",
            skill_name,
            session_id,
        )


__all__ = [
    "SkillActiveStateRail",
    "_CHAT_SEND_SOURCE_EXTRA_KEY",
    "_DEFAULT_SESSION_ID",
    "_PRESERVE_SKILL_ACTIVE_EXTRA_KEY",
    "_SESSION_ID_EXTRA_KEY",
    "_current_session_var",
    "adopt_default_active_skill",
    "clear_session_skill_state",
    "get_session_active_skill",
    "is_interrupt_resume_source",
    "resolve_skill_session_id",
    "should_preserve_skill_active_from_params",
]
