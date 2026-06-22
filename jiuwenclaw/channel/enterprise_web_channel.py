# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""EnterpriseWebChannel - Gateway WS client to Web Pod /gateway (standard req/res/event)."""

from __future__ import annotations

import asyncio
import contextlib
import inspect
import json
import logging
import os
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Awaitable, Callable, Protocol

import aiohttp

from jiuwenclaw.agentserver.session_metadata import get_resolved_project_dir
from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.channel.enterprise_web_uplink_config import get_enterprise_web_uplink_client_settings
from jiuwenclaw.gateway.local_rpc_hooks import LocalRpcHookDispatcher
from jiuwenclaw.request_ext import attach_to_metadata as _ext_attach
from jiuwenclaw.request_ext import build_ext_from_source as _ext_build
from jiuwenclaw.schema.message import Message, Mode, ReqMethod
from jiuwenclaw.utils import get_agent_sessions_dir

logger = logging.getLogger(__name__)

MethodHandler = Callable[..., Awaitable[None]]
ConnectHook = Callable[..., Any]

_STRUCTURED_EVENTS = frozenset({
    "connection.ack",
    "todo.updated",
    "chat.tool_call",
    "chat.tool_result",
    "chat.processing_status",
    "chat.interrupt_result",
    "chat.evolution_status",
    "chat.tool_calls.delta",
    "chat.error",
    "heartbeat.relay",
    "context.compressed",
    "context.usage",
    "chat.ask_user_question",
    "chat.invocation_paused",
    "chat.subtask_update",
    "history.message",
    "chat.session_result",
    "chat.usage_metadata",
    "chat.usage_summary",
})


class _UplinkPeer(Protocol):
    async def send_uplink_raw(self, data: str) -> None:
        ...


class _UplinkSocket:
    """Virtual WebSocket: handler send_response/send_event → single Gateway uplink."""

    def __init__(self, channel: _UplinkPeer) -> None:
        self._channel = channel
        self.closed = False

    async def send(self, data: str) -> None:
        if self.closed:
            raise ConnectionError("enterprise uplink socket is closed")
        await self._channel.send_uplink_raw(data)


@dataclass
class EnterpriseWebChannelConfig:
    """Configuration for EnterpriseWebChannel uplink to Web Pod."""

    enabled: bool = False
    gateway_url: str = ""
    gateway_path: str = "/gateway"
    host: str = "127.0.0.1"
    port: int = 19000
    allow_from: list[str] = field(default_factory=list)


def _build_outbound_frames(msg: Message) -> list[dict[str, Any]]:
    """Encode robot Message as one or more req/res/event JSON frames."""
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
        return [frame]

    event_name = "chat.final"
    if msg.event_type is not None:
        event_name = msg.event_type.value
    elif isinstance(msg.payload, dict):
        payload_event_type = msg.payload.get("event_type")
        if isinstance(payload_event_type, str) and payload_event_type.strip():
            event_name = payload_event_type.strip()

    if isinstance(msg.payload, dict):
        if (
            event_name in _STRUCTURED_EVENTS
            or event_name.startswith("skilldev.")
            or event_name.startswith("team.")
        ):
            payload = {**msg.payload}
            if "session_id" not in payload and msg.session_id:
                payload["session_id"] = msg.session_id
        else:
            content = str(msg.payload.get("content", "") or "")
            if not content and not getattr(msg, "ok", True) and msg.payload.get("error"):
                content = str(msg.payload.get("error", ""))
            payload = {
                "session_id": msg.session_id,
                "content": content,
            }
            if event_name == "chat.final":
                cron_extra = msg.payload.get("cron")
                if isinstance(cron_extra, dict):
                    payload["cron"] = cron_extra
    else:
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
    if msg.id:
        frame["request_id"] = msg.id
    frames = [frame]

    if event_name == "chat.interrupt_result":
        intent = payload.get("intent", "cancel") if isinstance(payload, dict) else "cancel"
        is_processing = intent in ("pause", "supplement", "resume")
        extra_processing: dict[str, Any] = {
            "type": "event",
            "event": "chat.processing_status",
            "payload": {"session_id": msg.session_id, "is_processing": is_processing},
        }
        if msg.id:
            extra_processing["request_id"] = msg.id
        frames.append(extra_processing)

    return frames


