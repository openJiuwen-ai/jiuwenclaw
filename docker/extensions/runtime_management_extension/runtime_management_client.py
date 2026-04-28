# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Runtime Management Agent Client.

通过 openjiuwen_runtime.management.orchestrator.Access 与 AgentServer 通信。
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import threading
import uuid
from typing import Any, AsyncIterator, Optional

from jiuwenclaw.e2a.agent_compat import e2a_to_agent_request
from jiuwenclaw.e2a.constants import (
    E2A_RESPONSE_KIND_E2A_CHUNK,
    E2A_RESPONSE_STATUS_IN_PROGRESS,
)
from jiuwenclaw.e2a.models import E2AEnvelope, E2AResponse
from jiuwenclaw.e2a.wire_codec import (
    is_e2a_response_wire_dict,
    parse_agent_server_wire_chunk,
    parse_agent_server_wire_unary,
)
from jiuwenclaw.gateway.agent_client import AgentServerClient, _wire_request_id_key
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

_session_id_lock = threading.Lock()
_session_id_to_service_pair: dict[str, tuple[str, str | None]] = {}


def _md5_chat_bot_id(chat_id: str, bot_id: str) -> str:
    return hashlib.md5("::".join((chat_id, bot_id)).encode("utf-8")).hexdigest()


def _session_id_to_invoke_ids(session_id: str) -> tuple[str, str | None]:
    """将网关 session_id 转为 invoker 的 (service_id, agent_id/space_id)，与 yuanrong_frontend_client 逻辑对齐。"""
    with _session_id_lock:
        cached = _session_id_to_service_pair.get(session_id)
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
        pair = (_md5_chat_bot_id(chat_id, bot_id), user_id)
    elif len(parts) == 5:
        _provider, chat_id, bot_id, _ts, _suffix = parts
        pair = (_md5_chat_bot_id(chat_id, bot_id), None)
    else:
        pair = (hashlib.md5(session_id.encode("utf-8")).hexdigest(), None)

    with _session_id_lock:
        _session_id_to_service_pair.setdefault(session_id, pair)
        return _session_id_to_service_pair[session_id]


class _SessionRequest(ISessionRequest):
    """ISessionRequest：上行负载与 WebSocket 直连 Agent 一致，使用 E2AEnvelope.to_dict() 的 JSON 串。"""

    def __init__(self, msg: AgentRequest, envelope: E2AEnvelope) -> None:
        self._req = msg
        self._envelope = envelope
        service_id, agent_id = _session_id_to_invoke_ids(self._req.session_id or "")
        self._service_id = service_id
        self._req.service_id = service_id
        self._req.agent_id = agent_id or ""
        # 与 AgentRequest 一致，供下游按 service_id / agent 路由
        self._envelope.service_id = service_id
        self._envelope.agent_id = agent_id or None

    @property
    def session_id(self) -> str:
        return self._service_id

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
        return json.dumps(self._envelope.to_dict(), ensure_ascii=False)


def _e2a_nested_is_complete(data: dict[str, Any]) -> bool:
    """``provenance.details.is_complete``（jiuwenclaw 网关对 agent chunk 的归一化）。"""
    prov = data.get("provenance")
    if not isinstance(prov, dict):
        return False
    det = prov.get("details")
    if not isinstance(det, dict):
        return False
    return det.get("is_complete") is True


def _wire_deprecated_unary_shape(data: dict[str, Any]) -> bool:
    """与 ``wire_codec._deprecated_unary_shape`` 一致（非 E2A 线 unary）。"""
    return (
        isinstance(data, dict)
        and "request_id" in data
        and "channel_id" in data
        and "ok" in data
        and not is_e2a_response_wire_dict(data)
    )


def _wire_deprecated_chunk_shape(data: dict[str, Any]) -> bool:
    """与 ``wire_codec._deprecated_chunk_shape`` 一致（非 E2A 线 chunk）。"""
    return (
        isinstance(data, dict)
        and "request_id" in data
        and "channel_id" in data
        and "is_complete" in data
        and "payload" in data
        and "ok" not in data
        and not is_e2a_response_wire_dict(data)
    )


