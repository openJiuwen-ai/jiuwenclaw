"""Xiaoyi-private invocation, Device RPC, and model-trace adapters."""

from __future__ import annotations

import copy
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

from jiuwenswarm.common.device_rpc.models import DeviceCommandContext
from jiuwenswarm.common.invocation_context import TRACE_HEADER_EXPORTER_METADATA_KEY
from jiuwenswarm.common.invocation_context.models import (
    TRACE_CONTEXT_VERSION,
    InvocationContext,
    TraceContext,
)
from jiuwenswarm.common.invocation_context.runtime import get_current_invocation_context
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.invocation_context.model_trace import register_trace_header_exporter


XIAOYI_INVOCATION_EXTENSION_KEY = "jiuwenswarm.xiaoyi_invocation"


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _copy_mapping(value: Any) -> dict[str, Any] | None:
    return copy.deepcopy(dict(value)) if isinstance(value, dict) else None


@dataclass(frozen=True, slots=True)
class XiaoyiInvocationExtension:
    root_session_id: str | None = None
    params_session_id: str | None = None
    task_id: str | None = None
    message_id: str | None = None
    device_id: str | None = None
    scheduled_device: dict[str, Any] | None = None
    cron: dict[str, Any] | None = None
    app_id: str | None = None
    binding_id: str | None = None


def build_xiaoyi_invocation_extension(request: AgentRequest) -> dict[str, Any]:
    """Extract Xiaoyi routing data into an opaque InvocationContext extension."""
    metadata = request.metadata if isinstance(request.metadata, dict) else {}
    params = request.params if isinstance(request.params, dict) else {}
    cron = _copy_mapping(metadata.get("cron")) or _copy_mapping(params.get("cron"))
    extension = XiaoyiInvocationExtension(
        root_session_id=_first_text(
            metadata.get("xiaoyi_root_session_id"),
            metadata.get("xiaoyi_session_id"),
            request.chat_id,
        ),
        params_session_id=_first_text(
            metadata.get("xiaoyi_params_session_id"),
            params.get("xiaoyi_session_id"),
            params.get("session_id"),
        ),
        task_id=_first_text(metadata.get("xiaoyi_task_id"), params.get("task_id"), request.request_id),
        message_id=_first_text(metadata.get("xiaoyi_rpc_id"), metadata.get("xiaoyi_message_id")),
        device_id=_first_text(metadata.get("xiaoyi_device_id"), metadata.get("device_id")),
        scheduled_device=_copy_mapping(metadata.get("scheduled_device")),
        cron=cron,
        app_id=_first_text(metadata.get("app_id")),
        binding_id=_first_text(metadata.get("binding_id")),
    )
    has_xiaoyi_metadata = any(
        isinstance(key, str) and key.startswith("xiaoyi_") for key in metadata
    )
    if not (
        str(request.channel_id or "").strip().lower() in {"xiaoyi", "__cron__"}
        or has_xiaoyi_metadata
        or extension.scheduled_device is not None
        or extension.cron is not None
    ):
        return {}
    return {
        XIAOYI_INVOCATION_EXTENSION_KEY: {
            "root_session_id": extension.root_session_id,
            "params_session_id": extension.params_session_id,
            "task_id": extension.task_id,
            "message_id": extension.message_id,
            "device_id": extension.device_id,
            "scheduled_device": copy.deepcopy(extension.scheduled_device),
            "cron": copy.deepcopy(extension.cron),
            "app_id": extension.app_id,
            "binding_id": extension.binding_id,
        },
        TRACE_HEADER_EXPORTER_METADATA_KEY: "xiaoyi",
    }


def _xiaoyi_extension_from_metadata(metadata: dict[str, Any]) -> XiaoyiInvocationExtension | None:
    raw = metadata.get(XIAOYI_INVOCATION_EXTENSION_KEY)
    if not isinstance(raw, dict):
        return None
    return XiaoyiInvocationExtension(
        root_session_id=_first_text(raw.get("root_session_id")),
        params_session_id=_first_text(raw.get("params_session_id")),
        task_id=_first_text(raw.get("task_id")),
        message_id=_first_text(raw.get("message_id")),
        device_id=_first_text(raw.get("device_id")),
        scheduled_device=_copy_mapping(raw.get("scheduled_device")),
        cron=_copy_mapping(raw.get("cron")),
        app_id=_first_text(raw.get("app_id")),
        binding_id=_first_text(raw.get("binding_id")),
    )


def get_xiaoyi_invocation_extension(
    invocation: InvocationContext,
) -> XiaoyiInvocationExtension | None:
    """Read a validated Xiaoyi extension without exposing it to common code."""
    metadata = invocation.metadata if isinstance(invocation.metadata, dict) else {}
    return _xiaoyi_extension_from_metadata(metadata)


