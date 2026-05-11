# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentWebSocketServer - Gateway 与 AgentServer 之间的 WebSocket 服务端."""

from __future__ import annotations

import asyncio
import json
import logging
import math
import os
from pathlib import Path
from typing import Any, ClassVar

from jiuwenclaw.agentserver.gateway_push.wire import build_server_push_wire
from jiuwenclaw.agentserver.tools.acp_output_tools import get_acp_output_manager
from jiuwenclaw.utils import get_agent_sessions_dir, get_config_file
from jiuwenclaw.e2a.agent_compat import e2a_to_agent_request
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
from jiuwenclaw.agentserver.extensions import get_rail_manager
from jiuwenclaw.agentserver.permissions.patterns import persist_cli_trusted_directory
from jiuwenclaw.schema.hooks_context import AgentServerChatHookContext
from jiuwenclaw.agentserver.agent_manager import AgentManager, ACP_DEFAULT_CAPABILITIES
from jiuwenclaw.agentserver.permissions.config_rpc import get_permissions_config_req_methods
from jiuwenclaw.config import (
    get_config,
    get_mcp_server_config,
    get_mcp_servers,
    remove_mcp_server_in_config,
    set_mcp_server_enabled_in_config,
    upsert_mcp_server_in_config,
)
from jiuwenclaw.security.ws_origin import (
    extract_handshake_request,
    forbidden_origin_response,
    get_header_value,
    is_allowed_browser_origin,
)


logger = logging.getLogger(__name__)

# 流式处理心跳间隔：当 Agent 处理时间超过此阈值时，发送心跳 chunk 保持 WebSocket 连接活跃
# 避免 ping_timeout 导致连接关闭。默认 10 秒，小于服务端 ping_timeout=20s。
_STREAM_HEARTBEAT_INTERVAL_SECONDS = 10.0


