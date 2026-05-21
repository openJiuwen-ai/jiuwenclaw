# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""WebSocket 扩展处理器集成测试."""

from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.e2a.wire_codec import encode_agent_response_for_wire
from jiuwenclaw.extensions.registry import ExtensionRegistry
from jiuwenclaw.extensions.types import WsHandlerContext
from jiuwenclaw.schema.agent import AgentResponse, AgentRequest


@pytest.fixture
def mock_callback_framework():
    return MagicMock()


@pytest.fixture
def mock_logger():
    return MagicMock()


@pytest.fixture
def registry(mock_callback_framework, mock_logger):
    """重置并创建 ExtensionRegistry 实例。"""
    ExtensionRegistry.reset_instance()
    return ExtensionRegistry.create_instance(
        callback_framework=mock_callback_framework,
        config={},
        logger=mock_logger,
    )


class TestCustomWsHandlerIntegration:
    """自定义处理器集成测试。"""

    @pytest.mark.asyncio
    async def test_handler_returns_payload_correctly(self, registry):
        """处理器返回 payload 应正确封装为 AgentResponse。"""

        async def my_handler(ctx: WsHandlerContext) -> dict:
            return {"status": "success", "data": {"count": 42}}

        registry.register_ws_handler(method="test.integration.action", handler=my_handler)

        # 模拟 WebSocket 连接和请求
        mock_ws = MagicMock()
        mock_ws.send = AsyncMock()

        # 构造模拟的 AgentRequest
        request = AgentRequest(
            request_id="req-001",
            channel_id="web",
            session_id="sess-001",
            params={"input": "test"},
        )

        # 获取 handler entry
        entry = registry.get_ws_handler("test.integration.action")
        assert entry is not None

        # 模拟调用处理器
        ctx = WsHandlerContext(
            request_id=request.request_id,
            channel_id=request.channel_id,
            session_id=request.session_id,
            params=request.params,
        )

        payload = await entry.handler(ctx)

        # 验证 payload 正确
        assert payload == {"status": "success", "data": {"count": 42}}

        # 模拟封装为响应
        resp = AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload=payload,
        )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)

        # 验证 wire 格式
        assert wire["request_id"] == "req-001"
        assert wire["is_final"] == True
        assert wire["status"] == "succeeded"

    @pytest.mark.asyncio
    async def test_handler_exception_returns_error_response(self, registry):
        """处理器抛出异常应返回错误响应。"""

        async def failing_handler(ctx: WsHandlerContext) -> dict:
            raise ValueError("something went wrong")

        registry.register_ws_handler(method="test.failing.action", handler=failing_handler)

        entry = registry.get_ws_handler("test.failing.action")
        ctx = WsHandlerContext(request_id="req-002", channel_id="web")

        # 模拟调用处理器并捕获异常
        try:
            payload = await entry.handler(ctx)
        except ValueError as e:
            # 模拟框架错误处理
            resp = AgentResponse(
                request_id="req-002",
                channel_id="web",
                ok=False,
                payload={"error": str(e), "error_type": "ValueError"},
            )
            wire = encode_agent_response_for_wire(resp, response_id="req-002")

            # 验证错误响应格式
            assert wire["status"] == "failed"
            # E2A 错误响应格式：body["message"] 包含错误信息，body["details"] 包含 payload
            assert wire["body"]["message"] == "something went wrong"
            assert wire["body"]["details"]["error"] == "something went wrong"
            assert wire["body"]["details"]["error_type"] == "ValueError"

    @pytest.mark.asyncio
    async def test_handler_with_response_metadata(self, registry):
        """处理器可以设置响应 metadata。"""

        async def handler_with_meta(ctx: WsHandlerContext) -> dict:
            ctx.response_metadata["custom_key"] = "custom_value"
            return {"result": "ok"}

        registry.register_ws_handler(method="test.metadata.action", handler=handler_with_meta)

        entry = registry.get_ws_handler("test.metadata.action")
        ctx = WsHandlerContext(request_id="req-003", channel_id="web")

        payload = await entry.handler(ctx)

        # 验证 response_metadata 被正确设置
        assert ctx.response_metadata == {"custom_key": "custom_value"}

        # 验证可以封装到响应中
        resp = AgentResponse(
            request_id="req-003",
            channel_id="web",
            ok=True,
            payload=payload,
            metadata=ctx.response_metadata,
        )
        assert resp.metadata == {"custom_key": "custom_value"}