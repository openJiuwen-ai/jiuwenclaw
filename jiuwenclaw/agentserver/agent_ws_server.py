# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentWebSocketServer - Gateway 与 AgentServer 之间的 WebSocket 服务端."""

from __future__ import annotations

import logging
import asyncio
import json
import math
from pathlib import Path
from typing import Any, ClassVar

from jiuwenclaw.agentserver.agent_manager import AgentManager
from jiuwenclaw.agentserver.gateway_push.wire import build_server_push_wire
from jiuwenclaw.utils import get_agent_sessions_dir, get_config_file, FileTransferStartParams
from jiuwenclaw.e2a.agent_compat import e2a_to_agent_request
from jiuwenclaw.e2a.constants import (
    FILE_TRANSFER_START,
    FILE_TRANSFER_CHUNK,
    FILE_TRANSFER_COMPLETE,
    FILE_TRANSFER_EVENT_TYPES,
)
from jiuwenclaw.e2a.gateway_normalize import (
    E2A_FALLBACK_FAILED_KEY,
    E2A_INTERNAL_CONTEXT_KEY,
    E2A_LEGACY_AGENT_REQUEST_KEY,
)
from jiuwenclaw.e2a.models import E2AEnvelope
from jiuwenclaw.e2a.wire_codec import (
    encode_agent_chunk_for_wire,
    encode_agent_response_for_wire,
    encode_json_parse_error_wire,
)
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.schema.hook_event import AgentServerHookEvents
from jiuwenclaw.schema.hooks_context import AgentServerChatHookContext, AgentWsServerStartHookContext
from jiuwenclaw.agentserver.file_transfer_manager import get_file_transfer_manager


logger = logging.getLogger(__name__)


def _payload_to_request(data: dict[str, Any]) -> AgentRequest:
    """将 Gateway 发送的 JSON 载荷解析为 AgentRequest."""
    from jiuwenclaw.schema.message import ReqMethod

    req_method = data.get("req_method")
    if req_method is not None and isinstance(req_method, str):
        req_method = ReqMethod(req_method)

    return AgentRequest(
        request_id=data["request_id"],
        channel_id=data.get("channel_id", ""),
        session_id=data.get("session_id"),
        req_method=req_method,
        params=data.get("params", {}),
        is_stream=data.get("is_stream", False),
        timestamp=data.get("timestamp", 0.0),
        metadata=data.get("metadata"),
    )


