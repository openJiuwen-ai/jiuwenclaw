# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""WebChannel - WebSocket 通道实现.

提供可扩展的方法处理器注册机制 (`register_method`) 和连接钩子 (`on_connect`)，
使上层应用可以灵活控制每个 req method 的行为，而无需修改通道本身。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

import aiohttp
from websockets.exceptions import ConnectionClosed as WebSocketConnectionClosed

from jiuwenswarm.common.utils import get_agent_workspace_dir
from jiuwenswarm.gateway.channel_manager.base import ChannelMetadata, RobotMessageRouter
from jiuwenswarm.gateway.routing.base_ws_channel import BaseWsChannel
from jiuwenswarm.gateway.routing.keys import AgentRef, RoutingKey
from jiuwenswarm.gateway.routing.session_sharing import RoutingTarget
from jiuwenswarm.common.security.ws_origin import (
    extract_handshake_request,
    forbidden_origin_response,
    get_header_value,
    is_origin_check_enabled,
    is_allowed_browser_origin,
)
from jiuwenswarm.common.schema.message import EventType, Message, Mode, ReqMethod
from jiuwenswarm.common.ws_diagnostics import (
    describe_ws_exception,
    describe_ws_peer,
    format_ws_diagnostics,
)

logger = logging.getLogger(__name__)

_HANDLER_BEFORE_CALLBACK_METHODS = frozenset({ReqMethod.CHAT_SEND.value})

# ── 类型别名 ──────────────────────────────────────────────
# 方法处理器签名: (ws, req_id, params, session_id) -> None
MethodHandler = Callable[..., Awaitable[None]]
# 连接钩子签名: (ws) -> None | Awaitable[None]
ConnectHook = Callable[..., Any]


@dataclass(frozen=True)
class _MethodHandlerInvocation:
    ws: Any
    method: str
    req_id: str
    params: dict[str, Any]
    session_id: str
    handler: MethodHandler


