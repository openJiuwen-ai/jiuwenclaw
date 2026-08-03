# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentServer → Gateway 下行推送抽象与 WebSocket 默认实现。"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class GatewayPushTransport(Protocol):
    async def send_push(self, msg: dict[str, Any]) -> None:
        """向 Gateway 发送一条 server_push 语义的消息（与 AgentWebSocketServer.send_push 入参一致）。"""
        ...

    async def request(
        self, msg: dict[str, Any], *, timeout_seconds: float = 15.0
    ) -> dict[str, Any]:
        """向 Gateway 发起一次带关联响应的 server-push 请求。"""
        ...


class WebSocketGatewayPushTransport:
    """通过进程内 AgentWebSocketServer 单例推送（分离部署 + WebSocket 默认路径）。"""

    async def send_push(self, msg: dict[str, Any]) -> None:
        from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

        await AgentWebSocketServer.get_instance().send_push(msg)

    async def request(
        self, msg: dict[str, Any], *, timeout_seconds: float = 15.0
    ) -> dict[str, Any]:
        from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

        return await AgentWebSocketServer.get_instance().request_gateway_push(
            msg, timeout_seconds=timeout_seconds
        )
