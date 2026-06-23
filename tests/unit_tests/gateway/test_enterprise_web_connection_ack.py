# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Any

import pytest

from jiuwenclaw.app_web_handlers import (
    WebHandlersBindParams,
    _register_web_handlers,
    emit_connection_ack,
)
from jiuwenclaw.channel.web_channel import ConnectHook, MethodHandler


@pytest.mark.asyncio
async def test_emit_connection_ack_uses_channel_send_with_route_conn_id() -> None:
    sent_messages: list[object] = []

    class _FakeChannel:
        channel_id = "web"

        async def send(self, msg: object) -> None:
            sent_messages.append(msg)

    class _FakeAgent:
        server_ready = True

    sid = await emit_connection_ack(
        _FakeChannel(),
        _FakeAgent(),
        route_conn_id="browser-conn-9",
    )

    assert sid is not None
    assert len(sent_messages) == 1
    msg = sent_messages[0]
    assert msg.payload["session_id"] == sid
    assert msg.payload["_route_conn_id"] == "browser-conn-9"


@pytest.mark.asyncio
async def test_web_connection_ack_method_registered_for_enterprise() -> None:
    methods: dict[str, object] = {}

    class _FakeChannel:
        channel_id = "web"

        async def send(self, msg: object) -> None:
            pass

        async def send_response(
            self,
            ws: Any,
            req_id: str,
            **kwargs: Any,
        ) -> None:
            pass

        @staticmethod
        def register_method(method: str, handler: MethodHandler) -> None:
            methods[method] = handler

        @staticmethod
        def on_connect(callback: ConnectHook) -> None:
            pass

    channel = _FakeChannel()
    _register_web_handlers(
        WebHandlersBindParams(
            channel=channel,
            agent_client=type("Agent", (), {"server_ready": True})(),
            emit_connection_ack_on_connect=False,
            enable_web_connection_ack_method=True,
        ),
    )

    assert "web.connection_ack" in methods
