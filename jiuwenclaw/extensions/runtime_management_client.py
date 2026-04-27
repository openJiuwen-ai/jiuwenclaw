# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Agent Client.

通过 openjiuwen_runtime.management.orchestrator.Access 与 AgentServer 通信。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
from typing import Any, AsyncIterator

from jiuwenclaw.e2a.agent_compat import e2a_to_agent_request
from jiuwenclaw.e2a.models import E2AEnvelope
from jiuwenclaw.gateway.agent_client import AgentServerClient
from jiuwenclaw.schema.agent import AgentResponse, AgentResponseChunk

logger = logging.getLogger(__name__)


class _RuntimeIRequest:
    """适配 Runtime Management 的 IRequest 实现。"""

    def __init__(self, request: Any, is_stream: bool = False) -> None:
        self._request = request
        self._request_id = request.request_id or uuid.uuid4().hex
        self._is_stream = is_stream

    @property
    def request_id(self) -> str:
        return self._request_id

    @property
    def chat_id(self) -> str | None:
        return getattr(self._request, 'chat_id', None)

    @property
    def user_id(self) -> str | None:
        return getattr(self._request, 'user_id', None)

    @property
    def bot_id(self) -> str | None:
        return getattr(self._request, 'bot_id', None)

    @property
    def session_id(self) -> str | None:
        return self._request.session_id

    @property
    def wire_dict(self) -> dict[str, Any]:
        """提供上行数据字典，Access 会注入 request_id。"""
        return {
            "request_id": self._request_id,
            "channel_id": self._request.channel_id,
            "session_id": self._request.session_id,
            "req_method": self._request.req_method.value if self._request.req_method else None,
            "params": self._request.params,
            "is_stream": self._is_stream,
            "timestamp": self._request.timestamp,
            "metadata": self._request.metadata,
        }


class _RuntimeResponseParser:
    """适配 Runtime Management 的 IResponseParser 实现。"""

    def request_id(self, data: dict[str, Any]) -> str | None:
        return data.get("request_id")

    def is_completed(self, data: dict[str, Any]) -> bool:
        return bool(data.get("completed") or data.get("error_code") is not None)

    def response(self, data: dict[str, Any]) -> Any:
        if "message" in data and "error_code" in data:
            return data["message"]
        return data.get("result", data)


