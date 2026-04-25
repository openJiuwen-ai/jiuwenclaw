# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Agent Client.

通过 openjiuwen_runtime.management.orchestrator.Access 与 AgentServer 通信。
"""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator

from jiuwenclaw.e2a.agent_compat import e2a_to_agent_request
from jiuwenclaw.e2a.models import E2AEnvelope
from jiuwenclaw.gateway.agent_client import AgentServerClient
from jiuwenclaw.schema.agent import AgentResponse, AgentResponseChunk

logger = logging.getLogger(__name__)


class RuntimeManagementAgentClient(AgentServerClient):
    """Runtime Management HTTP client."""

    def __init__(
        self,
        *,
        access_instance: Any,
        concurrency: int = 1,
        invoke_timeout_s: float = 60.0,
    ) -> None:
        """初始化 Runtime Management 客户端。

        Args:
            access_instance: openjiuwen_runtime.management.orchestrator.Access 实例
            concurrency: 并发数
            invoke_timeout_s: 调用超时时间（秒）
        """
        self._access = access_instance
        self._concurrency = max(int(concurrency), 1)
        self._invoke_timeout_s = float(invoke_timeout_s)
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
            # 通过健康检查判断 Access 是否已初始化
            health_status = await self._access.health_check()
            if health_status.get("Access") != "running":
                await self._access.init()
                logger.info("[RuntimeManagementAgentClient] Access initialized")

            self._connected = True
            self._server_ready = True
            logger.info("[RuntimeManagementAgentClient] ready")
        except Exception as exc:
            logger.error("[RuntimeManagementAgentClient] connect failed: %s", exc)
            raise

    async def disconnect(self) -> None:
        """断开连接并清理资源。"""
        try:
            if self._access and hasattr(self._access, "stop"):
                await self._access.stop()
                logger.info("[RuntimeManagementAgentClient] Access stopped")
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

        # 构造消息负载
        payload = {
            "request_id": request.request_id,
            "channel_id": request.channel_id,
            "session_id": request.session_id,
            "req_method": request.req_method.value if request.req_method else None,
            "params": request.params,
            "is_stream": False,
            "timestamp": request.timestamp,
            "metadata": request.metadata,
        }

        try:
            # 导入 Runtime 消息接口
            from openjiuwen_runtime.management.orchestrator.models import Message, MessagePriority

            # 创建消息对象
            message = Message(
                session_id=request.session_id or "",
                concurrency=self._concurrency,
                ttl=int(self._invoke_timeout_s),
                priority=MessagePriority.NORMAL,
                payload=payload,
                response_queue=None,
            )

            # 发送消息
            result = await self._access.send_message(message)

            if not result.get("success"):
                return AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": result.get("message", "send failed")},
                    metadata={},
                )

            # 等待响应（非流式模式下，Access 应直接返回完整响应）
            response_queue = result.get("response_queue")
            if response_queue:
                try:
                    response_msg = await asyncio.wait_for(
                        response_queue.get(),
                        timeout=self._invoke_timeout_s,
                    )
                    response_payload = response_msg.payload if hasattr(response_msg, "payload") else {}
                except asyncio.TimeoutError:
                    return AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=False,
                        payload={"error": "response timeout"},
                        metadata={},
                    )
            else:
                response_payload = {}

            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"content": response_payload},
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

        # 构造消息负载
        payload = {
            "request_id": request.request_id,
            "channel_id": request.channel_id,
            "session_id": request.session_id,
            "req_method": request.req_method.value if request.req_method else None,
            "params": request.params,
            "is_stream": True,
            "timestamp": request.timestamp,
            "metadata": request.metadata,
        }

        try:
            # 导入 Runtime 消息接口
            from openjiuwen_runtime.management.orchestrator.models import Message, MessagePriority

            # 创建消息对象
            message = Message(
                session_id=request.session_id or "",
                concurrency=self._concurrency,
                ttl=int(self._invoke_timeout_s),
                priority=MessagePriority.NORMAL,
                payload=payload,
                response_queue=None,
            )

            # 发送消息
            result = await self._access.send_message(message)

            if not result.get("success"):
                yield AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={"error": result.get("message", "send failed")},
                    is_complete=True,
                )
                return

            # 接收流式响应
            session_id = request.session_id or ""
            response_queue = result.get("response_queue")

            if not response_queue:
                yield AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={"error": "no response queue"},
                    is_complete=True,
                )
                return

            while True:
                try:
                    response_msg = await asyncio.wait_for(
                        response_queue.get(),
                        timeout=self._invoke_timeout_s,
                    )

                    # 解析响应
                    msg_payload = response_msg.payload if hasattr(response_msg, "payload") else {}
                    is_complete = getattr(response_msg, "is_complete", False)

                    # 检查是否是结束标记
                    if isinstance(msg_payload, dict) and msg_payload.get("task_type") == "response_end":
                        yield AgentResponseChunk(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            payload=None,
                            is_complete=True,
                        )
                        break

                    yield AgentResponseChunk(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        payload=msg_payload,
                        is_complete=is_complete,
                    )

                    if is_complete:
                        break

                except asyncio.TimeoutError:
                    logger.warning(
                        "[RuntimeManagementAgentClient] stream timeout: session_id=%s",
                        session_id,
                    )
                    yield AgentResponseChunk(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        payload={"error": "stream timeout"},
                        is_complete=True,
                    )
                    break

        except Exception as exc:
            logger.exception("[RuntimeManagementAgentClient] send_request_stream failed: %s", exc)
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"error": str(exc)},
                is_complete=True,
            )
