"""Build and adapt explicit invocation context at the AgentServer boundary."""

from __future__ import annotations

import copy
import logging
import uuid
from collections.abc import Mapping
from typing import Any

from jiuwenswarm.common.device_rpc.models import DeviceCommandContext
from jiuwenswarm.common.invocation_context.adapters import (
    build_device_command_context_from_invocation,
)
from jiuwenswarm.common.invocation_context.codec import attach_invocation_context
from jiuwenswarm.common.invocation_context.models import (
    INVOCATION_CONTEXT_VERSION,
    InvocationContext,
    XiaoyiInvocationContext,
)
from jiuwenswarm.common.schema.agent import AgentRequest

logger = logging.getLogger(__name__)


def _first_text(*values: Any) -> str | None:
    for value in values:
        if value is None:
            continue
        text = str(value).strip()
        if text:
            return text
    return None


def _copy_mapping(value: Any) -> dict[str, Any] | None:
    if not isinstance(value, Mapping):
        return None
    return copy.deepcopy(dict(value))


def build_invocation_context(request: AgentRequest) -> InvocationContext:
    """Construct one invocation identity from an incoming ``AgentRequest``.

    Xiaoyi field precedence intentionally mirrors the established
    ``build_device_context_from_request`` behavior.  Only explicit routing
    identity and the scheduled-device metadata objects are retained.
    """

    if not isinstance(request, AgentRequest):
        raise TypeError("request must be an AgentRequest")

    request_id = _first_text(request.request_id)
    if request_id is None:
        raise ValueError("request_id is required to build InvocationContext")
    channel_id = _first_text(request.channel_id)
    if channel_id is None:
        raise ValueError("channel_id is required to build InvocationContext")

    metadata = request.metadata if isinstance(request.metadata, Mapping) else {}
    params = request.params if isinstance(request.params, Mapping) else {}

    root_session_id = _first_text(
        metadata.get("xiaoyi_root_session_id"),
        metadata.get("xiaoyi_session_id"),
        request.chat_id,
    )
    params_session_id = _first_text(
        metadata.get("xiaoyi_params_session_id"),
        params.get("xiaoyi_session_id"),
        params.get("session_id"),
    )
    task_id = _first_text(
        metadata.get("xiaoyi_task_id"),
        params.get("task_id"),
        request_id,
    )
    message_id = _first_text(
        metadata.get("xiaoyi_rpc_id"),
        metadata.get("xiaoyi_message_id"),
    )
    device_id = _first_text(
        metadata.get("xiaoyi_device_id"),
        metadata.get("device_id"),
    )
    scheduled_device = _copy_mapping(metadata.get("scheduled_device"))
    cron = _copy_mapping(metadata.get("cron"))
    if cron is None:
        # The cron request layer historically accepted this value in params;
        # retaining that source keeps scheduled-device behavior equivalent.
        cron = _copy_mapping(params.get("cron"))
    app_id = _first_text(metadata.get("app_id"))
    binding_id = _first_text(metadata.get("binding_id"))

    normalized_channel = channel_id.lower()
    has_xiaoyi_metadata = any(
        key.startswith("xiaoyi_")
        for key in metadata
        if isinstance(key, str)
    )
    xiaoyi: XiaoyiInvocationContext | None = None
    if (
        normalized_channel in {"xiaoyi", "__cron__"}
        or has_xiaoyi_metadata
        or scheduled_device is not None
        or cron is not None
    ):
        xiaoyi = XiaoyiInvocationContext(
            root_session_id=root_session_id,
            params_session_id=params_session_id,
            task_id=task_id,
            message_id=message_id,
            device_id=device_id,
            scheduled_device=scheduled_device,
            cron=cron,
        )

    context = InvocationContext(
        version=INVOCATION_CONTEXT_VERSION,
        invocation_id=f"inv_{uuid.uuid4().hex}",
        request_id=request_id,
        session_id=_first_text(request.session_id),
        channel_id=channel_id,
        chat_id=_first_text(request.chat_id),
        xiaoyi=xiaoyi,
        metadata={
            key: copy.deepcopy(value)
            for key, value in (
                ("app_id", app_id),
                ("binding_id", binding_id),
                ("scheduled_device", scheduled_device),
                ("cron", cron),
            )
            if value is not None
        },
    )
    logger.info(
        "[INVOCATION_CTX] BUILT invocation_id=%s request_id=%s session_id=%s channel_id=%s",
        context.invocation_id,
        context.request_id,
        context.session_id,
        context.channel_id,
    )
    return context


def build_device_command_context_from_invocation_context(
    invocation: InvocationContext,
) -> DeviceCommandContext:
    """Compatibility alias kept at the server boundary."""

    return build_device_command_context_from_invocation(invocation)


__all__ = [
    "attach_invocation_context",
    "build_device_command_context_from_invocation",
    "build_device_command_context_from_invocation_context",
    "build_invocation_context",
]