class RuntimeManagementAgentClient(AgentServerClient):
    """Runtime Management HTTP client."""

    def __init__(
        self,
        *,
        service_manager: "IServiceManager",
        concurrency: int = 1,
        invoke_timeout_s: float = 60.0,
    ) -> None:
        """初始化 Runtime Management 客户端。

        Args:
            service_manager: IServiceManager 实例
            concurrency: 会话级并发数
            invoke_timeout_s: 调用超时时间（秒）
        """
        self._service_manager = service_manager
        self._concurrency = max(int(concurrency), 1)
        self._invoke_timeout_s = float(invoke_timeout_s)
        self._access: Any = None
        self._connected = False
        self._server_ready = False
        logger.info(
            "[RuntimeManagementAgentClient] initialized: concurrency=%d, timeout=%.1fs",
            self._concurrency,
            self._invoke_timeout_s,
        )

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        """更新服务端配置（可选实现）。"""
        return None

    @property
    def server_ready(self) -> bool:
        return self._server_ready

    async def connect(self, uri: str) -> None:
        """建立连接并初始化 Access。

        Args:
            uri: 连接 URI（可选，用于覆盖默认配置）
        """
        try:
            from openjiuwen_runtime.management.session.access import Access
            from openjiuwen_runtime.management.session.interfaces import IServiceManager
            from openjiuwen_runtime.management.session.models import AccessConfig, SessionConfig
            from openjiuwen_runtime.management.session.strategies.per_chat_bot import PerChatBotStrategy

            # 创建 Access 实例，传入 IServiceManager
            self._access = Access(self._service_manager)

            # 从环境变量获取 agent_server 镜像和端口配置
            import os
            agent_image = os.getenv("AGENT_SERVER_IMAGE", "agentserver:latest")
            target_port = int(os.getenv("AGENT_SERVER_PORT", "18092"))
            
            logger.info(
                "[RuntimeManagementAgentClient] using agent image=%s, port=%d",
                agent_image,
                target_port,
            )

            # 初始化 Access
            await self._access.init(
                response_parser=_RuntimeResponseParser(),
                strategy=PerChatBotStrategy(),
                config=AccessConfig(
                    user_queue_size=1000,
                    system_queue_size=100,
                    service_concurrency=self._concurrency,
                    min_idle_services=0,
                    max_services=5,
                    message_timeout=int(self._invoke_timeout_s),
                    image=agent_image,
                    target_port=target_port,
                    invoke_path="",
                    ws_use_tls=False,
                    service_ttl=300,
                    max_retries=3,
                    autoscale_interval=0.2,
                ),
                session_config=SessionConfig(
                    concurrency=self._concurrency,
                    ttl=0,
                ),
            )

            self._connected = True
            self._server_ready = True
            logger.info("[RuntimeManagementAgentClient] ready")
        except Exception as exc:
            logger.error("[RuntimeManagementAgentClient] connect failed: %s", exc)
            raise

    async def disconnect(self) -> None:
        """断开连接并清理资源。"""
        try:
            if self._access and hasattr(self._access, "shutdown"):
                await self._access.shutdown()
                logger.info("[RuntimeManagementAgentClient] Access shutdown")
            if self._service_manager and hasattr(self._service_manager, "stop"):
                await self._service_manager.stop()
                logger.info("[RuntimeManagementAgentClient] ServiceManager stopped")
        except Exception as exc:
            logger.warning("[RuntimeManagementAgentClient] disconnect error: %s", exc)
        finally:
            self._connected = False
            self._server_ready = False
            logger.info("[RuntimeManagementAgentClient] disconnected")

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("client not connected")

    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        """发送非流式请求。

        Args:
            envelope: E2A 信封

        Returns:
            Agent 响应
        """
        self._ensure_connected()
        request = e2a_to_agent_request(envelope)

        # 构造 IRequest 实现（非流式）
        runtime_request = _RuntimeIRequest(request, is_stream=False)

        try:
            # 调用 Access.send_message，它返回 AsyncIterator
            # Access 内部流程：
            # 1. 接收 IRequest
            # 2. strategy.handle_session() → ISessionRequest
            # 3. 创建 SessionRequestWrapper
            # 4. 投递到 ServiceManager
            # 5. 从 response_queue 读取并通过 IResponseParser 解析后 yield
            response_chunks = []
            async for chunk in self._access.send_message(runtime_request):
                response_chunks.append(chunk)

            if not response_chunks:
                return AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "no response received"},
                    metadata={},
                )

            # 合并所有响应片段（非流式模式下应该只有一个完整响应）
            final_response = response_chunks[-1] if response_chunks else {}

            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"content": final_response},
                metadata={},
            )

        except Exception as exc:
            logger.exception("[RuntimeManagementAgentClient] send_request failed: %s", exc)
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata={},
            )

    async def send_request_stream(self, envelope: E2AEnvelope) -> AsyncIterator[AgentResponseChunk]:
        """发送流式请求。

        Args:
            envelope: E2A 信封

        Yields:
            Agent 响应片段
        """
        self._ensure_connected()
        request = e2a_to_agent_request(envelope)

        # 构造 IRequest 实现（流式）
        runtime_request = _RuntimeIRequest(request, is_stream=True)

        try:
            # 调用 Access.send_message，它返回 AsyncIterator
            # Access 内部通过 IResponseParser 解析响应并逐个 yield
            async for chunk in self._access.send_message(runtime_request):
                # Access 已经通过 IResponseParser.response() 解析了响应
                yield AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload=chunk,
                    is_complete=False,
                )

            # 发送完成标记
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload=None,
                is_complete=True,
            )

        except Exception as exc:
            logger.exception("[RuntimeManagementAgentClient] send_request_stream failed: %s", exc)
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"error": str(exc)},
                is_complete=True,
            )
