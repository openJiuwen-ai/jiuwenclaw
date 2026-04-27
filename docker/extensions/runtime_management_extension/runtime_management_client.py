# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Agent Client.

通过 openjiuwen_runtime.management.orchestrator.Access 与 AgentServer 通信。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import uuid
from typing import Any, AsyncIterator, Optional

from jiuwenclaw.e2a.agent_compat import e2a_to_agent_request
from jiuwenclaw.e2a.models import E2AEnvelope
from jiuwenclaw.gateway.agent_client import AgentServerClient
from jiuwenclaw.schema import AgentRequest
from jiuwenclaw.schema.agent import AgentResponse, AgentResponseChunk

from openjiuwen_runtime.management.session.access import Access
from openjiuwen_runtime.management.session.dual_queue import PriorityDualAsyncQueues
from openjiuwen_runtime.management.session.interfaces import (
    IResponseParser,
    IServiceInstanceFactory,
    IServiceHandler,
    IRequest,
)
from openjiuwen_runtime.management.session.k8s_service_handler import (
    K8sDeployController,
    K8sServiceHandler,
)
from openjiuwen_runtime.management.session.models import AccessConfig, SessionConfig
from openjiuwen_runtime.management.session.service_handler import ServiceHandler
from openjiuwen_runtime.management.session.service_manager import (
    ServiceManager,
    QueueItem,
)
from openjiuwen_runtime.management.session.strategies.per_chat_bot import (
    PerChatBotStrategy,
)
from openjiuwen_runtime.management.session.timer import Timer
from openjiuwen_runtime.management.session.ws_client_channel import WSServiceMessageChannel
from openjiuwen_runtime.management.session.interfaces import ISessionRequest
from openjiuwen_runtime.management.session.models import MessagePriority

logger = logging.getLogger(__name__)


def _session_id_to_invoke_ids(self, session_id: str) -> tuple[str, str | None]:
    with self._session_instance_lock:
        cached = self._session_instance_map.get(session_id)
        if cached:
            return cached

    # SessionMap-generated ids:
    # per_chat_bot: provider::chat::bot::ts::suffix
    # per_chat_bot_user: provider::chat::bot::user::ts::suffix
    # For non-standard ids, fallback to md5(session_id) + no space_id.
    from jiuwenclaw.gateway.session_map import load_session_map_scope
    _ = load_session_map_scope()
    parts = session_id.split("::")
    if len(parts) == 6:
        _provider, chat_id, bot_id, user_id, _ts, _suffix = parts
        pair = (self._md5_id(chat_id, bot_id), user_id)
    elif len(parts) == 5:
        _provider, chat_id, bot_id, _ts, _suffix = parts
        pair = (self._md5_id(chat_id, bot_id), None)
    else:
        pair = (hashlib.md5(session_id.encode("utf-8")).hexdigest(), None)

    with self._session_instance_lock:
        self._session_instance_map.setdefault(session_id, pair)
        return self._session_instance_map[session_id]


class _SessionRequest(ISessionRequest):

    def __init__(self, msg: AgentRequest):
        self._req = msg

    @property
    def session_id(self) -> str:
        return self._req.session_id

    @property
    def session_concurrency(self) -> int:
        return 10

    @property
    def session_ttl(self) -> int:
        return 20

    @property
    def priority(self) -> "MessagePriority":
        return MessagePriority.LOW

    @property
    def request_id(self) -> Optional[str]:
        return self._req.request_id

    @property
    def raw_msg(self) -> Any:
        return self._req


def _e2a_nested_is_complete(data: dict[str, Any]) -> bool:
    """``provenance.details.is_complete``（jiuwenclaw 网关对 agent chunk 的归一化）。"""
    prov = data.get("provenance")
    if not isinstance(prov, dict):
        return False
    det = prov.get("details")
    if not isinstance(det, dict):
        return False
    return det.get("is_complete") is True

class E2aEnvelopResponseParser(IResponseParser):
    """解析 e2a / jiuwenclaw 网关归一化后的 WebSocket 下行 JSON。

    典型终态一帧（节选）::

        {
            "protocol_version": "1.0",
            "request_id": "req_xxx",
            "response_id": "req_xxx",
            "is_final": true,
            "status": "succeeded",
            "response_kind": "e2a.complete",
            "provenance": {"details": {"is_complete": true, ...}},
            "body": {"result": {}},
            ...
        }

    * ``request_id``：与上行多路复用键一致，优先取 ``request_id``，否则 ``response_id``/``id``。
    * **终态**（``is_completed`` 为真）：任一为真即可——``is_final``、``provenance.details.is_complete``、
      历史兼容字段（``error``/``done``/``is_end``/``event`` 等）。
    * ``response``：返回**整条原始 dict**（不剥 ``body``），便于业务自行读 ``body.result`` 等。
    """

    _END_EVENTS = {"stream.end", "stream.done", "chat.done", "response.end"}
    _TERMINAL_STATUS = {"succeeded", "failed", "canceled", "cancelled", "error"}

    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        rid = data.get("request_id") or data.get("response_id") or data.get("id")
        return str(rid) if rid is not None else None

    def is_completed(self, data: dict[str, Any]) -> bool:
        if data.get("is_final") is True:
            return True
        if _e2a_nested_is_complete(data):
            return True
        st = data.get("status")
        if isinstance(st, str) and st in self._TERMINAL_STATUS and st != "succeeded":
            return True
        if "error_code" in data or "error" in data:
            return True
        if data.get("completed") is True:
            return True
        if data.get("done") is True or data.get("is_end") is True:
            return True
        ev = data.get("event")
        if isinstance(ev, str) and ev in self._END_EVENTS:
            return True
        rk = data.get("response_kind")
        if isinstance(rk, str) and rk.endswith(".complete"):
            return True
        return False

    def response(self, data: dict[str, Any]) -> Any:
        return data


