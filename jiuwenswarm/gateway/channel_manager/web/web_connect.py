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
from jiuwenswarm.gateway.channel_manager.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenswarm.common.security.ws_origin import (
    extract_handshake_request,
    forbidden_origin_response,
    get_header_value,
    is_origin_check_enabled,
    is_allowed_browser_origin,
)
from jiuwenswarm.common.schema.message import Message, Mode, ReqMethod
from jiuwenswarm.common.ws_diagnostics import (
    describe_ws_exception,
    describe_ws_peer,
    format_ws_diagnostics,
)

logger = logging.getLogger(__name__)

# ── 类型别名 ──────────────────────────────────────────────
# 方法处理器签名: (ws, req_id, params, session_id) -> None
MethodHandler = Callable[..., Awaitable[None]]
# 连接钩子签名: (ws) -> None | Awaitable[None]
ConnectHook = Callable[..., Any]


@dataclass
class WebChannelConfig:
    """WebChannel 配置."""

    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 19000
    path: str = "/ws"
    allow_from: list[str] = field(default_factory=list)


class WebChannel(BaseChannel):
    """Web 前端 WebSocket 通道.

    核心职责：
    1. 管理 WebSocket 连接生命周期
    2. 解析帧协议 (req / res / event)
    3. 将入站消息发布到 RobotMessageRouter
    4. 将方法路由委托给通过 `register_method` 注册的处理器
    """

    name = "web"

    def __init__(self, config: WebChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: WebChannelConfig = config
        self._server: Any = None
        self._clients: set[Any] = set()
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._method_handlers: dict[str, MethodHandler] = {}
        self._connect_hooks: list[ConnectHook] = []

    # ── 公共属性 ──────────────────────────────────────────

    @property
    def channel_id(self) -> str:
        """返回唯一 Channel 标识."""
        return self.name

    @property
    def clients(self) -> set[Any]:
        """当前活跃的 WebSocket 客户端集合（只读副本）."""
        return set(self._clients)

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

    def on_message(self, callback: Callable[[Message], None]) -> None:
        """注册消息接收回调（替代默认的 router.publish_user_messages）。"""
        self._on_message_cb = callback

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
            await ws.send(json.dumps(frame, ensure_ascii=False))
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
            await ws.send(json.dumps(frame, ensure_ascii=False))
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
            ping_timeout=20,
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

        close_tasks = [client.close(code=1001, reason="server shutdown") for client in list(self._clients)]
        if close_tasks:
            await asyncio.gather(*close_tasks, return_exceptions=True)
        self._clients.clear()

        if self._server is not None:
            self._server.close()
            await self._server.wait_closed()
            self._server = None
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

    async def send(self, msg: Message) -> None:
        """向客户端发送消息（默认封装为 event 帧广播）."""
        if not self._clients:
            return

        # 响应帧：优先按 res 语义透传，避免误封装为 chat.final
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
            await self._broadcast(frame)
            return

        # 确定事件名称
        event_name = "chat.final"
        if msg.event_type is not None:
            event_name = msg.event_type.value
        elif isinstance(msg.payload, dict):
            payload_event_type = msg.payload.get("event_type")
            if isinstance(payload_event_type, str) and payload_event_type.strip():
                event_name = payload_event_type.strip()

        # 根据事件类型构造 payload
        payload = {}

        if isinstance(msg.payload, dict):
            # 对于需要传递完整结构化数据的事件类型
            if event_name in ("connection.ack", "todo.updated", "chat.tool_call", "chat.tool_result",
                              "chat.processing_status", "chat.interrupt_result", "chat.evolution_status",
                              "chat.error", "heartbeat.relay",
                              "context.usage", "context.compression_state",
                              "chat.ask_user_question", "chat.subtask_update",
                              "chat.symphony_status",
                              "history.message",
                              "chat.session_result", "chat.usage_metadata",
                              "chat.usage_summary", "chat.file",
                              "chat.retract", "security.alert") \
                                or event_name.startswith("team.") \
                                or event_name.startswith("harness."):
                # 传递完整 payload，保留所有字段
                payload = {**msg.payload}
                # 确保包含 session_id
                if "session_id" not in payload and msg.session_id:
                    payload["session_id"] = msg.session_id
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
                for _key in ("role", "member_name"):
                    _val = msg.payload.get(_key)
                    if _val is not None:
                        payload[_key] = _val
                # 定时任务推送：附带 cron 元数据，供前端识别并替换占位消息（避免误写入流式气泡）
                if event_name == "chat.final":
                    cron_extra = msg.payload.get("cron")
                    if isinstance(cron_extra, dict):
                        payload["cron"] = cron_extra
        else:
            # payload 不是 dict，尝试从 params 提取
            content = str((msg.params or {}).get("content", "") or "")
            payload = {
                "session_id": msg.session_id,
                "content": content,
            }

        frame = {
            "type": "event",
            "event": event_name,
            "payload": payload,
        }
        await self._broadcast(frame)

        # interrupt_result 根据 intent 决定 is_processing 状态
        if event_name == "chat.interrupt_result":
            intent = payload.get("intent", "cancel") if isinstance(payload, dict) else "cancel"
            is_processing = intent in ("pause", "supplement", "resume")
            await self._broadcast({
                "type": "event",
                "event": "chat.processing_status",
                "payload": {"session_id": msg.session_id, "is_processing": is_processing},
            })

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
        self._clients.add(ws)
        logger.info(f"WebChannel 新连接: remote={remote} query={query}")

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
            self._clients.discard(ws)
            logger.info(
                "WebChannel 连接清理完成: %s",
                format_ws_diagnostics(
                    {"remote": remote, "path": request_path, "clients": len(self._clients)},
                    describe_ws_peer(ws),
                ),
            )

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

        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = self._make_session_id()

        params = await self._process_files(params)

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
            metadata={"query": query, "method": method},
        )

        # 发布到 route 或回调
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

        # 路由到已注册的方法处理器
        handler = self._method_handlers.get(method)
        if handler is not None:
            try:
                await handler(ws, req_id, params, session_id)
            except Exception as e:
                # 客户端断开（如服务关闭时 code=1001）不再尝试回包，避免二次异常噪音。
                ws_closed = bool(getattr(ws, "closed", False))
                if ws_closed:
                    logger.warning(
                        "WebChannel method handler aborted on closed websocket: %s",
                        format_ws_diagnostics(
                            {"method": method, "id": req_id, "session_id": session_id},
                            describe_ws_peer(ws),
                            describe_ws_exception(e),
                        ),
                    )
                    return

                logger.error(
                    "WebChannel method handler error: %s",
                    format_ws_diagnostics(
                        {"method": method, "id": req_id, "session_id": session_id},
                        describe_ws_peer(ws),
                        describe_ws_exception(e),
                    ),
                )
                try:
                    await self.send_response(
                        ws, req_id, ok=False,
                        error=f"handler error: {e}", code="INTERNAL_ERROR",
                    )
                except Exception as send_err:
                    logger.warning(
                        "WebChannel failed to send handler error response ({}): {}",
                        method, send_err,
                    )
        else:
            await self.send_response(
                ws, req_id, ok=False,
                error=f"unknown method: {method}", code="METHOD_NOT_FOUND",
            )

    async def _broadcast(self, frame: dict[str, Any]) -> None:
        data = json.dumps(frame, ensure_ascii=False)
        if not self._clients:
            return
        await asyncio.gather(*[client.send(data) for client in list(self._clients)], return_exceptions=True)

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
