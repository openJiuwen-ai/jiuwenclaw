"""Adapters from explicit invocation data to existing RPC schemas."""

from __future__ import annotations

import copy

from jiuwenswarm.common.device_rpc.models import DeviceCommandContext

from .models import InvocationContext


def build_device_command_context_from_invocation(
    invocation: InvocationContext,
) -> DeviceCommandContext:
    """Build the unchanged Device RPC context schema from an invocation."""

    if not isinstance(invocation, InvocationContext):
        raise TypeError("invocation must be an InvocationContext")

    xiaoyi = invocation.xiaoyi
    metadata = {"invocation_id": invocation.invocation_id}
    # Keep the allow-list narrow; never put the full request or
    # InvocationContext in a Device wire payload. Routing identity is needed
    # by the typed Reverse RPC client; scheduled values belong to the existing
    # Device/Cron business contract.
    source_metadata = invocation.metadata if isinstance(invocation.metadata, dict) else {}
    for key in ("app_id", "binding_id"):
        value = source_metadata.get(key)
        if value is not None:
            text = str(value).strip()
            if text:
                metadata[key] = text
    scheduled_device = source_metadata.get("scheduled_device")
    if scheduled_device is None and xiaoyi is not None:
        scheduled_device = xiaoyi.scheduled_device
    cron = source_metadata.get("cron")
    if cron is None and xiaoyi is not None:
        cron = xiaoyi.cron
    if isinstance(scheduled_device, dict):
        metadata["scheduled_device"] = copy.deepcopy(scheduled_device)
    if isinstance(cron, dict):
        metadata["cron"] = copy.deepcopy(cron)

    return DeviceCommandContext(
        source_request_id=invocation.request_id,
        channel_id=invocation.channel_id,
        jiuwen_session_id=invocation.session_id,
        xiaoyi_root_session_id=xiaoyi.root_session_id if xiaoyi else None,
        xiaoyi_params_session_id=xiaoyi.params_session_id if xiaoyi else None,
        xiaoyi_task_id=xiaoyi.task_id if xiaoyi else None,
        xiaoyi_rpc_id=xiaoyi.message_id if xiaoyi else None,
        metadata=metadata,
    )


__all__ = ["build_device_command_context_from_invocation"]
