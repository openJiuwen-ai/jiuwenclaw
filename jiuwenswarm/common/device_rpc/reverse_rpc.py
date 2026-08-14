"""Typed Xiaoyi Device payloads carried by the generic Reverse RPC layer."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from jiuwenswarm.common.device_rpc.models import DeviceCommandContext


XIAOYI_DEVICE_REVERSE_RPC_METHOD = "xiaoyi.device.execute"
XIAOYI_DEVICE_MAX_TIMEOUT_SECONDS = 60.0


def _required_text(value: Any, name: str) -> str:
    text = str(value or "").strip()
    if not text:
        raise ValueError(f"{name} is required")
    return text


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _context_from_dict(data: Any) -> DeviceCommandContext:
    if not isinstance(data, dict):
        raise ValueError("context must be an object")
    metadata = data.get("metadata")
    if metadata is None:
        metadata = {}
    if not isinstance(metadata, dict):
        raise ValueError("context.metadata must be an object")
    return DeviceCommandContext(
        source_request_id=_required_text(
            data.get("source_request_id"),
            "context.source_request_id",
        ),
        channel_id=_required_text(data.get("channel_id"), "context.channel_id"),
        jiuwen_session_id=_optional_text(data.get("jiuwen_session_id")),
        xiaoyi_root_session_id=_optional_text(data.get("xiaoyi_root_session_id")),
        xiaoyi_params_session_id=_optional_text(data.get("xiaoyi_params_session_id")),
        xiaoyi_task_id=_optional_text(data.get("xiaoyi_task_id")),
        xiaoyi_rpc_id=_optional_text(data.get("xiaoyi_rpc_id")),
        metadata=dict(metadata),
    )


@dataclass(frozen=True, slots=True)
class DeviceReverseRpcPayload:
    operation_id: str
    intent_name: str
    command: dict[str, Any]
    context: DeviceCommandContext

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation_id": self.operation_id,
            "intent_name": self.intent_name,
            "command": dict(self.command),
            "context": asdict(self.context),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "DeviceReverseRpcPayload":
        if not isinstance(data, dict):
            raise ValueError("Device Reverse RPC payload must be an object")
        command = data.get("command")
        if not isinstance(command, dict):
            raise ValueError("command must be an object")
        return cls(
            operation_id=_required_text(data.get("operation_id"), "operation_id"),
            intent_name=_required_text(data.get("intent_name"), "intent_name"),
            command=dict(command),
            context=_context_from_dict(data.get("context")),
        )


@dataclass(frozen=True, slots=True)
class DeviceReverseRpcResult:
    rpc_id: str
    operation_id: str
    result: dict[str, Any]

    def to_dict(self) -> dict[str, Any]:
        return {
            "rpc_id": self.rpc_id,
            "operation_id": self.operation_id,
            "result": dict(self.result),
        }

    @classmethod
    def from_dict(cls, data: Any) -> "DeviceReverseRpcResult":
        if not isinstance(data, dict):
            raise ValueError("Device Reverse RPC result must be an object")
        result = data.get("result")
        if not isinstance(result, dict):
            raise ValueError("result must be an object")
        return cls(
            rpc_id=_required_text(data.get("rpc_id"), "rpc_id"),
            operation_id=_required_text(data.get("operation_id"), "operation_id"),
            result=dict(result),
        )