def get_xiaoyi_request_extension(request: AgentRequest) -> XiaoyiInvocationExtension | None:
    """Parse one inbound Xiaoyi request without leaking its schema to callers."""
    return _xiaoyi_extension_from_metadata(build_xiaoyi_invocation_extension(request))


def build_xiaoyi_trace_context(request: AgentRequest) -> TraceContext | None:
    """Convert Xiaoyi task/cron identity to the public trace contract."""
    extension = get_xiaoyi_request_extension(request)
    if extension is None:
        return None
    cron = extension.cron or {}
    job_id = _first_text(cron.get("job_id"))
    run_id = _first_text(cron.get("run_id"))
    if str(request.request_id or "").startswith("cron-") and job_id and run_id:
        encoded_job_id = quote(job_id, safe="")
        encoded_run_id = quote(run_id, safe="")
        return TraceContext(
            version=TRACE_CONTEXT_VERSION,
            trace_id=f"cron_{encoded_run_id}",
            conversation_id=encoded_job_id,
            interaction_id=encoded_run_id,
        )
    task_id = extension.task_id
    if not task_id:
        return None
    parts = task_id.split("&")
    return TraceContext(
        version=TRACE_CONTEXT_VERSION,
        trace_id=task_id,
        conversation_id=parts[0].strip() or None,
        interaction_id=parts[1].strip() if len(parts) > 1 and parts[1].strip() else None,
    )


def build_xiaoyi_device_command_context(
    invocation: InvocationContext,
) -> DeviceCommandContext:
    """Adapt the private Xiaoyi extension to the unchanged Device RPC schema."""
    extension = get_xiaoyi_invocation_extension(invocation)
    metadata: dict[str, Any] = {"invocation_id": invocation.invocation_id}
    if extension is not None:
        for key, value in (("app_id", extension.app_id), ("binding_id", extension.binding_id)):
            if value:
                metadata[key] = value
        if extension.scheduled_device is not None:
            metadata["scheduled_device"] = copy.deepcopy(extension.scheduled_device)
        if extension.cron is not None:
            metadata["cron"] = copy.deepcopy(extension.cron)
    return DeviceCommandContext(
        source_request_id=invocation.request_id,
        channel_id=invocation.channel_id,
        jiuwen_session_id=invocation.session_id,
        xiaoyi_root_session_id=extension.root_session_id if extension else None,
        xiaoyi_params_session_id=extension.params_session_id if extension else None,
        xiaoyi_task_id=extension.task_id if extension else None,
        xiaoyi_rpc_id=extension.message_id if extension else None,
        metadata=metadata,
    )


class XiaoyiTraceHeaderExporter:
    """Export the Xiaoyi model header allow-list from a public TraceContext."""

    def supports(self, invocation: InvocationContext) -> bool:
        return get_xiaoyi_invocation_extension(invocation) is not None

    def export(self, trace: TraceContext) -> dict[str, str]:
        headers = {"x-hag-trace-id": trace.trace_id}
        if trace.conversation_id:
            headers["x-session-id"] = trace.conversation_id
        if trace.interaction_id:
            headers["x-interaction-id"] = trace.interaction_id
        return headers


_XIAOYI_TRACE_HEADER_EXPORTER = XiaoyiTraceHeaderExporter()
register_trace_header_exporter("xiaoyi", _XIAOYI_TRACE_HEADER_EXPORTER)


def get_xiaoyi_trace_header_exporters() -> tuple[XiaoyiTraceHeaderExporter, ...]:
    return (_XIAOYI_TRACE_HEADER_EXPORTER,)


def export_xiaoyi_trace_headers(trace: TraceContext | None) -> dict[str, str]:
    return _XIAOYI_TRACE_HEADER_EXPORTER.export(trace) if trace is not None else {}


def export_current_xiaoyi_trace_headers() -> dict[str, str]:
    invocation = get_current_invocation_context()
    if invocation is None or invocation.trace is None:
        return {}
    return (
        _XIAOYI_TRACE_HEADER_EXPORTER.export(invocation.trace)
        if _XIAOYI_TRACE_HEADER_EXPORTER.supports(invocation)
        else {}
    )


__all__ = [
    "XIAOYI_INVOCATION_EXTENSION_KEY",
    "XiaoyiInvocationExtension",
    "XiaoyiTraceHeaderExporter",
    "build_xiaoyi_device_command_context",
    "build_xiaoyi_invocation_extension",
    "build_xiaoyi_trace_context",
    "export_current_xiaoyi_trace_headers",
    "export_xiaoyi_trace_headers",
    "get_xiaoyi_invocation_extension",
    "get_xiaoyi_request_extension",
    "get_xiaoyi_trace_header_exporters",
]
