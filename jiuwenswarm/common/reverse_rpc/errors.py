"""Exceptions shared by the capability-neutral Reverse RPC implementation."""

from __future__ import annotations

from typing import Any

from jiuwenswarm.common.reverse_rpc.constants import (
    ERROR_OVERLOADED,
    ERROR_TIMEOUT,
    ERROR_TRANSPORT_DISCONNECTED,
)


class ReverseRpcError(RuntimeError):
    """Base exception for local Reverse RPC failures."""


class ReverseRpcValidationError(ReverseRpcError, ValueError):
    """Raised when a V1 model or wire payload is invalid."""


class ReverseRpcTimeoutError(ReverseRpcError):
    code = ERROR_TIMEOUT


class ReverseRpcTransportDisconnected(ReverseRpcError):
    code = ERROR_TRANSPORT_DISCONNECTED


class ReverseRpcOverloadedError(ReverseRpcError):
    code = ERROR_OVERLOADED


class ReverseRpcRemoteError(ReverseRpcError):
    """A structured error returned by a remote capability."""

    def __init__(
        self,
        code: str,
        message: str,
        *,
        retryable: bool = False,
        details: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(f"{code}: {message}")
        self.code = code
        self.message = message
        self.retryable = retryable
        self.details = details