class AgentWebSocketServer:
    """Gateway 与 AgentServer 之间的 WebSocket 服务端（单例）.

    监听来自 Gateway (WebSocketAgentServerClient) 的连接，按协议约定处理请求：
    - 收到 JSON：E2AEnvelope（或过渡期 legacy + 兜底信封）
    - is_stream=False：``process_message`` → 一条 **E2AResponse** JSON（``jiuwenclaw.e2a.wire_codec``）
    - is_stream=True：逐条 **E2AResponse** JSON（chunk/complete/error）
    - 例外：首帧 ``connection.ack`` 仍为 ``type/event`` 事件帧

    支持 send_push：推送帧亦为 E2AResponse 线格式（由 chunk 编码）。
    """

    _instance: ClassVar[AgentWebSocketServer | None] = None

    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = 18000,
        *,
        ping_interval: float | None = 30.0,
        ping_timeout: float | None = 300.0,
    ) -> None:
        self._agent_manager = AgentManager()
        self._host = host
        self._port = port
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._server: Any = None
        # 当前 Gateway 连接，用于 send_push 主动推送
        self._current_ws: Any = None
        self._current_send_lock: asyncio.Lock | None = None

    @classmethod
    def get_instance(
        cls,
        *,
        agent: Any = None,
        host: str = "127.0.0.1",
        port: int = 18000,
        ping_interval: float | None = 30.0,
        ping_timeout: float | None = 300.0,
    ) -> "AgentWebSocketServer":
        """返回单例实例。

        首次调用时 agent 可选（若未提供则在 start() 时自动创建 JiuWenClaw 实例）。
        后续调用可省略所有参数，返回已存在的实例。
        """
        if cls._instance is not None:
            return cls._instance
        cls._instance = cls(
            host=host,
            port=port,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）。"""
        cls._instance = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        """启动 WebSocket 服务端，开始监听连接。优先使用 legacy.server.serve 以与 Gateway 的 legacy client 握手兼容."""
        await self._trigger_before_ws_server_start_hook()
        await self._agent_manager.initialize()
        logger.info("[AgentWebSocketServer] 已初始化 AgentManager")

        if self._server is not None:
            logger.warning("[AgentWebSocketServer] 服务端已在运行")
            return

        # 启动文件传输管理器的清理任务
        ft_manager = get_file_transfer_manager()
        if ft_manager.enabled:
            await ft_manager.start_cleanup_task()

        try:
            from websockets.legacy.server import serve as legacy_serve
            self._server = await legacy_serve(
                self._connection_handler,
                self._host,
                self._port,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
            )
        except ImportError:
            import websockets
            self._server = await websockets.serve(
                self._connection_handler,
                self._host,
                self._port,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
            )
        logger.info(
            "[AgentWebSocketServer] 已启动: ws://%s:%s", self._host, self._port
        )

    async def stop(self) -> None:
        """停止 WebSocket 服务端."""
        # 停止文件传输管理器的清理任务
        ft_manager = get_file_transfer_manager()
        if ft_manager.enabled:
            await ft_manager.stop_cleanup_task()

        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        logger.info("[AgentWebSocketServer] 已停止")

    # ---------- 连接处理 ----------

    async def _connection_handler(self, ws: Any) -> None:
        """处理单个 Gateway WebSocket 连接，同一连接可并发处理多个请求."""
        import websockets
        # 和客户端连接成功后, 触发agentserver启动成功的回调事件
        await self._trigger_agent_server_started_hook()

        remote = ws.remote_address
        logger.info("[AgentWebSocketServer] 新连接: %s", remote)

        send_lock = asyncio.Lock()
        self._current_ws = ws
        self._current_send_lock = send_lock

        # 发送 connection.ack 事件，通知 Gateway 服务端已就绪
        try:
            ack_frame = {
                "type": "event",
                "event": "connection.ack",
                "payload": {"status": "ready"},
            }
            await ws.send(json.dumps(ack_frame, ensure_ascii=False))
            logger.info("[AgentWebSocketServer] 已发送 connection.ack: %s", remote)
        except Exception as e:
            logger.warning("[AgentWebSocketServer] 发送 connection.ack 失败: %s", e)

        tasks: set[asyncio.Task] = set()

        try:
            async for raw in ws:
                task = asyncio.create_task(self._handle_message(ws, raw, send_lock))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except websockets.exceptions.ConnectionClosed:
            logger.info("[AgentWebSocketServer] 连接关闭: %s", remote)
        except Exception as e:
            logger.exception("[AgentWebSocketServer] 连接处理异常 (%s): %s", remote, e)
        finally:
            self._current_ws = None
            self._current_send_lock = None
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)

    async def _handle_message(self, ws: Any, raw: str | bytes, send_lock: asyncio.Lock) -> None:
        """解析一条 JSON 请求并分发到 IAgentServer 处理."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            wire = encode_json_parse_error_wire(
                request_id="",
                channel_id="",
                message=f"JSON 解析失败: {e}",
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            env = E2AEnvelope.from_dict(data)
        except Exception as parse_err:
            logger.warning(
                "[AgentWebSocketServer] E2A from_dict 失败，按旧载荷解析: %s",
                parse_err,
            )
            request = _payload_to_request(data)
        else:
            jw = (env.channel_context or {}).get(E2A_INTERNAL_CONTEXT_KEY)
            if isinstance(jw, dict) and jw.get(E2A_FALLBACK_FAILED_KEY):
                legacy = jw.get(E2A_LEGACY_AGENT_REQUEST_KEY)
                logger.warning(
                    "[E2A][fallback] using legacy_agent_request request_id=%s",
                    env.request_id,
                )
                if not isinstance(legacy, dict):
                    raise ValueError("legacy_agent_request missing or not a dict")
                request = _payload_to_request(legacy)
            # 文件传输请求：method 不在 ReqMethod 枚举中，需要特殊处理
            elif env.method in (FILE_TRANSFER_START, FILE_TRANSFER_CHUNK, FILE_TRANSFER_COMPLETE):
                logger.info(
                    "[E2A][in] request_id=%s channel=%s method=%s is_stream=%s",
                    env.request_id,
                    env.channel,
                    env.method,
                    env.is_stream,
                )
                # 直接构造 AgentRequest，不走 e2a_to_agent_request
                request = AgentRequest(
                    request_id=env.request_id or "",
                    channel_id=env.channel or "",
                    session_id=env.session_id,
                    req_method=None,  # 文件传输没有对应的 ReqMethod
                    params=dict(env.params or {}),
                    is_stream=False,
                    timestamp=0.0,
                    metadata=None,
                )
            else:
                logger.info(
                    "[E2A][in] request_id=%s channel=%s method=%s is_stream=%s",
                    env.request_id,
                    env.channel,
                    env.method,
                    env.is_stream,
                )
                request = e2a_to_agent_request(env)

        logger.info(
            "[AgentWebSocketServer] 收到请求: request_id=%s channel_id=%s is_stream=%s",
            request.request_id,
            request.channel_id,
            request.is_stream,
        )

        try:
            from jiuwenclaw.schema.message import ReqMethod

            await self._trigger_before_chat_request_hook(request)

            if request.req_method == ReqMethod.HISTORY_GET:
                if request.is_stream:
                    await self._handle_history_get_stream(ws, request, send_lock)
                else:
                    await self._handle_history_get(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.BROWSER_START:
                await self._handle_browser_start(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.BROWSER_RUNTIME_RESTART:
                await self._handle_browser_runtime_restart(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.CONFIG_CACHE_CLEAR:
                await self._handle_config_cache_clear(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.AGENT_RELOAD_CONFIG:
                await self._handle_agent_reload_config(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SESSION_CREATE:
                await self._handle_session_create(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SESSION_DELETE:
                await self._handle_session_delete(ws, request, send_lock)
                return
            # 文件传输处理
            event_type = request.params.get("event_type") if isinstance(request.params, dict) else None
            if event_type in FILE_TRANSFER_EVENT_TYPES:
                await self._handle_file_transfer(ws, request, send_lock)
                return
            if request.is_stream:
                await self._handle_stream(ws, request, send_lock)
            else:
                await self._handle_unary(ws, request, send_lock)
        except Exception as e:
            logger.exception(
                "[AgentWebSocketServer] 处理请求失败: request_id=%s: %s",
                request.request_id,
                e,
            )
            error_resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(
                error_resp, response_id=request.request_id
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

    @staticmethod
    async def _trigger_agent_server_started_hook() -> None:
        """在agentserver启动成功触发扩展；未初始化 ExtensionRegistry 时跳过。"""
        from jiuwenclaw.extensions.registry import ExtensionRegistry
        from jiuwenclaw.utils import get_agent_skills_dir

        ctx = AgentWsServerStartHookContext(skills_dir=str(get_agent_skills_dir()))
        await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.AGENT_SERVER_STARTED, ctx)

    @staticmethod
    async def _trigger_before_ws_server_start_hook() -> None:
        """在首次 create_instance（register_skill）之前触发扩展；未初始化 ExtensionRegistry 时跳过。"""
        from jiuwenclaw.extensions.registry import ExtensionRegistry
        from jiuwenclaw.utils import get_agent_skills_dir

        ctx = AgentWsServerStartHookContext(skills_dir=str(get_agent_skills_dir()))
        await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.BEFORE_WS_SERVER_START, ctx)

    @staticmethod
    async def _trigger_before_chat_request_hook(request: AgentRequest) -> None:
        from jiuwenclaw.extensions.registry import ExtensionRegistry

        params = request.params if isinstance(request.params, dict) else {}
        if not isinstance(request.params, dict):
            request.params = params

        ctx = AgentServerChatHookContext(
            request_id=request.request_id,
            channel_id=request.channel_id,
            session_id=request.session_id,
            req_method=request.req_method.value if request.req_method is not None else None,
            params=params,
        )

        await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.BEFORE_CHAT_REQUEST, ctx)

    async def _handle_unary(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """非流式处理：调用 process_message，返回一条 E2AResponse 线 JSON。"""
        await self._agent_manager.prepare_agent(request.session_id)
        agent = self._agent_manager.get_agent(request.session_id)
        resp = await agent.process_message(request)
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))
        logger.info(
            "[AgentWebSocketServer] 非流式响应已发送: request_id=%s",
            request.request_id,
        )

    async def _handle_stream(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """流式处理：调用 process_message_stream，逐条发送 E2AResponse 线 JSON。"""
        chunk_count = 0
        await self._agent_manager.prepare_agent(request.session_id)
        agent = self._agent_manager.get_agent(request.session_id)
        async for chunk in agent.process_message_stream(request):
            chunk_count += 1
            wire = encode_agent_chunk_for_wire(
                chunk,
                response_id=request.request_id,
                sequence=chunk_count - 1,
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
        logger.info(
            "[AgentWebSocketServer] 流式响应已发送: request_id=%s 共 %s 个 chunk",
            request.request_id,
            chunk_count,
        )

    async def _handle_history_get(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        session_id = params.get("session_id")
        page_idx = params.get("page_idx")
        data = self.get_conversation_history(session_id=session_id, page_idx=page_idx)
        if data is None:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "invalid page_idx or session history not found"},
            )
        else:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=data,
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_history_get_stream(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        session_id = params.get("session_id")
        page_idx = params.get("page_idx")
        data = self.get_conversation_history(session_id=session_id, page_idx=page_idx)
        if data is None:
            err_chunk = AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={
                    "event_type": "chat.error",
                    "error": "invalid page_idx or session history not found",
                },
                is_complete=True,
            )
            wire = encode_agent_chunk_for_wire(
                err_chunk,
                response_id=request.request_id,
                sequence=0,
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        messages = data.get("messages", [])
        total_pages = data.get("total_pages")
        page = data.get("page_idx")
        if isinstance(messages, list):
            for seq, item in enumerate(messages):
                chunk = AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={
                        "event_type": "history.message",
                        "message": item,
                        "total_pages": total_pages,
                        "page_idx": page,
                    },
                    is_complete=False,
                )
                wire = encode_agent_chunk_for_wire(
                    chunk,
                    response_id=request.request_id,
                    sequence=seq,
                )
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))

        done_chunk = AgentResponseChunk(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={
                "event_type": "history.message",
                "status": "done",
                "total_pages": total_pages,
                "page_idx": page,
            },
            is_complete=True,
        )
        done_seq = len(messages) if isinstance(messages, list) else 0
        wire_done = encode_agent_chunk_for_wire(
            done_chunk,
            response_id=request.request_id,
            sequence=done_seq,
        )
        async with send_lock:
            await ws.send(json.dumps(wire_done, ensure_ascii=False))

    async def _handle_browser_start(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """启动浏览器并返回执行结果（returncode）。"""
        try:
            from jiuwenclaw.agentserver.tools.browser_start_client import start_browser

            config_path = str(get_config_file())
            returncode = start_browser(dry_run=False, config_file=config_path)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"returncode": returncode},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] browser.start failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_browser_runtime_restart(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            from jiuwenclaw.agentserver.tools.browser_tools import restart_local_browser_runtime_server

            result = restart_local_browser_runtime_server()
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"result": result},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] browser.runtime_restart failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_config_cache_clear(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            from jiuwenclaw.agentserver.memory.config import clear_config_cache

            clear_config_cache()
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"cleared": True},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] config.cache_clear failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_agent_reload_config(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            config_payload = params.get("config")
            env_overrides = params.get("env")
            agent = self._agent_manager.get_agent(request.session_id)
            await agent.reload_agent_config(
                config_base=config_payload,
                env_overrides=env_overrides,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"reloaded": True},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] agent.reload_config failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_create(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 session.create 请求：在 Agent 本机 sessions 目录下创建会话目录并初始化元数据。"""
        import shutil

        from jiuwenclaw.agentserver.session_id_safe import (
            normalize_safe_session_id,
            resolve_session_dir_under_root,
        )
        from jiuwenclaw.agentserver.session_metadata import init_session_metadata

        try:
            params = request.params if isinstance(request.params, dict) else {}
            raw_sid = str(params.get("session_id") or "").strip()
            if not raw_sid:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "session_id is required", "code": "BAD_REQUEST"},
                )
            else:
                safe_sid = normalize_safe_session_id(raw_sid)
                if safe_sid is None:
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=False,
                        payload={"error": "invalid session_id", "code": "BAD_REQUEST"},
                    )
                else:
                    workspace_session_dir = get_agent_sessions_dir()
                    workspace_session_dir.mkdir(parents=True, exist_ok=True)
                    session_dir = resolve_session_dir_under_root(workspace_session_dir, safe_sid)
                    if session_dir is None:
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=False,
                            payload={"error": "invalid session_id", "code": "BAD_REQUEST"},
                        )
                    elif session_dir.exists():
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=False,
                            payload={"error": "session already exists", "code": "ALREADY_EXISTS"},
                        )
                    else:
                        session_dir.mkdir()
                        try:
                            init_session_metadata(
                                session_id=safe_sid,
                                channel_id=params.get("channel_id", ""),
                                user_id=params.get("user_id", ""),
                                title=params.get("title", ""),
                            )
                        except Exception:
                            shutil.rmtree(session_dir, ignore_errors=True)
                            raise
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=True,
                            payload={"session_id": safe_sid},
                        )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] session.create failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_delete(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 session.delete 请求：删除 Agent 本机 sessions 目录中的会话。"""
        import shutil

        from jiuwenclaw.agentserver.session_id_safe import (
            normalize_safe_session_id,
            resolve_session_dir_under_root,
        )

        try:
            params = request.params if isinstance(request.params, dict) else {}
            raw_sid = str(params.get("session_id") or "").strip()
            if not raw_sid:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "session_id is required", "code": "BAD_REQUEST"},
                )
            else:
                safe_sid = normalize_safe_session_id(raw_sid)
                if safe_sid is None:
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=False,
                        payload={"error": "invalid session_id", "code": "BAD_REQUEST"},
                    )
                else:
                    workspace_session_dir = get_agent_sessions_dir()
                    session_dir = resolve_session_dir_under_root(workspace_session_dir, safe_sid)
                    if session_dir is None:
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=False,
                            payload={"error": "invalid session_id", "code": "BAD_REQUEST"},
                        )
                    elif not session_dir.exists():
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=False,
                            payload={"error": "session not found", "code": "NOT_FOUND"},
                        )
                    elif not session_dir.is_dir():
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=False,
                            payload={"error": "session is not a directory", "code": "BAD_REQUEST"},
                        )
                    else:
                        shutil.rmtree(session_dir)
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=True,
                            payload={"session_id": safe_sid},
                        )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] session.delete failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def send_push(self, msg) -> None:
        """AgentServer 主动向 Gateway 推送消息。

        payload 格式与 AgentResponse.payload 一致，
        可含 event_type 等字段供 Gateway 转为 Message 派发到 Channel。
        """
        if self._current_ws is None or self._current_send_lock is None:
            logger.warning(
                "[AgentWebSocketServer] send_push 失败: 无活跃 Gateway 连接"
            )
            return

        try:
            wire = build_server_push_wire(msg)
            async with self._current_send_lock:
                await self._current_ws.send(json.dumps(wire, ensure_ascii=False))
            response_kind = str(msg.get("response_kind") or "").strip()
            if response_kind:
                logger.info(
                    "[AgentWebSocketServer] send_push response_kind wire sent: channel_id=%s kind=%s",
                    msg.get("channel_id", ""),
                    response_kind,
                )
            else:
                logger.info(
                    "[AgentWebSocketServer] send_push 已发送(E2A wire): channel_id=%s",
                    msg.get("channel_id", ""),
                )
        except Exception as e:
            logger.warning("[AgentWebSocketServer] send_push 失败: %s", e)

    def get_agent(self):
        agent = self._agent_manager.get_agent("default_session")
        return getattr(agent, "_instance", None)

    # =========================================================================
    # 文件传输处理
    # =========================================================================

    async def _handle_file_transfer(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理文件传输请求（Gateway -> AgentServer）.

        支持：
        - file.transfer.start: 开始传输
        - file.transfer.chunk: 传输分片
        - file.transfer.complete: 完成传输
        """
        params = request.params if isinstance(request.params, dict) else {}
        event_type = params.get("event_type", "")
        transfer_id = params.get("transfer_id", "")

        ft_manager = get_file_transfer_manager()

        # 检查是否启用分布式模式
        if not ft_manager.enabled:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "error": "file transfer not enabled (distributed mode required)",
                    "event_type": event_type,
                },
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            if event_type == FILE_TRANSFER_START:
                ft_params = FileTransferStartParams(
                    transfer_id=transfer_id,
                    filename=params.get("filename", "unnamed"),
                    file_size=params.get("file_size", 0),
                    sha256=params.get("sha256", ""),
                    total_chunks=params.get("total_chunks", 0),
                    chunk_size=params.get("chunk_size", 65536),
                    mime_type=params.get("mime_type", ""),
                    session_id=request.session_id or "",
                )
                result = await ft_manager.handle_transfer_start(ft_params)
            elif event_type == FILE_TRANSFER_CHUNK:
                result = await ft_manager.handle_transfer_chunk(
                    transfer_id=transfer_id,
                    chunk_index=params.get("chunk_index", 0),
                    base64_data=params.get("base64_data", ""),
                )
            elif event_type == FILE_TRANSFER_COMPLETE:
                result = await ft_manager.handle_transfer_complete(
                    transfer_id=transfer_id,
                    sha256=params.get("sha256", ""),
                )
            else:
                result = {
                    "accepted": False,
                    "error": f"unknown event_type: {event_type}",
                }

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=result.get("success", result.get("accepted", False)),
                payload={
                    "event_type": event_type,
                    **result,
                },
            )
        except Exception as e:
            logger.exception(
                "[AgentWebSocketServer] 文件传输处理失败: %s",
                e,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "error": str(e),
                    "event_type": event_type,
                },
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    @staticmethod
    def get_conversation_history(session_id: str, page_idx: int) -> dict[str, Any] | None:
        # 按照 session_id 和分页消息获取历史记录
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        if not isinstance(page_idx, int) or page_idx <= 0:
            return None

        history_path: Path = get_agent_sessions_dir() / session_id.strip() / "history.json"
        if not history_path.exists():
            return None
        try:
            raw = json.loads(history_path.read_text(encoding="utf-8"))
        except Exception:
            return None
        if not isinstance(raw, list):
            return None

        page_size = 50
        total = len(raw)
        total_pages = max(1, math.ceil(total / page_size))
        if page_idx > total_pages:
            return None

        ordered = list(reversed(raw))
        start = (page_idx - 1) * page_size
        end = start + page_size
        return {
            "messages": ordered[start:end],
            "total_pages": total_pages,
            "page_idx": page_idx,
        }
    
    def is_working(self) -> dict:
        """返回 Agent 是否正在工作的状态.

        用于沙箱保活校验。

        Returns:
            dict: 工作状态信息，包含 working, initialized, model_configured,
                  active_tasks, active_sessions 字段.
        """
        return self._agent_manager.is_working()