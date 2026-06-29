"""Session-scoped WeChat QR login state."""

from __future__ import annotations

import asyncio
import copy
import time
import uuid
from dataclasses import dataclass, field
from typing import Any


def _empty_state() -> dict[str, Any]:
    return {
        "phase": "idle",
        "message": "",
        "qr": None,
        "credentials": None,
        "credentials_source": None,
        "error": None,
        "updated_at": 0.0,
    }


@dataclass
class _LoginOperation:
    operation_id: str
    requester_channel_id: str = ""
    requester_session_id: str = ""
    state: dict[str, Any] = field(default_factory=_empty_state)


class WechatLoginService:
    """Own QR login state without coupling it to any delivery channel."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()
        self._operations: dict[str, _LoginOperation] = {}
        self._current_operation_id = ""

    async def begin(
        self,
        *,
        operation_id: str | None = None,
        requester_channel_id: str = "",
        requester_session_id: str = "",
    ) -> str:
        operation_id = str(operation_id or uuid.uuid4().hex)
        async with self._lock:
            self._operations[operation_id] = _LoginOperation(
                operation_id=operation_id,
                requester_channel_id=str(requester_channel_id or ""),
                requester_session_id=str(requester_session_id or ""),
            )
            self._current_operation_id = operation_id
            self._trim_locked()
        return operation_id

    async def update(self, **values: Any) -> None:
        async with self._lock:
            operation = self._current_locked()
            for key, value in values.items():
                if key == "credentials_source":
                    continue
                if key in operation.state:
                    operation.state[key] = value
            if "credentials" in values:
                if values["credentials"] is None:
                    operation.state["credentials_source"] = None
                elif values.get("credentials_source") in {"scan", "local_file"}:
                    operation.state["credentials_source"] = values["credentials_source"]
                else:
                    operation.state["credentials_source"] = "scan"
            operation.state["updated_at"] = time.time()

    async def snapshot(self, operation_id: str | None = None) -> dict[str, Any]:
        async with self._lock:
            operation = self._operations.get(str(operation_id or self._current_operation_id))
            if operation is None:
                return _empty_state()
            state = copy.deepcopy(operation.state)
            state["operation_id"] = operation.operation_id
            state["requester_channel_id"] = operation.requester_channel_id
            state["requester_session_id"] = operation.requester_session_id
            return state

    async def find_operation_id(self, channel_id: str, session_id: str) -> str | None:
        key = (str(channel_id or "").strip(), str(session_id or "").strip())
        if not all(key):
            return None
        async with self._lock:
            for operation in reversed(list(self._operations.values())):
                if (
                    operation.requester_channel_id,
                    operation.requester_session_id,
                ) == key:
                    return operation.operation_id
        return None

    async def reset(self, operation_id: str | None = None) -> None:
        async with self._lock:
            operation = self._operations.get(str(operation_id or self._current_operation_id))
            if operation is None:
                operation = self._current_locked()
            operation.state = _empty_state()
            operation.state["updated_at"] = time.time()

    def _current_locked(self) -> _LoginOperation:
        operation = self._operations.get(self._current_operation_id)
        if operation is None:
            operation = _LoginOperation(operation_id="default")
            self._operations[operation.operation_id] = operation
            self._current_operation_id = operation.operation_id
        return operation

    def _trim_locked(self) -> None:
        if len(self._operations) <= 64:
            return
        for operation_id in list(self._operations)[:-48]:
            if operation_id != self._current_operation_id:
                self._operations.pop(operation_id, None)


wechat_login_service = WechatLoginService()
