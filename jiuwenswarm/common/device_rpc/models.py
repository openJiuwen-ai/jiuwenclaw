from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class DeviceCommandContext:
    source_request_id: str
    channel_id: str
    jiuwen_session_id: str | None

    xiaoyi_root_session_id: str | None
    xiaoyi_params_session_id: str | None
    xiaoyi_task_id: str | None
    xiaoyi_rpc_id: str | None

    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class DeviceCommandRequest:
    rpc_id: str
    operation_id: str
    intent_name: str
    command: dict[str, Any]
    context: DeviceCommandContext
    timeout_seconds: float


@dataclass(frozen=True)
class DeviceCommandResponse:
    rpc_id: str
    operation_id: str
    ok: bool
    result: dict[str, Any] | None = None
    error_code: str | None = None
    error_message: str | None = None
