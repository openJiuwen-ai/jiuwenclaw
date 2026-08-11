"""Wire encoding helpers for the generic Reverse RPC transport."""

from __future__ import annotations

from typing import Any

from jiuwenswarm.common.e2a.constants import (
    E2A_RESPONSE_STATUS_IN_PROGRESS,
    E2A_WIRE_SERVER_PUSH_KEY,
)
from jiuwenswarm.common.reverse_rpc.constants import (
    REVERSE_RPC_CANCEL_KIND,
    REVERSE_RPC_REQUEST_KIND,
)
from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcValidationError
from jiuwenswarm.common.reverse_rpc.models import (
    ReverseRpcCancel,
    ReverseRpcRequest,
)


def _push_wire(
    *,
    response_kind: str,
    rpc_id: str,
    body: dict[str, Any],
    request: ReverseRpcRequest,
) -> dict[str, Any]:
    return {
        "request_id": f"reverse_rpc_push_{rpc_id}",
        "response_kind": response_kind,
        "is_final": False,
        "status": E2A_RESPONSE_STATUS_IN_PROGRESS,
        "body": body,
        "channel_id": request.route.channel_id or request.origin.channel_id or "",
        "session_id": request.origin.session_id,
        "metadata": {E2A_WIRE_SERVER_PUSH_KEY: True},
    }


def build_request_wire(request: ReverseRpcRequest) -> dict[str, Any]:
    return _push_wire(
        response_kind=REVERSE_RPC_REQUEST_KIND,
        rpc_id=request.rpc_id,
        body=request.to_dict(),
        request=request,
    )


def build_cancel_wire(
    cancel: ReverseRpcCancel,
    request: ReverseRpcRequest,
) -> dict[str, Any]:
    return _push_wire(
        response_kind=REVERSE_RPC_CANCEL_KIND,
        rpc_id=cancel.rpc_id,
        body=cancel.to_dict(),
        request=request,
    )


def request_from_wire(wire: Any) -> ReverseRpcRequest:
    if not isinstance(wire, dict):
        raise ReverseRpcValidationError("Reverse RPC wire must be an object")
    if wire.get("response_kind") != REVERSE_RPC_REQUEST_KIND:
        raise ReverseRpcValidationError("wire is not a Reverse RPC request")
    return ReverseRpcRequest.from_dict(wire.get("body"))


def cancel_from_wire(wire: Any) -> ReverseRpcCancel:
    if not isinstance(wire, dict):
        raise ReverseRpcValidationError("Reverse RPC wire must be an object")
    if wire.get("response_kind") != REVERSE_RPC_CANCEL_KIND:
        raise ReverseRpcValidationError("wire is not a Reverse RPC cancel")
    return ReverseRpcCancel.from_dict(wire.get("body"))
