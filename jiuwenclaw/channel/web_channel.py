# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""WebChannel - WebSocket 通道实现.

提供可扩展的方法处理器注册机制 (`register_method`) 和连接钩子 (`on_connect`)，
使上层应用可以灵活控制每个 req method 的行为，而无需修改通道本身。
"""

from __future__ import annotations

import asyncio
import inspect
import json
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable
from urllib.parse import parse_qs, urlparse

from jiuwenclaw.utils import logger
from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.schema.message import Message, Mode, ReqMethod

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
        await ws.send(json.dumps(frame, ensure_ascii=False))

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
        await ws.send(json.dumps(frame, ensure_ascii=False))

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

        self._server = await ws_serve(
            self._connection_handler,
            self.config.host,
            self.config.port,
            ping_interval=20,
            ping_timeout=20,
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

        # 根据事件类型构造 payload
        payload = {}

        if isinstance(msg.payload, dict):
            # 对于需要传递完整结构化数据的事件类型
            if event_name in ("connection.ack", "todo.updated", "chat.tool_call", "chat.tool_result",
                             "chat.processing_status", "chat.interrupt_result", "chat.error", "heartbeat.relay",
                             "context.compressed", "chat.ask_user_question", "chat.subtask_update"):
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
                logger.warning("WebChannel on_connect hook error: {}", e)

        try:
            async for raw in ws:
                await self._handle_raw_message(ws, raw, query)
        except Exception as e:  # pragma: no cover - 连接生命周期容错
            logger.warning("WebChannel 连接异常: {}", e)
        finally:
            self._clients.discard(ws)
            logger.info(f"WebChannel 连接关闭: remote={remote}")

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
                logger.error("WebChannel method handler error ({}): {}", method, e)
                await self.send_response(
                    ws, req_id, ok=False,
                    error=f"handler error: {e}", code="INTERNAL_ERROR",
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
        if isinstance(raw_mode, str):
            normalized = raw_mode.strip().lower()
            if normalized:
                try:
                    return Mode(normalized)
                except ValueError:
                    pass
        return Mode.PLAN

    @staticmethod
    def _make_session_id() -> str:
        # 与前端 generateSessionId 保持一致：毫秒时间戳(16进制) + 6位随机16进制
        ts = format(int(time.time() * 1000), "x")
        suffix = secrets.token_hex(3)
        return f"sess_{ts}_{suffix}"
