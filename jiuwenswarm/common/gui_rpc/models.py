from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any


GUI_RPC_REQUEST_MESSAGE_TYPE = "xiaoyi.gui_rpc.request"
GUI_RPC_RESPONSE_MESSAGE_TYPE = "xiaoyi.gui_rpc.response"
GUI_RPC_CANCEL_MESSAGE_TYPE = "xiaoyi.gui_rpc.cancel"


def _required_text(data: dict[str, Any], name: str) -> str:
    value = str(data.get(name) or "").strip()
    if not value:
        raise ValueError(f"{name} is required")
    return value


def _optional_text(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


@dataclass(frozen=True)
class GuiRpcRequest:
    message_type: str
    rpc_id: str
    query: str
    source_request_id: str
    jiuwen_session_id: str | None
    xiaoyi_session_id: str
    xiaoyi_task_id: str
    xiaoyi_message_id: str
    device_id: str | None
    deadline: float

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GuiRpcRequest":
        if not isinstance(data, dict):
            raise ValueError("GUI RPC request must be an object")
        message_type = _required_text(data, "message_type")
        if message_type != GUI_RPC_REQUEST_MESSAGE_TYPE:
            raise ValueError(f"unsupported GUI RPC request type: {message_type}")
        try:
            deadline = float(data.get("deadline"))
        except (TypeError, ValueError) as exc:
            raise ValueError("deadline must be a timestamp") from exc
        if deadline <= 0:
            raise ValueError("deadline must be positive")
        return cls(
            message_type=message_type,
            rpc_id=_required_text(data, "rpc_id"),
            query=_required_text(data, "query"),
            source_request_id=_required_text(data, "source_request_id"),
            jiuwen_session_id=_optional_text(data.get("jiuwen_session_id")),
            xiaoyi_session_id=_required_text(data, "xiaoyi_session_id"),
            xiaoyi_task_id=_required_text(data, "xiaoyi_task_id"),
            xiaoyi_message_id=_required_text(data, "xiaoyi_message_id"),
            device_id=_optional_text(data.get("device_id")),
            deadline=deadline,
        )


@dataclass(frozen=True)
class GuiRpcResponse:
    message_type: str
    rpc_id: str
    success: bool
    result: str | None = None
    error_code: str | None = None
    error_message: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GuiRpcResponse":
        if not isinstance(data, dict):
            raise ValueError("GUI RPC response must be an object")
        message_type = _required_text(data, "message_type")
        if message_type != GUI_RPC_RESPONSE_MESSAGE_TYPE:
            raise ValueError(f"unsupported GUI RPC response type: {message_type}")
        success = data.get("success")
        if not isinstance(success, bool):
            raise ValueError("success must be a boolean")
        result = _optional_text(data.get("result"))
        error_code = _optional_text(data.get("error_code"))
        error_message = _optional_text(data.get("error_message"))
        if success and result is None:
            raise ValueError("successful GUI RPC response requires result")
        if not success and error_code is None:
            raise ValueError("failed GUI RPC response requires error_code")
        return cls(
            message_type=message_type,
            rpc_id=_required_text(data, "rpc_id"),
            success=success,
            result=result,
            error_code=error_code,
            error_message=error_message,
        )


@dataclass(frozen=True)
class GuiRpcCancel:
    message_type: str
    rpc_id: str
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "GuiRpcCancel":
        if not isinstance(data, dict):
            raise ValueError("GUI RPC cancel must be an object")
        message_type = _required_text(data, "message_type")
        if message_type != GUI_RPC_CANCEL_MESSAGE_TYPE:
            raise ValueError(f"unsupported GUI RPC cancel type: {message_type}")
        return cls(
            message_type=message_type,
            rpc_id=_required_text(data, "rpc_id"),
            reason=_required_text(data, "reason"),
        )
