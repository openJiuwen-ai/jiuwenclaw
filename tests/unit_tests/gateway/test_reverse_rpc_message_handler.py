from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.common.e2a.constants import (
    E2A_RESPONSE_KIND_REVERSE_RPC_CANCEL,
    E2A_RESPONSE_KIND_REVERSE_RPC_REQUEST,
)
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "response_kind",
    [
        E2A_RESPONSE_KIND_REVERSE_RPC_REQUEST,
        E2A_RESPONSE_KIND_REVERSE_RPC_CANCEL,
    ],
)
async def test_reverse_rpc_server_push_uses_core_dispatcher_before_legacy_parser(
    response_kind: str,
) -> None:
    handler = object.__new__(MessageHandler)
    dispatcher = AsyncMock()
    handler._reverse_rpc_dispatcher = dispatcher
    wire = {"response_kind": response_kind, "body": {"rpc_id": "rpc-1"}}

    await MessageHandler._handle_agent_server_push(handler, wire)

    dispatcher.handle.assert_awaited_once_with(wire)
