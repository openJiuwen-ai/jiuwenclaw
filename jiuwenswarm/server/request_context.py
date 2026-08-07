from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Any
from urllib.parse import quote

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
XIAOYI_MODEL_TRACE_HEADERS_METADATA_KEY = "xiaoyi_model_trace_headers"


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


def build_xiaoyi_model_trace_headers(
    request: AgentRequest | None = None,
) -> dict[str, str]:
    """Build per-request model headers for Xiaoyi traffic."""
    request = request or get_current_agent_request()
    if request is None:
        return {}

    metadata = dict(request.metadata or {})
    cron_headers = _build_cron_model_trace_headers(request, metadata)
    if cron_headers:
        return cron_headers

    is_xiaoyi = str(request.channel_id or "").strip().lower() == "xiaoyi" or any(
        str(key).startswith("xiaoyi_")
        and key != XIAOYI_MODEL_TRACE_HEADERS_METADATA_KEY
        for key in metadata
    )
    if not is_xiaoyi:
        return {}

    params = request.params if isinstance(request.params, dict) else {}
    task_id = _first_text(
        metadata.get("xiaoyi_task_id"),
        params.get("task_id"),
    )
    if task_id is None:
        return {}

    task_parts = task_id.split("&")
    return {
        "x-hag-trace-id": task_id,
        "x-session-id": task_parts[0].strip(),
        "x-interaction-id": task_parts[1].strip() if len(task_parts) > 1 else "",
    }


def get_xiaoyi_model_trace_headers(metadata: dict[str, Any] | None) -> dict[str, str]:
    """Read validated Xiaoyi model trace headers from request metadata."""
    if not isinstance(metadata, dict):
        return {}
    raw_headers = metadata.get(XIAOYI_MODEL_TRACE_HEADERS_METADATA_KEY)
    if not isinstance(raw_headers, dict):
        return {}
    return {
        name: value
        for name, value in raw_headers.items()
        if name in {"x-hag-trace-id", "x-session-id", "x-interaction-id"}
        and isinstance(value, str)
    }


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


def _build_cron_model_trace_headers(
    request: AgentRequest,
    metadata: dict[str, Any],
) -> dict[str, str]:
    request_id = _first_text(request.request_id)
    if request_id is None or not request_id.startswith("cron-"):
        return {}

    params = request.params if isinstance(request.params, dict) else {}
    cron_metadata = metadata.get("cron")
    if not isinstance(cron_metadata, dict):
        cron_metadata = params.get("cron")
    if not isinstance(cron_metadata, dict):
        return {}

    job_id = _first_text(cron_metadata.get("job_id"))
    run_id = _first_text(cron_metadata.get("run_id"))
    if job_id is None or run_id is None:
        return {}

    encoded_job_id = quote(job_id, safe="")
    encoded_run_id = quote(run_id, safe="")
    return {
        "x-hag-trace-id": f"cron_{encoded_run_id}",
        "x-session-id": encoded_job_id,
        "x-interaction-id": encoded_run_id,
    }
