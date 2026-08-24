"""Process-local assembly for the generic AgentServer Reverse RPC client."""

from __future__ import annotations

from jiuwenswarm.server.reverse_rpc.client import ReverseRpcClient
from jiuwenswarm.server.reverse_rpc.pending_registry import ReverseRpcPendingRegistry
from jiuwenswarm.server.reverse_rpc.transport import ReverseRpcTransport


_client = ReverseRpcClient(registry=ReverseRpcPendingRegistry(max_pending=1024))


def get_reverse_rpc_client() -> ReverseRpcClient:
    return _client


def configure_reverse_rpc_transport(transport: ReverseRpcTransport) -> None:
    _client.set_transport(transport)