class RuntimeManagementAgentClient(AgentServerClient):
    """Runtime Management HTTP client."""

    def __init__(
        self,
    ) -> None:
        """初始化 Runtime Management 客户端。
        """

        class _Factory(IServiceInstanceFactory):
            async def new_service(
                    self, response_parser: IResponseParser
            ) -> IServiceHandler:
                k8s = K8sServiceHandler(
                    "swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:0.0.1",
                    name_prefix="jiuwenclaw",
                    namespace="default",
                    container_name="jiuwenclaw-agentserver",
                    container_port=18092,
                    port_name="http1",
                    image_pull_policy="IfNotPresent",
                    env_vars={
                        "MODEL_PROVIDER": "OpenAI",
                        "MODEL_NAME": "Qwen/Qwen3-32B",
                        "API_BASE": "https://api.siliconflow.cn/v1",
                        "API_KEY": "sk-xicwxncrmiymkavenhjupgtprrqcejzcvtvhtncpahutlabd"
                    },
                    kubeconfig=None,
                    readiness_initial_delay=5,
                    readiness_period=10,
                    ready_timeout=300,
                    ready_poll_interval=2,
                    nfs_server="192.168.1.90",
                    nfs_path="/",
                    nfs_mount_path="/home/app/.jiuwenclaw"
                )
                ch = WSServiceMessageChannel(
                    target_port=18092,
                    invoke_path="",
                    ws_use_tls=False,
                )
                return ServiceHandler(
                    total_concurrency=10,
                    message_channel=ch,
                    response_parser=response_parser,
                    deploy_controller=K8sDeployController(k8s),
                )

        dual_q: PriorityDualAsyncQueues[QueueItem] = PriorityDualAsyncQueues(
            1000, 100
        )

        factory = _Factory()
        sm = ServiceManager(
            service_factory=factory,
            dual_queue=dual_q,
            timer=Timer(),
            service_concurrency=10,
            min_idle_services=1,
            max_services=10,
            autoscale_interval=0.2,
            service_idle_ttl=30,
        )
        self._access: Any = Access(sm)
        self._connected = False

        logger.info(
            "[RuntimeManagementAgentClient] initialized",
        )

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        """更新服务端配置（可选实现）。"""
        return None

    async def connect(self, uri: str) -> None:
        """建立连接并初始化 Access。

        Args:
            uri: 连接 URI（可选，用于覆盖默认配置）
        """
        logger.info("[RuntimeManagementAgentClient] ready")

        if self._connected:
            return
        acc_cfg = AccessConfig(
            image="swr.cn-north-4.myhuaweicloud.com/openjiuwen/jiuwenclaw-agentserver-amd64:0.0.1",
            service_concurrency=10,
            min_idle_services=1,
            max_services=10,
            target_port=18092,
            invoke_path="",
            ws_use_tls=False,
            service_ttl=30,
            message_timeout=300,
            autoscale_interval=0.2,
        )

        session_cfg = SessionConfig(
            concurrency=10,
            ttl=20,
        )

        await self._access.init(
            response_parser=E2aEnvelopResponseParser(),
            config=acc_cfg,
            session_config=session_cfg,
        )
        self._connected = True

    async def disconnect(self) -> None:
        """断开连接并清理资源。"""
        try:
            if self._access and hasattr(self._access, "shutdown"):
                await self._access.shutdown()
                logger.info("[RuntimeManagementAgentClient] Access shutdown")
        except Exception as exc:
            logger.warning("[RuntimeManagementAgentClient] disconnect error: %s", exc)
        finally:
            self._connected = False
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
        session_request = _SessionRequest(request)

        try:
            # 调用 Access.send_message，它返回 AsyncIterator
            # Access 内部流程：
            # 1. 接收 IRequest
            # 2. strategy.handle_session() → ISessionRequest
            # 3. 创建 SessionRequestWrapper
            # 4. 投递到 ServiceManager
            # 5. 从 response_queue 读取并通过 IResponseParser 解析后 yield
            response_chunks = []
            async for chunk in self._access.send_message(session_request):
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
        session_request = _SessionRequest(request)

        try:
            # 调用 Access.send_message，它返回 AsyncIterator
            # Access 内部通过 IResponseParser 解析响应并逐个 yield
            async for chunk in self._access.send_message(session_request):
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
