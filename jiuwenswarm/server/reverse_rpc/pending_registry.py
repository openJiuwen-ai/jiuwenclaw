"""Pending request correlation for AgentServer Reverse RPC calls."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass

from jiuwenswarm.common.reverse_rpc.errors import ReverseRpcOverloadedError
from jiuwenswarm.common.reverse_rpc.models import ReverseRpcRequest, ReverseRpcResponse


@dataclass(slots=True)
class PendingReverseRpc:
    request: ReverseRpcRequest
    future: asyncio.Future[ReverseRpcResponse]
    created_at: float


class ReverseRpcPendingRegistry:
    def __init__(self, *, max_pending: int = 1024) -> None:
        if max_pending <= 0:
            raise ValueError("max_pending must be positive")
        self._max_pending = max_pending
        self._pending: dict[str, PendingReverseRpc] = {}

    @property
    def max_pending(self) -> int:
        return self._max_pending

    def register(self, pending: PendingReverseRpc) -> None:
        rpc_id = pending.request.rpc_id
        if rpc_id in self._pending:
            raise RuntimeError(f"duplicate Reverse RPC rpc_id: {rpc_id}")
        if len(self._pending) >= self._max_pending:
            raise ReverseRpcOverloadedError(
                f"Reverse RPC pending limit reached: {self._max_pending}"
            )
        self._pending[rpc_id] = pending

    def complete(self, response: ReverseRpcResponse) -> bool:
        pending = self._pending.pop(response.rpc_id, None)
        if pending is None or pending.future.done():
            return False
        pending.future.set_result(response)
        return True

    def fail(self, rpc_id: str, exc: BaseException) -> bool:
        pending = self._pending.pop(rpc_id, None)
        if pending is None or pending.future.done():
            return False
        pending.future.set_exception(exc)
        return True

    def remove(self, rpc_id: str) -> PendingReverseRpc | None:
        return self._pending.pop(rpc_id, None)

    def fail_all(self, exc: BaseException) -> None:
        pending = list(self._pending.values())
        self._pending.clear()
        for item in pending:
            if not item.future.done():
                item.future.set_exception(exc)

    def contains(self, rpc_id: str) -> bool:
        return rpc_id in self._pending

    def pending_count(self) -> int:
        return len(self._pending)