class E2aEnvelopResponseParser(IResponseParser):
    """解析 AgentServer WebSocket 下行 JSON，与 ``WebSocketAgentServerClient`` 使用同一套 wire 解码。

    * ``request_id``：与 ``agent_client._wire_request_id_key`` 对齐；缺失时回退 ``response_id`` / ``id``。
    * ``is_completed``：E2A 线以 ``E2AResponse.is_final`` 为准；legacy unary 单帧即终态；legacy chunk 看 ``is_complete``。
    * ``response``：``parse_agent_server_wire_unary`` 或 ``parse_agent_server_wire_chunk``，与直连 WebSocket 客户端一致。
    """

    _END_EVENTS = {"stream.end", "stream.done", "chat.done", "response.end"}
    _TERMINAL_STATUS = {"succeeded", "failed", "canceled", "cancelled", "error"}

    def request_id(self, data: dict[str, Any]) -> Optional[str]:
        logger.debug(f"parse request_id: {data}")
        rid = data.get("request_id")
        if rid is None:
            rid = data.get("response_id")
        if rid is None:
            rid = data.get("id")
        if rid is None:
            return None
        out = _wire_request_id_key(rid)
        return out if out else None

    def is_completed(self, data: dict[str, Any]) -> bool:
        logger.debug(f"parse is_completed: {data}")
        if not isinstance(data, dict):
            return True
        if data.get("type") == "event":
            return False
        if is_e2a_response_wire_dict(data):
            try:
                e2a = E2AResponse.from_dict(dict(data))
                return bool(e2a.is_final)
            except Exception:
                return False
        if _wire_deprecated_unary_shape(data):
            return True
        if _wire_deprecated_chunk_shape(data):
            return bool(data.get("is_complete"))
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
        logger.debug(f"get response: {data}")

        return parse_agent_server_wire_chunk(data)


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
                    "",
                    name_prefix="jiuwenclaw",
                    namespace="default",
                    container_name="jiuwenclaw-agentserver",
                    container_port=18092,
                    port_name="http1",
                    image_pull_policy="IfNotPresent",
                    env_vars={
                        "AGENT_SERVER_HOST": "0.0.0.0",
                        "AGENT_CLIENT_TYPE": "runtime",
                        "MODEL_PROVIDER": "",
                        "MODEL_NAME": "",
                        "API_BASE": "",
                        "API_KEY": "",
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
            image="",
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
        logger.debug("send request: %s", envelope)
        self._ensure_connected()
        request = e2a_to_agent_request(envelope)

        # 构造 IRequest 实现（非流式）
        session_request = _SessionRequest(request, envelope)

        try:
            # 调用 Access.send_message，它返回 AsyncIterator
            # Access 内部流程：
            # 1. 接收 IRequest
            # 2. strategy.handle_session() → ISessionRequest
            # 3. 创建 SessionRequestWrapper
            # 4. 投递到 ServiceManager
            # 5. 从 response_queue 读取并通过 IResponseParser 解析后 yield
            async for chunk in self._access.send_message(session_request):
                logger.debug("received chunk: %s", chunk)
                return chunk

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
        logger.info(f"send stream request: {envelope}")
        self._ensure_connected()
        request = e2a_to_agent_request(envelope)

        # 构造 IRequest 实现（流式）
        session_request = _SessionRequest(request, envelope)

        try:
            # 调用 Access.send_message，它返回 AsyncIterator
            # Access 内部通过 IResponseParser 解析响应并逐个 yield
            async for chunk in self._access.send_message(session_request):
                logger.debug("yield chunk: %s", chunk)
                # Access 已经通过 IResponseParser.response() 解析了响应
                yield chunk

        except Exception as exc:
            logger.exception("[RuntimeManagementAgentClient] send_request_stream failed: %s", exc)
            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={"error": str(exc)},
                is_complete=True,
            )
