"""Typed Xiaoyi GUI payloads carried by the generic Reverse RPC layer."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


XIAOYI_GUI_REVERSE_RPC_METHOD = "xiaoyi.gui.execute"
XIAOYI_GUI_MAX_TIMEOUT_SECONDS = 180.0


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


@dataclass(frozen=True, slots=True)
class GuiReverseRpcPayload:
    query: str
    xiaoyi_session_id: str
    xiaoyi_task_id: str
    xiaoyi_message_id: str
    device_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "query": self.query,
            "xiaoyi_session_id": self.xiaoyi_session_id,
            "xiaoyi_task_id": self.xiaoyi_task_id,
            "xiaoyi_message_id": self.xiaoyi_message_id,
            "device_id": self.device_id,
        }

    @classmethod
    def from_dict(cls, data: Any) -> "GuiReverseRpcPayload":
        if not isinstance(data, dict):
            raise ValueError("GUI Reverse RPC payload must be an object")
        return cls(
            query=_required_text(data.get("query"), "query"),
            xiaoyi_session_id=_required_text(
                data.get("xiaoyi_session_id"),
                "xiaoyi_session_id",
            ),
            xiaoyi_task_id=_required_text(
                data.get("xiaoyi_task_id"),
                "xiaoyi_task_id",
            ),
            xiaoyi_message_id=_required_text(
                data.get("xiaoyi_message_id"),
                "xiaoyi_message_id",
            ),
            device_id=_optional_text(data.get("device_id")),
        )


@dataclass(frozen=True, slots=True)
class GuiReverseRpcResult:
    rpc_id: str
    result: str

    def to_dict(self) -> dict[str, Any]:
        return {"rpc_id": self.rpc_id, "result": self.result}

    @classmethod
    def from_dict(cls, data: Any) -> "GuiReverseRpcResult":
        if not isinstance(data, dict):
            raise ValueError("GUI Reverse RPC result must be an object")
        return cls(
            rpc_id=_required_text(data.get("rpc_id"), "rpc_id"),
            result=_required_text(data.get("result"), "result"),
        )
