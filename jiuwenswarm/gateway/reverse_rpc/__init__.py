"""Gateway-side capability-neutral Reverse RPC dispatcher."""

from jiuwenswarm.gateway.reverse_rpc.dispatcher import ReverseRpcDispatcher
from jiuwenswarm.gateway.reverse_rpc.errors import CapabilityError
from jiuwenswarm.gateway.reverse_rpc.registry import (
    CapabilityRegistry,
    CapabilitySpec,
    ReverseRpcCapabilityContext,
    ReverseRpcCapabilityHandler,
)
from jiuwenswarm.gateway.reverse_rpc.transport import ReverseRpcResponseTransport

__all__ = [
    "CapabilityError",
    "CapabilityRegistry",
    "CapabilitySpec",
    "ReverseRpcCapabilityContext",
    "ReverseRpcCapabilityHandler",
    "ReverseRpcDispatcher",
    "ReverseRpcResponseTransport",
]