@dataclass
class WebChannelConfig:
    """WebChannel 配置."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 19000
    path: str = "/ws"
    allow_from: list[str] = field(default_factory=list)


class WebChannel(BaseWsChannel):
    """Web 前端 WebSocket 通道.

    核心职责：
    1. 管理 WebSocket 连接生命周期
    2. 解析帧协议 (req / res / event)
    3. 将入站消息发布到 RobotMessageRouter
    4. 将方法路由委托给通过 `register_method` 注册的处理器
    5. V2: 基于 _clients_by_key[RoutingKey] 的 5 维精确路由
    """

    name = "web"
    channel_id = "web"

    def __init__(self, config: WebChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: WebChannelConfig = config
        self._server: Any = None
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._method_handlers: dict[str, MethodHandler] = {}
        self._connect_hooks: list[ConnectHook] = []
        self._disconnect_hooks: list[ConnectHook] = []
        # ws -> set[session_id]: 追踪每个连接上活跃的 session
        self._ws_sessions: dict[int, set[str]] = {}

    # ── 公共属性 ──────────────────────────────────────────

    # channel_id 属性由 name 提供，BaseWsChannel.channel_id 通过 __init_subclass__ 或直接赋值为 "web"

    @property
    def clients(self) -> set[Any]:
        """当前活跃的 WebSocket 客户端集合（从 _clients_by_key 推导，只读副本）."""
        result: set[Any] = set()
        for ws_list in self._clients_by_key.values():
            result.update(ws_list)
        return result

    # ── 扩展注册 API ──────────────────────────────────────

    def register_method(self, method: str, handler: MethodHandler) -> None:
        """注册 req method 处理器.

        handler 签名: ``async def handler(ws, req_id, params, session_id) -> None``
        handler 应通过 `send_response` / `send_event` 向客户端回复。
        """
        self._method_handlers[method] = handler

    def on_connect(self, callback: ConnectHook) -> None:
        """注册连接建立钩子，新客户端接入时依次调用."""
        self._connect_hooks.append(callback)

    def on_disconnect(self, callback: ConnectHook) -> None:
        """注册连接断开钩子，客户端断连时依次调用.

        callback 签名: ``async def callback(ws, session_ids: set[str]) -> None``
        """
        self._disconnect_hooks.append(callback)

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """注册消息接收回调（替代默认的 router.publish_user_messages）。"""
        self._on_message_cb = callback

    def wrap_message_callback(
        self, wrapper: Callable[[Callable[[Message], Any] | None, Message], Any],
    ) -> None:
        """包装现有的消息回调。wrapper 接收 (original_callback, msg) 并返回处理结果。"""
        original = self._on_message_cb

        def wrapped(msg):
            return wrapper(original, msg)

        self._on_message_cb = wrapped

    # ── 帧发送 API（公开给处理器使用）─────────────────────

    async def send_response(
            self,
            ws: Any,
            req_id: str,
            *,
            ok: bool,
            payload: dict[str, Any] | None = None,
            error: str | None = None,
            code: str | None = None,
    ) -> None:
        """向指定客户端发送 ``res`` 帧."""
        frame: dict[str, Any] = {
            "type": "res",
            "id": req_id,
            "ok": ok,
            "payload": payload or {},
        }
        if not ok:
            frame["error"] = error or "request failed"
            if code:
                frame["code"] = code
        try:
            self._enqueue_send(ws, json.dumps(frame, ensure_ascii=False))
        except Exception as e:
            if bool(getattr(ws, "closed", False)):
                logger.debug(
                    "WebChannel send_response skipped on closed websocket: %s",
                    format_ws_diagnostics(
                        {"id": req_id},
                        describe_ws_peer(ws),
                        describe_ws_exception(e),
                    ),
                )
                return
            raise

    async def send_event(
            self,
            ws: Any,
            event: str,
            payload: dict[str, Any],
            *,
            seq: int | None = None,
            stream_id: str | None = None,
    ) -> None:
        """向指定客户端发送 ``event`` 帧."""
        frame: dict[str, Any] = {"type": "event", "event": event, "payload": payload}
        if seq is not None:
            frame["seq"] = seq
        if stream_id is not None:
            frame["stream_id"] = stream_id
        try:
            self._enqueue_send(ws, json.dumps(frame, ensure_ascii=False))
        except Exception as e:
            if bool(getattr(ws, "closed", False)):
                logger.debug(
                    "WebChannel send_event skipped on closed websocket: %s",
                    format_ws_diagnostics(
                        {"event": event, "seq": seq, "stream_id": stream_id},
                        describe_ws_peer(ws),
                        describe_ws_exception(e),
                    ),
                )
                return
            raise

    async def _invoke_method_handler(
            self,
            invocation: _MethodHandlerInvocation,
    ) -> bool:
        try:
            await invocation.handler(
                invocation.ws,
                invocation.req_id,
                invocation.params,
                invocation.session_id,
            )
            return True
        except Exception as e:
            ws_closed = bool(getattr(invocation.ws, "closed", False))
            if ws_closed:
                logger.warning(
                    "WebChannel method handler aborted on closed websocket: %s",
                    format_ws_diagnostics(
                        {
                            "method": invocation.method,
                            "id": invocation.req_id,
                            "session_id": invocation.session_id,
                        },
                        describe_ws_peer(invocation.ws),
                        describe_ws_exception(e),
                    ),
                )
                return False

            logger.error(
                "WebChannel method handler error: %s",
                format_ws_diagnostics(
                    {
                        "method": invocation.method,
                        "id": invocation.req_id,
                        "session_id": invocation.session_id,
                    },
                    describe_ws_peer(invocation.ws),
                    describe_ws_exception(e),
                ),
            )
            try:
                await self.send_response(
                    invocation.ws, invocation.req_id, ok=False,
                    error=f"handler error: {e}", code="INTERNAL_ERROR",
                )
            except Exception as send_err:
                logger.warning(
                    "WebChannel failed to send handler error response ({}): {}",
                    invocation.method, send_err,
                )
            return False

    async def broadcast_event(
            self,
            event: str,
            payload: dict[str, Any],
            *,
            seq: int | None = None,
            stream_id: str | None = None,
    ) -> None:
        """向所有已连接客户端广播 ``event`` 帧."""
        frame: dict[str, Any] = {"type": "event", "event": event, "payload": payload}
        if seq is not None:
            frame["seq"] = seq
        if stream_id is not None:
            frame["stream_id"] = stream_id
        await self._broadcast(frame)

    async def _download_file(self, url: str) -> bytes | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
                    else:
                        logger.warning("WebChannel 文件下载失败: %s, 状态码: %s", url, response.status)
                        return None
        except Exception as e:
            logger.warning("WebChannel 文件下载异常: %s, 错误: %s", url, e)
            return None

    async def _process_files(self, params: dict[str, Any]) -> dict[str, Any]:
        files = params.get("files")
        if not files or not isinstance(files, list):
            return params

        downloaded_files = []
        workspace_dir = str(get_agent_workspace_dir())

        for file_info in files:
            if not isinstance(file_info, dict):
                downloaded_files.append(file_info)
                continue

            file_url = file_info.get("url") or file_info.get("uri") or ""
            file_name = file_info.get("name") or file_info.get("filename") or "unknown_file"

            if file_url:
                file_content = await self._download_file(file_url)
                if file_content:
                    try:
                        os.makedirs(workspace_dir, exist_ok=True)
                        file_path = os.path.join(workspace_dir, file_name)
                        with open(file_path, "wb") as f:
                            f.write(file_content)
                        file_info["path"] = file_path
                    except Exception as e:
                        logger.warning("WebChannel 文件保存失败: %s", e)

            downloaded_files.append(file_info)

        params["files"] = downloaded_files
        return params

    # ── Channel 生命周期 ──────────────────────────────────

    async def start(self) -> None:
        """启动 WebSocket 服务并监听客户端连接."""
        if self._running:
            logger.warning("WebChannel 已在运行")
            return
        if not self.config.enabled:
            logger.warning("WebChannel 未启用（enabled=False）")
            return

        try:
            from websockets.legacy.server import serve as ws_serve
        except Exception:  # pragma: no cover
            import websockets

            ws_serve = websockets.serve

        ws_max_size = 8 * 2**20  # 8 MB — matches AgentServer link

        self._server = await ws_serve(
            self._connection_handler,
            self.config.host,
            self.config.port,
            process_request=self._process_request,
            ping_interval=20,
            ping_timeout=60,
            max_size=ws_max_size,
        )
        self._running = True
        logger.info(
            f"WebChannel 已启动: ws://{self.config.host}:{self.config.port}{self.config.path}"
        )
        await self._server.wait_closed()

    async def stop(self) -> None:
        """停止 WebSocket 服务并清理连接."""
        self._running = False

        all_clients = list(self.clients)
        close_tasks = [client.close(code=1001, reason="server shutdown") for client in all_clients]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        self._clients_by_key.clear()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
        # 兜底清理未走正常断连路径的 writer 协程（正常断连已由 unregister_ws 清理）
        await self._shutdown_all_writers()
        logger.info("WebChannel 已停止")

    async def connect(self) -> None:
        """兼容方法：调用 start."""
        await self.start()

    async def disconnect(self) -> None:
        """兼容方法：调用 stop."""
        await self.stop()

    async def _process_request(self, *args: Any) -> Any:
        """在握手阶段执行 Origin 校验，兼容 legacy/new websockets APIs。"""
        path, request_headers = extract_handshake_request(args)
        origin = get_header_value(request_headers, "Origin")
        enable_origin_check = is_origin_check_enabled()
        if not enable_origin_check:
            logger.info(
                "WebChannel 握手检查 path=%s origin=%s enable_origin_check=%s allowed=%s",
                path,
                origin,
                enable_origin_check,
                True,
            )
            return None

        allowed = is_allowed_browser_origin(origin)
        logger.info(
            "WebChannel 握手检查 path=%s origin=%s enable_origin_check=%s allowed=%s",
            path,
            origin,
            enable_origin_check,
            allowed,
        )
        if allowed:
            return None

        logger.warning(
            "WebChannel 握手拒绝 path=%s origin=%s reason=origin_not_allowed",
            path,
            origin,
        )
        return forbidden_origin_response(args)

    async def send(
        self,
        msg: Message,
        *,
        routing_target: RoutingTarget | None = None,
    ) -> None:
        """向客户端发送消息。

        V2: 当 routing_target 非空时，按其 routing_keys 精确路由（_clients_by_key）。
        否则回退到全量广播（向后兼容）。
        """
        _pl = getattr(msg, "payload", None) or {}
        _et = _pl.get("event_type", "") if isinstance(_pl, dict) else ""
        _has_fanout = bool((getattr(msg, "metadata", None) or {}).get("fan_out_targets"))
        logger.debug(
            "[WebChannel] send() called: id=%s event_type=%s payload_et=%s has_fanout=%s"
            " has_routing_target=%s client_count=%s",
            getattr(msg, "id", ""), getattr(msg, "event_type", None), _et,
            _has_fanout, routing_target is not None, len(self.clients),
        )
        # ── 心跳 relay：临时 session_id（heartbeat_{ts}_{suffix}）不匹配任何前端连接，
        # 按常规 session_id 路由会被当作"无连接"丢弃。心跳状态是全局的（非会话级），
        # 前端 setHeartbeatStatus 也是全局 store，因此直接广播给所有 web 客户端。
        # 与 wechat 等 IM 渠道在 send() 中对 HEARTBEAT_RELAY 的专属分支对齐。
        if msg.event_type == EventType.HEARTBEAT_RELAY:
            frame = self._serialize_frame(msg, None)  # 已是 json 字符串
            clients = self.clients
            for w in clients:
                self._enqueue_send(w, frame)
            logger.debug(
                "[WebChannel] heartbeat.relay broadcast to %d client(s) id=%s",
                len(clients), getattr(msg, "id", ""),
            )
            return

        # ── 定时任务推 web：原设计绑定 job.session_id，但关闭 tab/换设备后旧会话再无连接，
        # 按 session_id 路由会被丢弃。cron 推送（占位 + 结果）带 payload.cron 标记，普通对话
        # chat.final 不带，以此为识别条件广播给所有 web 客户端。前端 _push_to_targets 已对 web
        # 置空 session_id，shouldHandleSessionEvent 放行，消息进当前活跃会话流（含 placeholder 替换）。
        if (
            msg.event_type == EventType.CHAT_FINAL
            and isinstance(msg.payload, dict)
            and isinstance(msg.payload.get("cron"), dict)
        ):
            frame = self._serialize_frame(msg, None)  # 已是 json 字符串
            clients = self.clients
            for w in clients:
                self._enqueue_send(w, frame)
            logger.debug(
                "[WebChannel] cron push broadcast to %d client(s) id=%s run_id=%s",
                len(clients), getattr(msg, "id", ""),
                (msg.payload.get("cron") or {}).get("run_id", ""),
            )
            return

        if msg.type == "res":
            if isinstance(msg.payload, dict):
                res_payload = {**msg.payload}
            elif msg.payload is None:
                res_payload = {}
            else:
                res_payload = {"content": str(msg.payload)}

            frame: dict[str, Any] = {
                "type": "res",
                "id": msg.id,
                "ok": bool(msg.ok),
                "payload": res_payload,
            }
            if not msg.ok:
                error_text = res_payload.get("error")
                if isinstance(error_text, str) and error_text:
                    frame["error"] = error_text
                code_text = res_payload.get("code")
                if isinstance(code_text, str) and code_text:
                    frame["code"] = code_text

            ws_set: set[Any] = set()
            metadata = msg.metadata if isinstance(msg.metadata, dict) else {}
            request_ws_id = str(metadata.get("ws_id") or "").strip()
            if request_ws_id:
                ws = self._ws_by_id.get(request_ws_id)
                if ws is not None and not getattr(ws, "closed", False):
                    ws_set.add(ws)

            if not ws_set and routing_target is not None:
                delivery = routing_target.delivery
                if delivery is not None:
                    ws_id = getattr(delivery, "ws_id", "")
                    if ws_id:
                        ws = self._ws_by_id.get(ws_id)
                        if ws is not None and not getattr(ws, "closed", False):
                            ws_set.add(ws)
                if not ws_set:
                    for rk in routing_target.routing_keys:
                        ws_list = self._clients_by_key.get(rk) or []
                        for w in ws_list:
                            if not getattr(w, "closed", False):
                                ws_set.add(w)

            if not ws_set and msg.session_id:
                for rk, ws_list in self._clients_by_key.items():
                    if rk.session_id == msg.session_id:
                        for w in ws_list:
                            if not getattr(w, "closed", False):
                                ws_set.add(w)

            if not ws_set:
                logger.debug(
                    "[WebChannel] response route miss: ws_id=%s session_id=%s id=%s",
                    request_ws_id,
                    msg.session_id,
                    getattr(msg, "id", ""),
                )
                return
            await self._broadcast_to(frame, ws_set)
            return

        # ── V2 精确路由 ──
        if routing_target is not None:
            routing_keys = routing_target.routing_keys
            member_names = list(routing_target.member_names)

            # ── 优先：按 delivery.ws_id 物理寻址 ──
            ws_set: set[Any] = set()
            delivery = routing_target.delivery
            if delivery is not None:
                ws_id = getattr(delivery, "ws_id", "")
                if ws_id:
                    ws = self._ws_by_id.get(ws_id)
                    if ws is not None and not getattr(ws, "closed", False):
                        ws_set.add(ws)

            # ── 兜底：按 routing_keys 5 维逻辑查 _clients_by_key ──
            if not ws_set and routing_keys:
                for rk in routing_keys:
                    ws_list = self._clients_by_key.get(rk) or []
                    for w in ws_list:
                        if not getattr(w, "closed", False):
                            ws_set.add(w)
            if ws_set:
                frame_data = self._serialize_frame(msg, routing_target, member_names=member_names)
                for w in ws_set:
                    self._enqueue_send(w, frame_data)
                return
            # V2 精确路由未命中 —— 回退到 session_id 路由
            logger.debug(
                "[WebChannel] V2 routing miss: looked up %d routing_keys + ws_id=%s,"
                " ws_set empty — falling back to session_id=%s",
                len(routing_keys), getattr(delivery, "ws_id", "") if delivery else "",
                getattr(msg, "session_id", ""),
            )

        # ── 旧路径：按 session_id 精确路由（不再全量广播）──
        if not msg.session_id:
            logger.warning(
                "[WebChannel] msg has no session_id, cannot route -- "
                "dropping msg id=%s to avoid cross-session broadcast",
                getattr(msg, "id", ""),
            )
            return
        ws_set: set[Any] = set()
        for rk, ws_list in self._clients_by_key.items():
            if rk.session_id == msg.session_id:
                for w in ws_list:
                    if not getattr(w, "closed", False):
                        ws_set.add(w)
        if not ws_set:
            logger.debug(
                "[WebChannel] session_id=%s has no connected ws, dropping msg id=%s",
                msg.session_id, getattr(msg, "id", ""),
            )
            return
        all_clients = ws_set

        # 确定事件名称
        event_name = "chat.final"
        if msg.event_type is not None:
            event_name = msg.event_type.value
        elif isinstance(msg.payload, dict):
            payload_event_type = msg.payload.get("event_type")
            if isinstance(payload_event_type, str) and payload_event_type.strip():
                event_name = payload_event_type.strip()

        # 根据事件类型构造 payload
        payload: dict[str, Any] = {}

        if isinstance(msg.payload, dict):
            # 对于需要传递完整结构化数据的事件类型
            if event_name in ("connection.ack", "todo.updated", "chat.tool_call", "chat.tool_result",
                              "chat.processing_status", "chat.interrupt_result", "chat.evolution_status",
                              "chat.error", "heartbeat.relay",
                              "context.usage", "context.compression_state",
                              "chat.ask_user_question", "chat.subtask_update",
                              "chat.symphony_status", "chat.notice",
                              "history.message",
                              "chat.session_result", "chat.usage_metadata",
                              "chat.usage_summary", "chat.file",
                              "chat.retract", "security.alert",
                              "proactive_recommendation") \
                                or event_name.startswith("team.") \
                                or event_name.startswith("harness."):
                # 传递完整 payload，保留所有字段
                payload = {**msg.payload}
                # 确保包含 session_id
                if "session_id" not in payload and msg.session_id:
                    payload["session_id"] = msg.session_id
                if event_name.startswith("chat.") and "request_id" not in payload and msg.id:
                    payload["request_id"] = msg.id
            else:
                # 对于纯文本消息（chat.delta, chat.final, chat.error 等），提取 content
                content = str(msg.payload.get("content", "") or "")
                if not content and not getattr(msg, "ok", True) and msg.payload.get("error"):
                    content = str(msg.payload.get("error", ""))
                payload = {
                    "session_id": msg.session_id,
                    "content": content,
                }
                # teammate 消息：保留 role 和 member_name 供前端区分成员
                for _key in ("role", "member_name", "member_action", "source_channel", "user_id", "display_name"):
                    _val = msg.payload.get(_key)
                    if _val is not None:
                        payload[_key] = _val
                # 定时任务推送：附带 cron 元数据，供前端识别并替换占位消息（避免误写入流式气泡）
                if event_name == "chat.final":
                    cron_extra = msg.payload.get("cron")
                    if isinstance(cron_extra, dict):
                        payload["cron"] = cron_extra
                    # 保留 source 字段，供前端识别消息来源（如主动推荐）
                    source = msg.payload.get("source")
                    if source:
                        payload["source"] = source
                    # 保留 proactive_type，供前端选对卡片样式（技能推荐/任务提醒/探索发现）
                    ptype = msg.payload.get("proactive_type")
                    if ptype:
                        payload["proactive_type"] = ptype
                    if source == "proactive_recommendation":
                        logger.info(
                            "[WebChannel] proactive push frame: source=%s proactive_type=%s "
                            "content_len=%d payload_keys=%s",
                            source, ptype, len(str(payload.get("content", ""))), list(payload.keys()),
                        )
        else:
            # payload 不是 dict，尝试从 params 提取
            content = str((msg.params or {}).get("content", "") or "")
            payload = {
                "session_id": msg.session_id,
                "content": content,
            }

        # ── V2: 诊断日志 ──
        if routing_target is not None:
            logger.info(
                "[WebChannel] frame: id=%s event=%s intent=%s",
                getattr(msg, "id", ""), event_name, routing_target.intent,
            )
        if getattr(msg, "agent_ref", None):
            payload["agent_ref"] = msg.agent_ref if isinstance(msg.agent_ref, dict) else {
                "mode": getattr(msg.agent_ref, "mode", ""),
                "id": getattr(msg.agent_ref, "id", ""),
            }

        frame_data: dict[str, Any] = {
            "type": "event",
            "event": event_name,
            "payload": payload,
        }
        await self._broadcast_to(frame_data, all_clients)

        # interrupt_result 根据 intent 决定 is_processing 状态
        if event_name == "chat.interrupt_result":
            intent = payload.get("intent", "cancel") if isinstance(payload, dict) else "cancel"
            is_processing = intent in ("pause", "supplement", "resume")
            await self._broadcast_to({
                "type": "event",
                "event": "chat.processing_status",
                "payload": {"session_id": msg.session_id, "is_processing": is_processing},
            }, all_clients)

    def get_metadata(self) -> ChannelMetadata:
        """获取 Channel 元数据."""
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="websocket",
            extra={"host": self.config.host, "port": self.config.port, "path": self.config.path},
        )

    # ── 内部实现 ──────────────────────────────────────────

    async def _connection_handler(self, ws: Any, path: str | None = None) -> None:
        raw_path = path if path is not None else getattr(ws, "path", "")
        parsed = urlparse(raw_path)
        request_path = parsed.path or raw_path
        if request_path != self.config.path:
            await ws.close(code=1008, reason=f"unsupported path: {request_path}")
            return

        query = parse_qs(parsed.query)
        remote = getattr(ws, "remote_address", None)
        logger.info(f"WebChannel 新连接: remote={remote} query={query}")

        # ── V2: 从 query 提取身份字段，构造默认 RoutingKey ──
        # session_id 和 agent_id 可能在首条消息中更新
        _flat_query = {k: (v[0] if v else "") for k, v in query.items()}
        _user_id = _flat_query.get("user_id", str(remote or "unknown"))
        _app_id = _flat_query.get("app_id", "default")
        _mode = _flat_query.get("mode", "agent")
        _agent_id = _flat_query.get("agent_id", "default")
        _initial_sid = _flat_query.get("session_id", self._make_session_id())
        _initial_rk = RoutingKey(
            user_id=_user_id,
            channel_id=self.channel_id,
            app_id=_app_id,
            agent_ref=AgentRef(mode=_mode, id=_agent_id),
            session_id=_initial_sid,
        )
        await self.register_ws(ws, _initial_rk)
        # 将握手阶段占位 session_id 挂到 ws 上，供 _on_connect 等连接级钩子复用，
        # 确保 connection.ack 与 ws 在 _clients_by_key 中的注册 key 一致，
        # 否则 send() 按 session_id 反查会落空导致 ACK 丢弃。
        # 注：此 sid 仅为传输层占位，首条 chat.send 携带真实 session_id 时会 re-register 覆盖。
        setattr(ws, "_jiuwen_initial_sid", _initial_sid)

        # 触发连接钩子（如发送 connection.ack）
        for hook in self._connect_hooks:
            try:
                result = hook(ws)
                if inspect.isawaitable(result):
                    await result
            except Exception as e:  # pragma: no cover
                logger.warning(
                    "WebChannel on_connect hook error: %s",
                    format_ws_diagnostics(
                        {"remote": remote, "path": request_path},
                        describe_ws_peer(ws),
                        describe_ws_exception(e),
                    ),
                )

        try:
            async for raw in ws:
                await self._handle_raw_message(ws, raw, query)
        except WebSocketConnectionClosed as e:  # pragma: no cover - 连接生命周期容错
            logger.info(
                "WebChannel 连接关闭: %s",
                format_ws_diagnostics(
                    {"remote": remote, "path": request_path},
                    describe_ws_peer(ws),
                    describe_ws_exception(e),
                ),
            )
        except Exception as e:  # pragma: no cover - 连接生命周期容错
            logger.warning(
                "WebChannel 连接异常: %s",
                format_ws_diagnostics(
                    {"remote": remote, "path": request_path},
                    describe_ws_peer(ws),
                    describe_ws_exception(e),
                ),
            )
        finally:
            await self.unregister_ws(ws)

            # 触发断开钩子
            for hook in self._disconnect_hooks:
                try:
                    result = hook(ws)
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:
                    logger.warning("WebChannel on_disconnect hook error: %s", e)

            logger.info(
                "WebChannel 连接清理完成: %s",
                format_ws_diagnostics(
                    {"remote": remote, "path": request_path, "clients": len(self._clients_by_key)},
                    describe_ws_peer(ws),
                ),
            )
            # 取出该 ws 关联的 session_ids，清理映射
            ws_id = id(ws)
            disconnected_sessions = self._ws_sessions.pop(ws_id, set())
            logger.info(
                "WebChannel 连接关闭: remote=%s sessions=%s",
                remote,
                disconnected_sessions or "none",
            )
            # 触发断连钩子，传入 session_ids
            for hook in self._disconnect_hooks:
                try:
                    result = hook(ws, disconnected_sessions)
                    if inspect.isawaitable(result):
                        await result
                except Exception as e:  # pragma: no cover
                    logger.warning("WebChannel on_disconnect hook error: %s", e)

    async def _handle_raw_message(self, ws: Any, raw: str, query: dict[str, list[str]]) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            await self.send_response(ws, "", ok=False, error="invalid json", code="BAD_REQUEST")
            return

        if not isinstance(data, dict):
            await self.send_response(ws, "", ok=False, error="invalid request", code="BAD_REQUEST")
            return

        req_type = data.get("type")
        req_id = data.get("id")
        method = data.get("method")
        params = data.get("params")

        if req_type != "req" or not isinstance(req_id, str) or not isinstance(method, str):
            await self.send_response(
                ws,
                req_id if isinstance(req_id, str) else "",
                ok=False,
                error="invalid request",
                code="BAD_REQUEST",
            )
            return
        if not isinstance(params, dict):
            params = {}

        # ── V2: session_id 解析 ──
        # 请求自带 session_id（如 chat.send）→ 用它更新 ws 路由注册。
        # 请求未带 session_id（如 memory.compute 心跳、updater.check、config.get
        # 等 ws 层 keepalive / 拉取请求）→ 这类请求与 session 无关，
        # 仅合成一个临时 id 供后续 Message 构造使用，但【不】参与 register_ws，
        # 保留 ws 上一次的真实 RoutingKey，避免把 ws 从其所属 team session 摘除。
        _explicit_session_id = params.get("session_id")
        has_explicit_session = (
            isinstance(_explicit_session_id, str) and bool(_explicit_session_id)
        )
        session_id = _explicit_session_id if has_explicit_session else self._make_session_id()

        # 追踪 ws → session_id 映射，用于断连时清理
        ws_id = id(ws)
        sessions = self._ws_sessions.get(ws_id)
        if sessions is None:
            sessions = set()
            self._ws_sessions[ws_id] = sessions
        sessions.add(session_id)

        params = await self._process_files(params)

        # ── V2: 用实际的 session_id / mode / agent_id 更新 ws 注册 ──
        _flat_query = {k: (v[0] if v else "") for k, v in query.items()}
        _mode = params.get("mode", "agent")
        _agent_id = params.get("agent_id", "default")
        _app_id = _flat_query.get("app_id", "default")
        if has_explicit_session:
            _rk = RoutingKey(
                user_id=_flat_query.get(
                    "user_id", str(getattr(ws, "remote_address", "unknown"))
                ),
                channel_id=self.channel_id,
                app_id=_app_id,
                agent_ref=AgentRef(mode=_mode, id=_agent_id),
                session_id=session_id,
            )
            await self.register_ws(ws, _rk)
        # else: ws 层心跳 / 拉取请求，不更新路由注册，沿用 ws 已有的 RoutingKey。

        user_message = Message(
            id=req_id,
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=self._parse_req_method(method),
            mode=self._parse_mode(params.get("mode")),
            app_id=_app_id,
            agent_ref={"mode": _mode, "id": _agent_id},
            metadata={
                "query": query,
                "method": method,
                # V2: 注入 ws_id 供 MessageHandler 构造 WebDeliveryTarget(ws_id=真值)。
                "ws_id": getattr(ws, "_jiuwen_ws_id", ""),
            },
        )

        # 发布到 route 或回调
        handler = self._method_handlers.get(method)
        handler_already_called = False
        if method in _HANDLER_BEFORE_CALLBACK_METHODS and handler is not None:
            handler_already_called = await self._invoke_method_handler(
                _MethodHandlerInvocation(
                    ws, method, req_id, params, session_id, handler,
                ),
            )
            if not handler_already_called:
                return

        handled_by_callback = False
        if self._on_message_cb is not None:
            result = self._on_message_cb(user_message)
            if inspect.isawaitable(result):
                result = await result
            handled_by_callback = bool(result)
        else:
            await self.bus.publish_user_messages(user_message)

        if handled_by_callback:
            return
        if handler_already_called:
            return

        # 路由到已注册的方法处理器
        if handler is not None:
            await self._invoke_method_handler(
                _MethodHandlerInvocation(
                    ws, method, req_id, params, session_id, handler,
                ),
            )
        else:
            await self.send_response(
                ws, req_id, ok=False,
                error=f"unknown method: {method}", code="METHOD_NOT_FOUND",
            )

    async def _broadcast_to(self, frame: dict[str, Any], clients: set[Any]) -> None:
        """向指定 clients 集合广播帧（走 per-ws writer，非阻塞入队）."""
        data = json.dumps(frame, ensure_ascii=False)
        if not clients:
            return
        for client in clients:
            self._enqueue_send(client, data)

    # ── BaseWsChannel 抽象方法 ──

    def _serialize_frame(
        self,
        msg: Any,
        routing_target: RoutingTarget | None = None,
        *,
        member_names: list[str] | None = None,
    ) -> str:
        """将 Message 序列化为 Web 前端 JSON 帧."""
        event_name = "chat.final"
        if getattr(msg, "event_type", None) is not None:
            event_name = msg.event_type.value
        elif isinstance(getattr(msg, "payload", None), dict):
            et = msg.payload.get("event_type")
            if isinstance(et, str) and et.strip():
                event_name = et.strip()

        payload: dict[str, Any] = {}
        if isinstance(msg.payload, dict):
            payload = {**msg.payload}
            if "session_id" not in payload and getattr(msg, "session_id", None):
                payload["session_id"] = msg.session_id
        elif getattr(msg, "payload", None) is not None:
            payload = {"session_id": getattr(msg, "session_id", None), "content": str(msg.payload)}
        else:
            payload = {"session_id": getattr(msg, "session_id", None), "content": ""}

        agent_ref = getattr(msg, "agent_ref", None)
        if agent_ref:
            payload["agent_ref"] = agent_ref if isinstance(agent_ref, dict) else {
                "mode": getattr(agent_ref, "mode", ""),
                "id": getattr(agent_ref, "id", ""),
            }

        frame: dict[str, Any] = {
            "type": "event",
            "event": event_name,
            "payload": payload,
        }
        return json.dumps(frame, ensure_ascii=False)

    @staticmethod
    def _parse_req_method(method: str) -> ReqMethod | None:
        for item in ReqMethod:
            if item.value == method:
                return item
        return None

    @staticmethod
    def _parse_mode(raw_mode: Any) -> Mode:
        return Mode.from_raw(raw_mode, default=Mode.AGENT_PLAN)

    @staticmethod
    def _make_session_id() -> str:
        # 与前端 generateSessionId 保持一致：毫秒时间戳(16进制) + 6位随机16进制
        ts = format(int(time.time() * 1000), "x")
        suffix = secrets.token_hex(3)
        return f"sess_{ts}_{suffix}"
