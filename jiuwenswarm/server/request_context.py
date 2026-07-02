from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any

from jiuwenswarm.common.device_rpc.models import DeviceCommandContext
from jiuwenswarm.common.schema.agent import AgentRequest


_current_device_context: ContextVar[DeviceCommandContext | None] = ContextVar(
    "current_device_context",
    default=None,
)
_current_agent_request: ContextVar[AgentRequest | None] = ContextVar(
    "current_agent_request",
    default=None,
)


def set_device_context(context: DeviceCommandContext) -> Token:
    return _current_device_context.set(context)


def get_device_context() -> DeviceCommandContext | None:
    return _current_device_context.get()


def reset_device_context(token: Token) -> None:
    _current_device_context.reset(token)


def set_current_agent_request(request: AgentRequest) -> Token:
    return _current_agent_request.set(request)


def get_current_agent_request() -> AgentRequest | None:
    return _current_agent_request.get()


def reset_current_agent_request(token: Token) -> None:
    _current_agent_request.reset(token)


def build_device_context_from_request(request: AgentRequest) -> DeviceCommandContext:
    metadata = dict(request.metadata or {})
    params = request.params if isinstance(request.params, dict) else {}
    return DeviceCommandContext(
        source_request_id=str(request.request_id or ""),
        channel_id=str(request.channel_id or ""),
        jiuwen_session_id=request.session_id,
        xiaoyi_root_session_id=_first_text(
            metadata.get("xiaoyi_root_session_id"),
            metadata.get("xiaoyi_session_id"),
            request.chat_id,
        ),
        xiaoyi_params_session_id=_first_text(
            metadata.get("xiaoyi_params_session_id"),
            params.get("xiaoyi_session_id"),
            params.get("session_id"),
        ),
        xiaoyi_task_id=_first_text(
            metadata.get("xiaoyi_task_id"),
            params.get("task_id"),
            request.request_id,
        ),
        xiaoyi_rpc_id=_first_text(metadata.get("xiaoyi_rpc_id")),
        metadata=metadata,
    )


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None