class EnterpriseWebChannel(BaseChannel):
    """Gateway-side web channel: single WS uplink to Web Pod /gateway (no browser WS server)."""

    name = "web"

    def __init__(self, config: EnterpriseWebChannelConfig, router: RobotMessageRouter) -> None:
        super().__init__(config, router)
        self.config: EnterpriseWebChannelConfig = config
        self._method_handlers: dict[str, MethodHandler] = {}
        self._connect_hooks: list[ConnectHook] = []
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._local_rpc_hooks = LocalRpcHookDispatcher()
        self._uplink_ws: Any | None = None
        self._uplink_shim = _UplinkSocket(self)
        self._stop_event = asyncio.Event()
        self._uplink_task: asyncio.Task[None] | None = None
        self._clients: set[Any] = set()

    @property
    def channel_id(self) -> str:
        return self.name

    @property
    def clients(self) -> set[Any]:
        return set(self._clients)

    def register_method(self, method: str, handler: MethodHandler) -> None:
        self._method_handlers[method] = handler

    def on_connect(self, callback: ConnectHook) -> None:
        self._connect_hooks.append(callback)

    def on_message(self, callback: Callable[[Message], Any]) -> None:
        self._on_message_cb = callback

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
        ok, payload, error, code = await self._local_rpc_hooks.after_response(
            ws,
            req_id,
            ok=ok,
            payload=payload,
            error=error,
            code=code,
        )
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
        except Exception as exc:
            if bool(getattr(ws, "closed", False)):
                logger.debug(
                    "EnterpriseWebChannel send_response skipped on closed uplink: id=%s err=%s",
                    req_id,
                    exc,
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
        frame: dict[str, Any] = {"type": "event", "event": event, "payload": payload}
        if seq is not None:
            frame["seq"] = seq
        if stream_id is not None:
            frame["stream_id"] = stream_id
        try:
            await ws.send(json.dumps(frame, ensure_ascii=False))
        except Exception as exc:
            if bool(getattr(ws, "closed", False)):
                logger.debug(
                    "EnterpriseWebChannel send_event skipped on closed uplink: event=%s err=%s",
                    event,
                    exc,
                )
                return
            raise

    def _resolve_gateway_url(self) -> str:
        explicit = (self.config.gateway_url or "").strip()
        if explicit:
            return explicit
        path = self.config.gateway_path or "/gateway"
        if not path.startswith("/"):
            path = f"/{path}"
        return f"ws://{self.config.host}:{self.config.port}{path}"

    def is_uplink_connect_running(self) -> bool:
        """Whether the uplink connect/reconnect background task is active."""
        task = self._uplink_task
        return task is not None and not task.done()

    def start_uplink_connect(self) -> None:
        """Start uplink connect/reconnect loop (idempotent).

        Used in active-standby mode: only PRIMARY should call this after election.
        """
        if self._uplink_task is not None and not self._uplink_task.done():
            return
        if not self.config.enabled:
            logger.warning("EnterpriseWebChannel 未启用（enabled=False），跳过 uplink 连接")
            return
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            logger.warning("EnterpriseWebChannel 无运行中的 event loop，跳过 uplink 连接")
            return
        self._uplink_task = loop.create_task(
            self._run_uplink_connect(),
            name="enterprise-web-uplink",
        )
        logger.info(
            "EnterpriseWebChannel uplink connect task started (target %s)",
            self._resolve_gateway_url(),
        )

    async def _run_uplink_connect(self) -> None:
        try:
            await self.start()
        finally:
            current = asyncio.current_task()
            if self._uplink_task is current:
                self._uplink_task = None

    async def stop_uplink_connect(self) -> None:
        """Stop uplink and cancel reconnect loop (idempotent).

        Used in active-standby mode: STANDBY calls this to release Web Pod /gateway.
        """
        await self.stop()
        task = self._uplink_task
        if task is not None:
            if not task.done():
                task.cancel()
                with contextlib.suppress(asyncio.CancelledError):
                    await task
            self._uplink_task = None
        logger.info("EnterpriseWebChannel uplink connect task stopped")

    async def start(self) -> None:
        if self._running:
            logger.warning("EnterpriseWebChannel 已在运行")
            return
        if not self.config.enabled:
            logger.warning("EnterpriseWebChannel 未启用（enabled=False）")
            return

        self._running = True
        self._stop_event.clear()
        url = self._resolve_gateway_url()
        logger.info("EnterpriseWebChannel 启动 uplink: %s", url)

        attempt = 0
        while self._running:
            attempt += 1
            try:
                await self._connect_and_serve(url)
                attempt = 0
            except asyncio.CancelledError:
                self._running = False
                break
            except Exception as exc:
                logger.warning("EnterpriseWebChannel uplink 断开或失败: %s", exc)
            finally:
                self._uplink_shim.closed = True
                self._clients.discard(self._uplink_shim)
                self._local_rpc_hooks.clear_ws(self._uplink_shim)

            if not self._running:
                break
            uplink = get_enterprise_web_uplink_client_settings()
            delay = min(
                uplink.connect_base_delay_sec * (2 ** min(attempt - 1, uplink.reconnect_backoff_cap)),
                uplink.reconnect_max_delay_sec,
            )
            logger.info("EnterpriseWebChannel %.2fs 后重连 uplink", delay)
            try:
                await asyncio.wait_for(self._stop_event.wait(), timeout=delay)
                break
            except asyncio.TimeoutError:
                continue

        logger.info("EnterpriseWebChannel 已停止")

    async def stop(self) -> None:
        self._running = False
        self._stop_event.set()
        self._uplink_shim.closed = True
        await self._close_uplink()
        self._clients.discard(self._uplink_shim)
        self._local_rpc_hooks.clear()
        logger.info("EnterpriseWebChannel 已停止")

    async def send(self, msg: Message) -> None:
        if self._uplink_ws is None:
            return
        for frame in _build_outbound_frames(msg):
            await self._send_uplink_frame(frame)

    def get_metadata(self) -> ChannelMetadata:
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="enterprise_web_uplink",
            extra={
                "mode": "enterprise_web_uplink",
                "gateway_url": self._resolve_gateway_url(),
            },
        )

    async def _connect_and_serve(self, url: str) -> None:
        try:
            from websockets.legacy.client import connect as ws_connect
        except Exception:  # pragma: no cover
            from websockets import connect as ws_connect

        uplink = get_enterprise_web_uplink_client_settings()
        last_exc: BaseException | None = None
        for connect_attempt in range(1, uplink.connect_max_attempts + 1):
            try:
                self._uplink_ws = await ws_connect(
                    url,
                    ping_interval=uplink.ping_interval,
                    ping_timeout=uplink.ping_timeout,
                    open_timeout=uplink.open_timeout,
                )
                break
            except BaseException as exc:
                last_exc = exc
                self._uplink_ws = None
                if connect_attempt >= uplink.connect_max_attempts:
                    raise RuntimeError(
                        f"EnterpriseWebChannel uplink connect failed after "
                        f"{uplink.connect_max_attempts} attempts: {url}"
                    ) from last_exc
                delay = min(
                    uplink.connect_base_delay_sec * (2 ** (connect_attempt - 1)),
                    uplink.reconnect_max_delay_sec,
                )
                logger.warning(
                    "EnterpriseWebChannel connect attempt %d/%d failed (%s); retry in %.2fs",
                    connect_attempt,
                    uplink.connect_max_attempts,
                    exc,
                    delay,
                )
                await asyncio.sleep(delay)
        else:
            raise RuntimeError(f"EnterpriseWebChannel uplink connect failed: {url}") from last_exc

        self._uplink_shim.closed = False
        self._clients.add(self._uplink_shim)
        logger.info("EnterpriseWebChannel uplink 已连接: %s", url)

        try:
            async for raw in self._uplink_ws:
                if not self._running:
                    break
                if isinstance(raw, bytes):
                    raw = raw.decode("utf-8", errors="replace")
                await self._handle_uplink_inbound(raw)
        finally:
            await self._close_uplink()

    async def _close_uplink(self) -> None:
        ws = self._uplink_ws
        self._uplink_ws = None
        if ws is not None:
            try:
                await ws.close()
            except Exception:
                logger.debug("EnterpriseWebChannel uplink close ignored", exc_info=True)

    async def send_uplink_raw(self, data: str) -> None:
        uplink = self._uplink_ws
        if uplink is None or self._uplink_shim.closed:
            raise ConnectionError("enterprise uplink not connected")
        await uplink.send(data)

    async def _send_uplink_frame(self, frame: dict[str, Any]) -> None:
        if self._uplink_ws is None:
            return
        try:
            await self.send_uplink_raw(json.dumps(frame, ensure_ascii=False))
        except Exception as exc:
            logger.warning("EnterpriseWebChannel uplink send failed: %s", exc)

    async def _handle_uplink_inbound(self, raw: str) -> None:
        try:
            data = json.loads(raw)
        except json.JSONDecodeError:
            logger.warning("EnterpriseWebChannel 忽略无效 uplink JSON: %s", raw[:200])
            return
        if not isinstance(data, dict):
            return
        if data.get("type") != "req":
            return
        await self._handle_raw_message(self._uplink_shim, raw, {})

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

        params = await self._process_files(params, session_id)

        stream_methods = ("skilldev.start", "skilldev.respond")
        is_stream = method in stream_methods

        # request_ext 透传：Web Pod 把浏览器握手 query 随帧带上（_browser_query），
        # 据此抽取 ext —— 与 web_channel 的"从 query 抽 ext"逻辑一致；
        # forward_headers 仍在 Gateway 进程按 JIUWENCLAW_REQUEST_EXT_FORWARD_HEADERS 读取。
        browser_query = data.get("_browser_query")
        if not isinstance(browser_query, dict):
            browser_query = query
        ext = _ext_build(browser_query)

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
            is_stream=is_stream,
            metadata=_ext_attach({"query": browser_query, "method": method}, ext=ext),
        )

        if self._on_message_cb is not None:
            result = self._on_message_cb(user_message)
            if inspect.isawaitable(result):
                result = await result
            if bool(result):
                return
        else:
            await self.bus.route_user_message(user_message)

        handler = self._method_handlers.get(method)
        if handler is None:
            await self.send_response(
                ws,
                req_id,
                ok=False,
                error=f"unknown method: {method}",
                code="METHOD_NOT_FOUND",
            )
            return

        try:
            params = await self._local_rpc_hooks.before_request(
                ws,
                request_id=req_id,
                channel_id=self.channel_id,
                session_id=session_id,
                method=method,
                params=params,
                source="web",
                route=self.config.gateway_path,
                metadata={"query": query},
            )
            await handler(ws, req_id, params, session_id)
            self._local_rpc_hooks.discard(ws, req_id)
        except Exception as exc:
            if bool(getattr(ws, "closed", False)):
                self._local_rpc_hooks.discard(ws, req_id)
                logger.warning(
                    "EnterpriseWebChannel method handler aborted on closed uplink (%s): %s",
                    method,
                    exc,
                )
                return
            logger.error("EnterpriseWebChannel method handler error (%s): %s", method, exc)
            try:
                await self.send_response(
                    ws,
                    req_id,
                    ok=False,
                    error=f"handler error: {exc}",
                    code="INTERNAL_ERROR",
                )
            except Exception as send_err:
                logger.warning(
                    "EnterpriseWebChannel failed to send handler error response (%s): %s",
                    method,
                    send_err,
                )

    async def _download_file(self, url: str) -> bytes | None:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as response:
                    if response.status == 200:
                        return await response.read()
                    logger.warning(
                        "EnterpriseWebChannel 文件下载失败: %s, 状态码: %s",
                        url,
                        response.status,
                    )
                    return None
        except Exception as exc:
            logger.warning("EnterpriseWebChannel 文件下载异常: %s, 错误: %s", url, exc)
            return None

    async def _process_files(self, params: dict[str, Any], session_id: str) -> dict[str, Any]:
        files = params.get("files")
        if not files or not isinstance(files, list):
            return params

        downloaded_files = []
        workspace_dir = get_resolved_project_dir(session_id, str(get_agent_sessions_dir()))

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
                        with open(file_path, "wb") as handle:
                            handle.write(file_content)
                        file_info["path"] = file_path
                    except Exception as exc:
                        logger.warning("EnterpriseWebChannel 文件保存失败: %s", exc)

            downloaded_files.append(file_info)

        params["files"] = downloaded_files
        return params

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
        ts = format(int(time.time() * 1000), "x")
        suffix = secrets.token_hex(3)
        return f"sess_{ts}_{suffix}"
