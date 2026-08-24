"""Versioned, business-agnostic Reverse RPC protocol models."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

from jiuwenswarm.common.reverse_rpc.constants import REVERSE_RPC_VERSION
from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcValidationError


def _optional_text(value: Any, name: str) -> str | None:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReverseRpcValidationError(f"{name} must be a string or null")
    value = value.strip()
    return value or None


def _required_text(value: Any, name: str) -> str:
    result = _optional_text(value, name)
    if result is None:
        raise ReverseRpcValidationError(f"{name} is required")
    return result


def _version(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ReverseRpcValidationError("version must be an integer")
    if value != REVERSE_RPC_VERSION:
        raise ReverseRpcValidationError(f"unsupported Reverse RPC version: {value}")
    return value


@dataclass(frozen=True, slots=True)
class ReverseRpcOrigin:
    execution_id: str | None = None
    request_id: str | None = None
    session_id: str | None = None
    channel_id: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "ReverseRpcOrigin":
        if not isinstance(data, dict):
            raise ReverseRpcValidationError("origin must be an object")
        return cls(
            execution_id=_optional_text(
                data.get("execution_id"), "origin.execution_id"
            ),
            request_id=_optional_text(data.get("request_id"), "origin.request_id"),
            session_id=_optional_text(data.get("session_id"), "origin.session_id"),
            channel_id=_optional_text(data.get("channel_id"), "origin.channel_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReverseRpcRoute:
    gateway_id: str | None = None
    channel_id: str | None = None
    app_id: str | None = None
    binding_id: str | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "ReverseRpcRoute":
        if not isinstance(data, dict):
            raise ReverseRpcValidationError("route must be an object")
        return cls(
            gateway_id=_optional_text(data.get("gateway_id"), "route.gateway_id"),
            channel_id=_optional_text(data.get("channel_id"), "route.channel_id"),
            app_id=_optional_text(data.get("app_id"), "route.app_id"),
            binding_id=_optional_text(data.get("binding_id"), "route.binding_id"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @property
    def is_empty(self) -> bool:
        return not any((self.gateway_id, self.channel_id, self.app_id, self.binding_id))


@dataclass(frozen=True, slots=True)
class ReverseRpcRequest:
    version: int
    rpc_id: str
    method: str
    payload: dict[str, Any]
    timeout_ms: int
    origin: ReverseRpcOrigin
    route: ReverseRpcRoute

    @classmethod
    def from_dict(cls, data: Any) -> "ReverseRpcRequest":
        if not isinstance(data, dict):
            raise ReverseRpcValidationError("Reverse RPC request must be an object")
        payload = data.get("payload")
        if not isinstance(payload, dict):
            raise ReverseRpcValidationError("payload must be an object")
        timeout_ms = data.get("timeout_ms")
        if (
            isinstance(timeout_ms, bool)
            or not isinstance(timeout_ms, int)
            or timeout_ms <= 0
        ):
            raise ReverseRpcValidationError("timeout_ms must be a positive integer")
        return cls(
            version=_version(data.get("version")),
            rpc_id=_required_text(data.get("rpc_id"), "rpc_id"),
            method=_required_text(data.get("method"), "method"),
            payload=dict(payload),
            timeout_ms=timeout_ms,
            origin=ReverseRpcOrigin.from_dict(data.get("origin")),
            route=ReverseRpcRoute.from_dict(data.get("route")),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReverseRpcErrorPayload:
    code: str
    message: str
    retryable: bool = False
    details: dict[str, Any] | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "ReverseRpcErrorPayload":
        if not isinstance(data, dict):
            raise ReverseRpcValidationError("error must be an object")
        retryable = data.get("retryable", False)
        if not isinstance(retryable, bool):
            raise ReverseRpcValidationError("error.retryable must be a boolean")
        details = data.get("details")
        if details is not None and not isinstance(details, dict):
            raise ReverseRpcValidationError("error.details must be an object or null")
        return cls(
            code=_required_text(data.get("code"), "error.code"),
            message=_required_text(data.get("message"), "error.message"),
            retryable=retryable,
            details=dict(details) if details is not None else None,
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class ReverseRpcResponse:
    version: int
    rpc_id: str
    ok: bool
    result: Any | None = None
    error: ReverseRpcErrorPayload | None = None

    @classmethod
    def from_dict(cls, data: Any) -> "ReverseRpcResponse":
        if not isinstance(data, dict):
            raise ReverseRpcValidationError("Reverse RPC response must be an object")
        ok = data.get("ok")
        if not isinstance(ok, bool):
            raise ReverseRpcValidationError("ok must be a boolean")
        error_raw = data.get("error")
        error = (
            None if error_raw is None else ReverseRpcErrorPayload.from_dict(error_raw)
        )
        if ok and error is not None:
            raise ReverseRpcValidationError("successful response cannot contain error")
        if not ok and error is None:
            raise ReverseRpcValidationError("failed response requires error")
        return cls(
            version=_version(data.get("version")),
            rpc_id=_required_text(data.get("rpc_id"), "rpc_id"),
            ok=ok,
            result=data.get("result"),
            error=error,
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "version": self.version,
            "rpc_id": self.rpc_id,
            "ok": self.ok,
            "result": self.result,
            "error": self.error.to_dict() if self.error is not None else None,
        }


@dataclass(frozen=True, slots=True)
class ReverseRpcCancel:
    version: int
    rpc_id: str
    reason: str

    @classmethod
    def from_dict(cls, data: Any) -> "ReverseRpcCancel":
        if not isinstance(data, dict):
            raise ReverseRpcValidationError("Reverse RPC cancel must be an object")
        return cls(
            version=_version(data.get("version")),
            rpc_id=_required_text(data.get("rpc_id"), "rpc_id"),
            reason=_required_text(data.get("reason"), "reason"),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)
