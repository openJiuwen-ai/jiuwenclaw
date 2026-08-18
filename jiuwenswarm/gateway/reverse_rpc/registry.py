"""Explicit capability registration for the generic Gateway dispatcher."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Protocol

from jiuwenswarm.common.reverse_rpc.models import ReverseRpcOrigin, ReverseRpcRoute


@dataclass(frozen=True, slots=True)
class ReverseRpcCapabilityContext:
    rpc_id: str
    origin: ReverseRpcOrigin
    route: ReverseRpcRoute
    timeout_seconds: float
    connection_generation: int


class ReverseRpcCapabilityHandler(Protocol):
    async def handle(
        self,
        ctx: ReverseRpcCapabilityContext,
        payload: dict[str, Any],
    ) -> Any: ...


@dataclass(frozen=True, slots=True)
class CapabilitySpec:
    method: str
    handler: ReverseRpcCapabilityHandler
    supports_cancel: bool = True
    cancel_on_disconnect: bool = True
    max_timeout_seconds: float = 300.0
    allow_empty_route: bool = False

    def __post_init__(self) -> None:
        if not self.method.strip():
            raise ValueError("capability method is required")
        if self.max_timeout_seconds <= 0:
            raise ValueError("max_timeout_seconds must be positive")


class CapabilityRegistry:
    def __init__(self) -> None:
        self._capabilities: dict[str, CapabilitySpec] = {}

    def register(self, spec: CapabilitySpec) -> None:
        method = spec.method.strip()
        if method in self._capabilities:
            raise RuntimeError(f"duplicate Reverse RPC capability method: {method}")
        self._capabilities[method] = spec

    def resolve(self, method: str) -> CapabilitySpec | None:
        return self._capabilities.get(method)

    def methods(self) -> tuple[str, ...]:
        return tuple(self._capabilities)