def _payload_to_request(data: dict[str, Any]) -> AgentRequest:
    """将 Gateway 发送的 JSON 载荷解析为 AgentRequest."""
    from jiuwenclaw.schema.message import ReqMethod

    req_method = data.get("req_method")
    if req_method is not None and isinstance(req_method, str):
        req_method = ReqMethod(req_method)

    return AgentRequest(
        request_id=data["request_id"],
        channel_id=data.get("channel_id", "web"),
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
        self._host = host
        self._port = port
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._server: Any = None
        # 当前 Gateway 连接，用于 send_push 主动推送
        self._current_ws: Any = None
        self._current_send_lock: asyncio.Lock | None = None
        self._acp_client_capabilities_by_ws: dict[int, dict[str, Any]] = {}
        # AgentManager 实例
        self._agent_manager = AgentManager()
        get_acp_output_manager().set_send_push_callback(
            lambda msg: asyncio.create_task(self.send_push(msg))
        )

    @staticmethod
    def _ws_capabilities_key(ws: Any) -> int:
        return id(ws)

    def _set_ws_acp_client_capabilities(self, ws: Any, capabilities: dict[str, Any] | None) -> None:
        key = self._ws_capabilities_key(ws)
        if isinstance(capabilities, dict):
            self._acp_client_capabilities_by_ws[key] = dict(capabilities)
        else:
            self._acp_client_capabilities_by_ws.pop(key, None)

    def _get_ws_acp_client_capabilities(self, ws: Any) -> dict[str, Any]:
        key = self._ws_capabilities_key(ws)
        caps = self._acp_client_capabilities_by_ws.get(key)
        return dict(caps) if isinstance(caps, dict) else {}

    def _clear_ws_acp_client_capabilities(self, ws: Any) -> None:
        self._acp_client_capabilities_by_ws.pop(self._ws_capabilities_key(ws), None)

    @classmethod
    def get_instance(
        cls,
        *,
        host: str = "127.0.0.1",
        port: int = 18000,
        ping_interval: float | None = 30.0,
        ping_timeout: float | None = 300.0,
    ) -> "AgentWebSocketServer":
        """返回单例实例。

        首次调用时创建实例，后续调用返回已存在的实例。
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
        if self._server is not None:
            logger.warning("[AgentWebSocketServer] 服务端已在运行")
            return

        # Reset harness package state to native on service startup
        from jiuwenclaw.agentserver.deep_agent.auto_harness_service import reset_harness_packages_state
        reset_harness_packages_state()

        try:
            from websockets.legacy.server import serve as legacy_serve
            self._server = await legacy_serve(
                self._connection_handler,
                self._host,
                self._port,
                process_request=self._process_request,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
            )
        except ImportError:
            import websockets
            self._server = await websockets.serve(
                self._connection_handler,
                self._host,
                self._port,
                process_request=self._process_request,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
            )
        logger.info(
            "[AgentWebSocketServer] 已启动: ws://%s:%s", self._host, self._port
        )

    async def _process_request(self, *args: Any) -> Any:
        """在握手阶段执行 Origin 校验，兼容 legacy/new websockets APIs。"""
        path, request_headers = extract_handshake_request(args)
        origin = get_header_value(request_headers, "Origin")
        allowed = is_allowed_browser_origin(origin)
        logger.info(
            "[AgentWebSocketServer] 握手检查 path=%s origin=%s allowed=%s",
            path,
            origin,
            allowed,
        )
        if allowed:
            return None

        logger.warning(
            "[AgentWebSocketServer] 握手拒绝 path=%s origin=%s reason=origin_not_allowed",
            path,
            origin,
        )
        return forbidden_origin_response(args)

    async def stop(self) -> None:
        """停止 WebSocket 服务端."""
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
            self._clear_ws_acp_client_capabilities(ws)
            # Gateway 进程退出/端口关闭时，必须先取消各 session 内流式生产者（SessionManager）
            # 并中止 DeepAgent 内层循环；否则仅等待 _handle_message 任务结束会一直阻塞到任务自然完成。
            try:
                await self._agent_manager.cancel_all_inflight_work(
                    reason=f"[gateway ws closed {remote}] ",
                )
            except Exception:
                logger.exception("[AgentWebSocketServer] cancel_all_inflight_work failed")
            try:
                from jiuwenclaw.agentserver.team import cancel_all_team_stream_tasks_across_managers

                await cancel_all_team_stream_tasks_across_managers(
                    reason=f"[gateway ws closed {remote}] ",
                )
            except Exception:
                logger.exception("[AgentWebSocketServer] team stream cancel failed")
            if tasks:
                for t in list(tasks):
                    if not t.done():
                        t.cancel()
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

            if request.channel_id == "acp" and request.req_method != ReqMethod.INITIALIZE:
                metadata = dict(request.metadata or {})
                ws_caps = self._get_ws_acp_client_capabilities(ws)
                metadata.setdefault(
                    "acp_client_capabilities",
                    ws_caps or self._agent_manager.get_client_capabilities("acp"),
                )
                request.metadata = metadata

            await self._trigger_before_chat_request_hook(request)

            if request.req_method == ReqMethod.SESSION_LIST:
                await self._handle_session_list(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SESSION_RENAME:
                await self._handle_session_rename(ws, request, send_lock)
                return
            if request.req_method in get_permissions_config_req_methods():
                await self._handle_permissions_config(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HISTORY_GET:
                if request.is_stream:
                    await self._handle_history_get_stream(ws, request, send_lock)
                else:
                    await self._handle_history_get(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_ADD_DIR:
                await self._handle_command_add_dir(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_CHROME:
                await self._handle_command_chrome(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_COMPACT:
                await self._handle_command_compact(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_DIFF:
                await self._handle_command_diff(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_MODEL:
                await self._handle_command_model(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_MCP:
                await self._handle_command_mcp(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_RESUME:
                await self._handle_command_resume(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_SESSION:
                await self._handle_command_session(ws, request, send_lock)
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
            if request.req_method == ReqMethod.EXTENSIONS_LIST:
                await self._handle_extensions_list(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_IMPORT:
                await self._handle_extensions_import(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_DELETE:
                await self._handle_extensions_delete(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_TOGGLE:
                await self._handle_extensions_toggle(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HARNESS_PACKAGES_GET:
                await self._handle_harness_packages_get(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HARNESS_PACKAGES_SCAN:
                await self._handle_harness_packages_scan(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HARNESS_PACKAGES_ACTIVATE:
                await self._handle_harness_packages_activate(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HARNESS_PACKAGES_DELETE:
                await self._handle_harness_packages_delete(ws, request, send_lock)
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
    def _should_trigger_before_chat_request_hook(request: AgentRequest) -> bool:
        from jiuwenclaw.schema.message import ReqMethod

        return request.req_method in (
            ReqMethod.CHAT_SEND,
            ReqMethod.CHAT_RESUME,
            ReqMethod.CHAT_ANSWER,
        )

    async def _trigger_before_chat_request_hook(self, request: AgentRequest) -> None:
        if not self._should_trigger_before_chat_request_hook(request):
            return
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
        from jiuwenclaw.schema.message import ReqMethod

        channel_id = request.channel_id or "default"

        if request.req_method == ReqMethod.INITIALIZE:
            await self._handle_initialize(ws, request, send_lock)
            return

        if request.req_method == ReqMethod.SESSION_CREATE:
            await self._handle_session_create(ws, request, send_lock)
            return

        if request.req_method == ReqMethod.ACP_TOOL_RESPONSE:
            await self._handle_acp_tool_response(ws, request, send_lock)
            return

        mode = request.params.get("mode", "agent.plan").split(".")[0]
        agent_mode = "agent" if mode == "auto_harness" else mode
        try:
            sub_mode = request.params.get("mode", "agent.plan").split(".")[1]
        except IndexError:
            sub_mode ="plan"
        trusted_dirs = request.params.get("trusted_dirs", None)
        agent = await self._agent_manager.get_agent(
            channel_id=channel_id,
            mode=agent_mode,
            project_dir=trusted_dirs[0] if trusted_dirs else None,
            sub_mode=sub_mode,
        )
        if agent is None:
            raise ValueError("Failed to get agent")

        # code 模式：在真实 session 上执行 switch_mode，确保 state 持久化
        if mode == "code":
            from openjiuwen.core.single_agent import create_agent_session
            session = create_agent_session(session_id=request.session_id, card=agent.get_instance().card)
            await session.pre_run(inputs=None)  # 从 checkpointer 加载历史 state
            agent.get_instance().switch_mode(session=session, mode=sub_mode)
            # 持久化 switch_mode 修改后的 state
            state = agent.get_instance().load_state(session)
            session.update_state({"deep_agent_state": state.to_session_dict()})
            await session.post_run()  # 写入 checkpointer

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
        channel_id = request.channel_id or "default"
        mode = request.params.get("mode", "agent.plan").split(".")[0]
        agent_mode = "agent" if mode == "auto_harness" else mode
        try:
            sub_mode = request.params.get("mode", "agent.plan").split(".")[1]
        except IndexError:
            sub_mode = "plan"
        trusted_dirs = request.params.get("trusted_dirs", None)
        agent = await self._agent_manager.get_agent(
            channel_id=channel_id,
            mode=agent_mode,
            project_dir=trusted_dirs[0] if trusted_dirs else None,
            sub_mode=sub_mode,
        )
        if agent is None:
            raise ValueError("Failed to get agent")

        # code 模式：在真实 session 上执行 switch_mode，确保 state 持久化
        if mode == "code":
            from openjiuwen.core.single_agent import create_agent_session
            session = create_agent_session(session_id=request.session_id, card=agent.get_instance().card)
            await session.pre_run(inputs=None)  # 从 checkpointer 加载历史 state
            agent.get_instance().switch_mode(session=session, mode=sub_mode)
            # 持久化 switch_mode 修改后的 state
            state = agent.get_instance().load_state(session)
            session.update_state({"deep_agent_state": state.to_session_dict()})
            await session.post_run()  # 写入 checkpointer

        chunk_count = 0
        # 心跳控制：当有真实 chunk 发送时重置，空闲时发送心跳
        heartbeat_event = asyncio.Event()
        heartbeat_task: asyncio.Task | None = None

        async def _heartbeat_loop() -> None:
            """后台心跳任务：在空闲期间定期发送 keepalive chunk."""
            try:
                while True:
                    # 等待心跳间隔，如果期间有真实 chunk 发送则 heartbeat_event 被设置，重置等待
                    try:
                        await asyncio.wait_for(
                            heartbeat_event.wait(),
                            timeout=_STREAM_HEARTBEAT_INTERVAL_SECONDS,
                        )
                        # 有真实 chunk 发送，重置 event 继续等待
                        heartbeat_event.clear()
                    except asyncio.TimeoutError:
                        # 超时：空闲超过心跳间隔，发送 keepalive chunk
                        heartbeat_chunk = AgentResponseChunk(
                            request_id=request.request_id,
                            channel_id=channel_id,
                            payload={"event_type": "keepalive"},
                            is_complete=False,
                        )
                        wire = encode_agent_chunk_for_wire(
                            heartbeat_chunk,
                            response_id=request.request_id,
                            sequence=-1,  # 心跳使用特殊序列号 -1
                        )
                        async with send_lock:
                            await ws.send(json.dumps(wire, ensure_ascii=False))
                        logger.info(
                            "[AgentWebSocketServer] keepalive chunk 发送: request_id=%s",
                            request.request_id,
                        )
            except asyncio.CancelledError:
                pass

        # 启动心跳任务
        heartbeat_task = asyncio.create_task(_heartbeat_loop())

        try:
            async for chunk in agent.process_message_stream(request):
                chunk_count += 1
                # 通知心跳任务有真实 chunk 发送，重置心跳计时
                heartbeat_event.set()
                wire = encode_agent_chunk_for_wire(
                    chunk,
                    response_id=request.request_id,
                    sequence=chunk_count - 1,
                )
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
                # 清除 event，让心跳任务重新开始计时
                heartbeat_event.clear()
        finally:
            # 停止心跳任务
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass

        logger.info(
            "[AgentWebSocketServer] 流式响应已发送: request_id=%s 共 %s 个 chunk",
            request.request_id,
            chunk_count,
        )

    async def _handle_session_list(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 session.list 请求：扫描 sessions 目录，返回历史会话基础信息列表."""
        from jiuwenclaw.agentserver.session_metadata import get_session_metadata

        sessions_dir = get_agent_sessions_dir()
        sessions = []

        try:
            if sessions_dir.exists():
                for entry in sorted(sessions_dir.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
                    if not entry.is_dir():
                        continue
                    meta = get_session_metadata(entry.name)
                    if not meta:
                        meta = {
                            "session_id": entry.name,
                            "channel_id": "",
                            "title": "",
                            "message_count": 0,
                            "last_message_at": entry.stat().st_mtime,
                        }
                    sessions.append(meta)
        except Exception as exc:
            logger.warning("[AgentWebSocketServer] 扫描 sessions 目录失败: %s", exc)

        resp = AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"sessions": sessions},
            metadata=request.metadata,
        )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_rename(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 session.rename：与 CLI Gateway 本地回退共用 apply_session_rename。"""
        from jiuwenclaw.agentserver.session_rename import apply_session_rename

        sid = request.session_id or ""
        ch = (request.channel_id or "").strip() or "tui"
        ok, payload, err, code = apply_session_rename(
            request.params,
            sid,
            init_channel_id=ch,
        )
        if ok:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload or {},
                metadata=request.metadata,
            )
        else:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": err or "session.rename failed", "code": code or ""},
                metadata=request.metadata,
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_permissions_config(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 permissions.* E2A 请求（与 Web ``register_method`` 同名 method）。"""
        from jiuwenclaw.agentserver.permissions.config_rpc import dispatch_permissions_config_request

        resp = dispatch_permissions_config_request(request)
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

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

    async def _handle_command_add_dir(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            directory_path = params.get("path")
            remember = params.get("remember", False)
            persist: dict[str, Any]
            if directory_path is None or (
                isinstance(directory_path, str) and not directory_path.strip()
            ):
                persist = {"ok": False, "error": "path is required"}
            else:
                persist = persist_cli_trusted_directory(str(directory_path))
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=bool(persist.get("ok", False)),
                payload={
                    "path": directory_path,
                    "remember": remember,
                    "persist": persist,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.add_dir failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_chrome(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.chrome failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_compact(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            session_id = request.session_id or "default"
            params = request.params or {}

            channel_id = request.channel_id or "default"
            mode = params.get("mode", "agent.plan").split(".")[0]
            agent = await self._agent_manager.get_agent(
                channel_id=channel_id,
                mode=mode,
                project_dir=params.get("project_dir", None)
            )

            if agent is None:
                raise ValueError("Failed to get agent")

            result_data = await agent.compress_context(session_id=session_id)

            result = result_data.get("result")
            stats = result_data.get("stats")

            if result == "compressed" and stats:
                before_tokens = stats.get("raw_total_tokens", 0)
                after_tokens = stats.get("total_tokens", 0)
                if before_tokens > 0:
                    rate = round((before_tokens - after_tokens) / before_tokens * 100, 1)
                else:
                    rate = 0

                await self.send_push({
                    "channel_id": channel_id,
                    "session_id": session_id,
                    "payload": {
                        "event_type": "context.compressed",
                        "rate": rate,
                        "beforeCompressed": before_tokens,
                        "afterCompressed": after_tokens,
                    },
                })

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "result": result,
                    "stats": stats,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.compact failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_diff(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from jiuwenclaw.agentserver.diff_service import get_diff_service

        try:
            session_id = request.session_id or "default"
            diff_service = get_diff_service()
            turns = diff_service.get_turn_diffs(session_id)

            logger.info(
                "[AgentWebSocketServer] command.diff response: session_id=%s turns=%s",
                session_id,
                turns,
            )

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "type": "list",
                    "turns": turns,
                },
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] command.diff failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_model(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            action = params.get("action")

            if action == "add_model":
                target = str(params.get("target", "")).strip()
                logger.info("[command.model] add_model: target=%s", target)
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"type": "model_added", "name": target},
                )

            elif action == "switch_model":
                target = str(params.get("model", "")).strip()
                env_updates = params.get("env_updates", {})
                logger.info(
                    "[command.model] switch_model: target=%s, env_updates=%s",
                    target,
                    {k: (v if k != "API_KEY" else "***") for k, v in env_updates.items()},
                )

                if not env_updates:
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=False,
                        payload={"error": "No env_updates provided"},
                    )
                else:
                    for k, v in env_updates.items():
                        os.environ[k] = v
                    logger.info("[command.model] os.environ 已更新, MODEL_NAME=%s", os.getenv("MODEL_NAME", "unknown"))

                    try:
                        from jiuwenclaw.agentserver.memory.config import clear_config_cache
                        clear_config_cache()
                        logger.info("[command.model] config cache 已清除")
                    except Exception as e:
                        logger.debug("[command.model] clear_config_cache skipped: %s", e)

                    try:
                        await self._agent_manager.reload_agents_config(None, env_updates)
                        logger.info("[command.model] agent config 已重载")
                    except Exception as e:
                        logger.debug("[command.model] reload_agents_config skipped: %s", e)

                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=True,
                        payload={
                            "current": os.getenv("MODEL_NAME", "unknown"),
                            "requested": target,
                            "type": "switched",
                            "applied": True,
                        },
                    )
                    logger.info("[command.model] 切换完成: current=%s", os.getenv("MODEL_NAME", "unknown"))

            else:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"current": os.getenv("MODEL_NAME", "unknown"), "available": ["default-model"]},
                )

        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.model failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    @staticmethod
    def _mask_sensitive_fields(payload: Any) -> Any:
        if isinstance(payload, dict):
            masked: dict[str, Any] = {}
            for key, value in payload.items():
                key_text = str(key).lower()
                value_text = value.lower() if isinstance(value, str) else ""
                key_sensitive = any(
                    token in key_text for token in ("api_key", "token", "authorization", "secret")
                )
                value_sensitive = any(token in value_text for token in ("bearer ", "api-key ", "secret-"))
                if key_sensitive or value_sensitive:
                    masked[key] = "***"
                else:
                    masked[key] = AgentWebSocketServer._mask_sensitive_fields(value)
            return masked
        if isinstance(payload, list):
            return [AgentWebSocketServer._mask_sensitive_fields(item) for item in payload]
        return payload

    @staticmethod
    def _normalize_mcp_payload(
        params: dict[str, Any], current: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        merged = dict(current or {})
        merged.update(params)
        name = str(merged.get("name", "")).strip()
        transport = str(merged.get("transport", "")).strip().lower()
        if not name:
            raise ValueError("MCP server name is required")
        if transport not in {"stdio", "sse"}:
            raise ValueError("transport must be one of stdio|sse")

        payload: dict[str, Any] = {
            "name": name,
            "enabled": bool(merged.get("enabled", True)),
            "transport": transport,
        }
        if transport == "stdio":
            command = str(merged.get("command", "")).strip()
            if not command:
                raise ValueError("stdio transport requires command")
            payload["command"] = command
            args = merged.get("args")
            if isinstance(args, list):
                payload["args"] = [str(item) for item in args]
            cwd = merged.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                payload["cwd"] = cwd.strip()
            env = merged.get("env")
            if isinstance(env, dict):
                payload["env"] = {str(k): str(v) for k, v in env.items()}
        else:
            url = str(merged.get("url", "")).strip()
            if not url:
                raise ValueError(f"{transport} transport requires url")
            payload["url"] = url
            headers = merged.get("headers")
            if isinstance(headers, dict):
                payload["headers"] = {str(k): str(v) for k, v in headers.items()}
            timeout_s = merged.get("timeout_s")
            if isinstance(timeout_s, (int, float)):
                payload["timeout_s"] = int(timeout_s)
        return payload

    def _normalize_mcp_add_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._normalize_mcp_payload(params)

    def _normalize_mcp_update_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name", "")).strip()
        if not name:
            raise ValueError("MCP server name is required")
        current = get_mcp_server_config(name)
        if current is None:
            raise KeyError(f"MCP server '{name}' not found")
        return self._normalize_mcp_payload(params, current=current)

    async def _handle_command_mcp(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            action = str(params.get("action", "list")).strip().lower()

            if action == "list":
                items = [self._mask_sensitive_fields(item) for item in get_mcp_servers()]
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"type": "list", "items": items},
                )
            elif action == "show":
                name = str(params.get("name", "")).strip()
                if name:
                    item = get_mcp_server_config(name)
                    if item is None:
                        raise KeyError(f"MCP server '{name}' not found")
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=True,
                        payload={"type": "detail", "item": self._mask_sensitive_fields(item)},
                    )
                else:
                    enabled_items = [
                        self._mask_sensitive_fields(item)
                        for item in get_mcp_servers()
                        if bool(item.get("enabled", True))
                    ]
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=True,
                        payload={"type": "list", "items": enabled_items},
                    )
            elif action == "add":
                server_payload = self._normalize_mcp_add_payload(params)
                _, created = upsert_mcp_server_in_config(server_payload)
                applied = True
                error_message = ""
                try:
                    await self._agent_manager.reload_agents_config(get_config(), None)
                except Exception as reload_exc:  # noqa: BLE001
                    applied = False
                    error_message = str(reload_exc)
                    logger.warning("[command.mcp] reload after add failed: %s", reload_exc)
                resp_payload: dict[str, Any] = {
                    "type": "added" if created else "updated",
                    "name": server_payload["name"],
                    "applied": applied,
                }
                if error_message:
                    resp_payload["error"] = error_message
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload=resp_payload,
                )
            elif action in {"enable", "disable"}:
                name = str(params.get("name", "")).strip()
                if not name:
                    raise ValueError("MCP server name is required")
                enabled = action == "enable"
                item = set_mcp_server_enabled_in_config(name, enabled)
                applied = True
                error_message = ""
                try:
                    await self._agent_manager.reload_agents_config(get_config(), None)
                except Exception as reload_exc:  # noqa: BLE001
                    applied = False
                    error_message = str(reload_exc)
                    logger.warning("[command.mcp] reload after %s failed: %s", action, reload_exc)
                payload = {
                    "type": "enabled" if enabled else "disabled",
                    "name": name,
                    "applied": applied,
                    "item": self._mask_sensitive_fields(item),
                }
                if error_message:
                    payload["error"] = error_message
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload=payload,
                )
            elif action in {"remove", "delete"}:
                name = str(params.get("name", "")).strip()
                if not name:
                    raise ValueError("MCP server name is required")
                removed = remove_mcp_server_in_config(name)
                applied = True
                error_message = ""
                try:
                    await self._agent_manager.reload_agents_config(get_config(), None)
                except Exception as reload_exc:  # noqa: BLE001
                    applied = False
                    error_message = str(reload_exc)
                    logger.warning("[command.mcp] reload after remove failed: %s", reload_exc)
                payload = {
                    "type": "removed",
                    "name": name,
                    "applied": applied,
                    "item": self._mask_sensitive_fields(removed),
                }
                if error_message:
                    payload["error"] = error_message
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload=payload,
                )
            elif action == "update":
                normalized = self._normalize_mcp_update_payload(params)
                _, _created = upsert_mcp_server_in_config(normalized)
                applied = True
                error_message = ""
                try:
                    await self._agent_manager.reload_agents_config(get_config(), None)
                except Exception as reload_exc:  # noqa: BLE001
                    applied = False
                    error_message = str(reload_exc)
                    logger.warning("[command.mcp] reload after update failed: %s", reload_exc)
                payload = {
                    "type": "updated",
                    "name": normalized["name"],
                    "applied": applied,
                    "item": self._mask_sensitive_fields(normalized),
                }
                if error_message:
                    payload["error"] = error_message
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload=payload,
                )
            else:
                raise ValueError("Unsupported action, must be one of list|show|add|update|enable|disable|remove")
        except KeyError as exc:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "MCP_NOT_FOUND"},
            )
        except ValueError as exc:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "MCP_BAD_REQUEST"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.mcp failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "MCP_INTERNAL"},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_resume(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            query = params.get("query")
            session_id = query if isinstance(query, str) and query.strip() else "sess_mock_resume"
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "session_id": session_id,
                    "query": query if isinstance(query, str) else "",
                    "resumed": True,
                    "preview": "Mock resumed conversation",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.resume failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_session(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            session_id = request.session_id or "sess_mock"
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "session_id": session_id,
                    "remote_url": f"https://example.com/session/{session_id}",
                    "qr_text": f"session:{session_id}",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.session failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

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
            from openjiuwen.harness.tools.browser_move import restart_local_browser_runtime_server

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

            await self._agent_manager.reload_agents_config(config_payload, env_overrides)
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

    async def _handle_extensions_list(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """获取所有 Rail 扩展列表."""
        try:
            manager = get_rail_manager()
            extensions = manager.list_extensions()

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"extensions": extensions},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.list failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_import(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """导入新的 Rail 扩展（文件夹结构）."""
        try:
            params = request.params or {}
            folder_path = params.get("folder_path")

            if not folder_path:
                raise ValueError("缺少 folder_path 参数")

            source_path = Path(folder_path)
            if not source_path.exists() or not source_path.is_dir():
                raise ValueError(f"文件夹不存在或不是目录: {folder_path}")

            manager = get_rail_manager()
            extension = manager.import_extension(folder_path)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=extension,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.import failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_delete(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """删除 Rail 扩展."""
        try:
            params = request.params or {}
            name = params.get("name")

            if not name:
                raise ValueError("缺少 name 参数")

            manager = get_rail_manager()
            manager.delete_extension(name)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"deleted": True, "name": name},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.delete failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_toggle(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """切换 Rail 扩展的启用状态，并触发热更新."""
        try:
            params = request.params or {}
            name = params.get("name")
            enabled = params.get("enabled", False)

            if name is None:
                raise ValueError("缺少 name 参数")
            if enabled is None:
                raise ValueError("缺少 enabled 参数")

            manager = get_rail_manager()

            # 1. 更新配置文件中的启用状态
            extension = manager.toggle_extension(name, enabled)

            # 2. 触发热更新：根据 enabled 状态注册或注销 rail
            await manager.hot_reload_rail(name, enabled)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=extension,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.toggle failed: %s", e)
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
        """获取 default agent 实例（向后兼容）."""
        return self._agent_manager.get_agent_nowait()

    def get_agent_manager(self) -> AgentManager:
        """获取 AgentManager 实例."""
        return self._agent_manager
    
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

    async def _handle_initialize(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理 initialize 方法（非流式）.

        调用 AgentManager.initialize 完成初始化，返回 capabilities。

        Args:
            ws: WebSocket 连接
            request: AgentRequest
            send_lock: 发送锁
        """
        logger.info("[AgentServer] initialize: request_id=%s channel_id=%s", request.request_id, request.channel_id)

        try:
            params = request.params if isinstance(request.params, dict) else {}
            client_capabilities = params.get("clientCapabilities", {})
            logger.info(
                "[AgentServer] initialize clientCapabilities: %s",
                client_capabilities,
            )

            extra_config = {
                "protocol_version": params.get("protocolVersion", "0.1.0"),
                "client_capabilities": client_capabilities,
            }
            if request.channel_id == "acp":
                self._set_ws_acp_client_capabilities(ws, client_capabilities)

            channel_id = request.channel_id or "default"
            capabilities = await self._agent_manager.initialize(
                channel_id=channel_id,
                extra_config=extra_config,
            )
            if capabilities is None:
                capabilities = ACP_DEFAULT_CAPABILITIES.copy()

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=capabilities,
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

            logger.info("[AgentServer] initialize completed: capabilities=%s", capabilities)

        except Exception as e:
            logger.exception("[AgentServer] initialize failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_create(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理 session.create 方法.

        调用 AgentManager.create_session 创建会话，返回 session_id。

        Args:
            ws: WebSocket 连接
            request: AgentRequest
            send_lock: 发送锁
        """
        logger.info("[AgentServer] session.create: request_id=%s", request.request_id)

        try:
            channel_id = request.channel_id or "default"
            params = request.params if isinstance(request.params, dict) else {}
            explicit_session_id = params.get("session_id")
            session_id = await self._agent_manager.create_session(
                channel_id=channel_id,
                session_id=str(explicit_session_id).strip() if isinstance(explicit_session_id, str) else None,
            )

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"sessionId": session_id},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

            logger.info("[AgentServer] session.create completed: session_id=%s", session_id)

        except Exception as e:
            logger.exception("[AgentServer] session.create failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_acp_tool_response(
        self,
        ws: Any,
        request: AgentRequest,
        send_lock: asyncio.Lock,
    ) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        jsonrpc_id = params.get("jsonrpc_id")
        response_payload = params.get("response")
        if not isinstance(response_payload, dict):
            response_payload = {}

        if get_acp_output_manager().complete_jsonrpc_response(jsonrpc_id, response_payload):
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"accepted": True},
            )
        else:
            logger.info(
                "[AgentServer] ignore unknown/late acp tool response: jsonrpc_id=%s request_id=%s",
                jsonrpc_id,
                request.request_id,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "accepted": False,
                    "ignored": True,
                    "reason": "unknown_or_late_response",
                    "jsonrpc_id": jsonrpc_id,
                },
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def handle_acp_tool_response_for_test(
        self,
        ws: Any,
        request: AgentRequest,
        send_lock: asyncio.Lock,
    ) -> None:
        """Public test helper that delegates to ACP tool-response handling."""
        await self._handle_acp_tool_response(ws, request, send_lock)

    async def _handle_harness_packages_get(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle harness.packages.get request - retrieve packages info."""
        from jiuwenclaw.agentserver.deep_agent.auto_harness_service import AutoHarnessService

        try:
            service = AutoHarnessService(rail=None, agent=None)
            payload = service.get_packages_info()
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except Exception as exc:
            logger.exception("[AgentServer] harness.packages.get failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_harness_packages_scan(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle harness.packages.scan request - scan runtime extensions."""
        from jiuwenclaw.agentserver.deep_agent.auto_harness_service import AutoHarnessService

        try:
            service = AutoHarnessService(rail=None, agent=None)
            payload = service.scan_runtime_extensions()
            service.save_packages(payload)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except Exception as exc:
            logger.exception("[AgentServer] harness.packages.scan failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_harness_packages_activate(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle harness.packages.activate request - activate a harness package."""
        from jiuwenclaw.agentserver.deep_agent.auto_harness_service import AutoHarnessService

        params = request.params if isinstance(request.params, dict) else {}
        package_id = params.get("package_id")

        if not package_id:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "missing package_id"},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            # Get or create the agent instance (auto-create if not exists)
            channel_id = request.channel_id or "default"
            agent = await self._agent_manager.get_agent(channel_id=channel_id, mode="agent")
            agent_instance = None
            if agent is not None:
                agent_instance = agent.get_instance()
                logger.info(
                    "[AgentServer] harness.packages.activate: agent_instance type=%s, has_load_harness_config=%s",
                    type(agent_instance).__name__ if agent_instance else None,
                    hasattr(agent_instance, "load_harness_config") if agent_instance else False,
                )

            service = AutoHarnessService(rail=None, agent=agent_instance)
            payload = await service.activate_package(package_id)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except ValueError as exc:
            logger.warning("[AgentServer] harness.packages.activate validation error: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
            )
        except Exception as exc:
            logger.exception("[AgentServer] harness.packages.activate failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_harness_packages_delete(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle harness.packages.delete request - delete a harness package."""
        from jiuwenclaw.agentserver.deep_agent.auto_harness_service import AutoHarnessService

        params = request.params if isinstance(request.params, dict) else {}
        package_id = params.get("package_id")

        if not package_id:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "missing package_id"},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        if package_id == "native":
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "Cannot delete native agent version"},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            service = AutoHarnessService(rail=None, agent=None)
            payload = service.delete_package(package_id)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except ValueError as exc:
            logger.warning("[AgentServer] harness.packages.delete validation error: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
            )
        except Exception as exc:
            logger.exception("[AgentServer] harness.packages.delete failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))
