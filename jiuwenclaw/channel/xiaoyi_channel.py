# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""XiaoyiChannel - 华为小艺 A2A 协议客户端."""

from __future__ import annotations

import asyncio
import base64
import hmac
import hashlib
import inspect
import json
import time
import ssl
from dataclasses import dataclass
from typing import Any, Callable
from urllib.parse import urlparse

from jiuwenclaw.utils import logger
from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.schema.message import EventType, Message, ReqMethod


@dataclass
class XiaoyiChannelConfig:
    """小艺通道配置（客户端模式）."""

    enabled: bool = False
    ak: str = ""
    sk: str = ""
    agent_id: str = ""
    ws_url1: str = ""
    ws_url2: str = ""
    enable_streaming: bool = True


def _generate_signature(sk: str, timestamp: str) -> str:
    """生成 HMAC-SHA256 签名（Base64 编码）."""
    h = hmac.new(
        sk.encode("utf-8"),
        timestamp.encode("utf-8"),
        hashlib.sha256,
    )
    return base64.b64encode(h.digest()).decode("utf-8")


def _generate_auth_headers(ak: str, sk: str, agent_id: str) -> dict[str, str]:
    """生成鉴权 Header."""
    timestamp = str(int(time.time() * 1000))
    signature = _generate_signature(sk, timestamp)
    return {
        "x-access-key": ak,
        "x-sign": signature,
        "x-ts": timestamp,
        "x-agent-id": agent_id,
    }


class XiaoyiChannel(BaseChannel):
    """小艺通道：作为客户端连接到小艺服务器，实现 A2A 协议."""

    name = "xiaoyi"

    def __init__(self, config: XiaoyiChannelConfig, router: RobotMessageRouter):
        super().__init__(config, router)
        self.config: XiaoyiChannelConfig = config
        self._ws_connections: dict[str, Any] = {}  # Dual channel connections
        self._running = False
        self._heartbeat_tasks: dict[str, asyncio.Task] = {}  # Heartbeat tasks for each channel
        self._connect_tasks: dict[str, asyncio.Task] = {}  # Connection tasks for each channel
        self._session_task_map: dict[str, str] = {}
        self._session_heartbeat_tasks: dict[str, asyncio.Task] = {}  # Response heartbeat tasks for each session
        self._on_message_cb: Callable[[Message], Any] | None = None

    @property
    def channel_id(self) -> str:
        return self.name

    @property
    def clients(self) -> set[Any]:
        return set()

    def on_message(self, callback: Callable[[Message], None]) -> None:
        self._on_message_cb = callback

    async def start(self) -> None:
        if self._running:
            logger.warning("XiaoyiChannel 已在运行")
            return
        if not self.config.enabled:
            logger.warning("XiaoyiChannel 未启用（enabled=False）")
            return
        if not self.config.ak or not self.config.sk or not self.config.agent_id:
            logger.error("XiaoyiChannel 未配置 ak/sk/agent_id")
            return

        self._running = True
        # Start dual channel connections
        for url_key, url in [("ws_url1", self.config.ws_url1), ("ws_url2", self.config.ws_url2)]:
            if url:
                self._connect_tasks[url_key] = asyncio.create_task(self._reconnect_loop(url_key, url))
        logger.info("XiaoyiChannel 已启动（客户端模式，双通道）")

    async def stop(self) -> None:
        self._running = False
        # Cancel all heartbeat tasks
        for url_key in list(self._heartbeat_tasks.keys()):
            if self._heartbeat_tasks[url_key]:
                self._heartbeat_tasks[url_key].cancel()
                self._heartbeat_tasks[url_key] = None
        # Cancel all connection tasks
        for url_key in list(self._connect_tasks.keys()):
            if self._connect_tasks[url_key]:
                self._connect_tasks[url_key].cancel()
                self._connect_tasks[url_key] = None
        # Cancel all session heartbeat tasks
        for session_id in list(self._session_heartbeat_tasks.keys()):
            if self._session_heartbeat_tasks[session_id]:
                self._session_heartbeat_tasks[session_id].cancel()
                self._session_heartbeat_tasks[session_id] = None
        # Close all websocket connections
        for url_key, ws in list(self._ws_connections.items()):
            if ws:
                try:
                    await ws.close()
                except Exception as e:
                    logger.warning(f"关闭 WebSocket 连接失败 ({url_key}): {e}")
                self._ws_connections[url_key] = None
        self._heartbeat_tasks.clear()
        self._connect_tasks.clear()
        self._session_heartbeat_tasks.clear()
        self._ws_connections.clear()
        logger.info("XiaoyiChannel 已停止")

    def _extract_platform_receive_info(self, msg: Message) -> tuple[str, str]:
        """
        从消息中提取小艺平台会话 ID 与任务 ID。
        优先使用 metadata（避免 \new_session 覆盖 session_id 后无法回发），否则回退到 session_id 与 _session_task_map。
        """
        meta = getattr(msg, "metadata", None) or {}
        platform_session_id = (meta.get("xiaoyi_session_id") or "").strip()
        platform_task_id = (meta.get("xiaoyi_task_id") or "").strip()
        if platform_session_id or platform_task_id:
            return (
                platform_session_id or (msg.session_id or ""),
                platform_task_id or platform_session_id,
            )
        session_id = msg.session_id or ""
        task_id = self._session_task_map.get(session_id, session_id)
        return session_id, task_id

    async def send(self, msg: Message) -> None:
        """发送消息到小艺服务端（A2A 格式，双通道发送）."""
        if not self._ws_connections:
            return
        session_id, task_id = self._extract_platform_receive_info(msg)
        logger.info(f"XiaoyiChannel 发送消息: {msg}")

        content = ""
        if isinstance(msg.payload, dict):
            content = msg.payload.get("content", "")
            if isinstance(content, dict):
                content = content.get("output", str(content))
            content = str(content)
        elif msg.payload:
            content = str(msg.payload)

        # Send to all active connections
        for url_key, ws in self._ws_connections.items():
            if ws:
                try:
                    await self._send_text_response(session_id, task_id, content, url_key, is_final=True)
                except Exception as e:
                    logger.warning(f"XiaoyiChannel 发送消息失败 ({url_key}): {e}")

        if session_id:
            await self._stop_session_heartbeat(session_id)

    def get_metadata(self) -> ChannelMetadata:
        return ChannelMetadata(
            channel_id=self.channel_id,
            source="websocket",
            extra={
                "mode": "client",
                "ws_url1": self.config.ws_url1,
                "ws_url2": self.config.ws_url2,
                "agent_id": self.config.agent_id,
            },
        )

    async def _reconnect_loop(self, url_key: str, url: str) -> None:
        """自动重连循环（双通道）."""
        while self._running:
            try:
                await self._connect(url_key, url)
            except Exception as e:
                logger.warning(f"XiaoyiChannel 连接失败 ({url}): {e}")
                await asyncio.sleep(5)

    async def _connect(self, url_key: str, url: str) -> None:
        """连接到小艺服务器（双通道）."""
        import websockets

        headers = _generate_auth_headers(self.config.ak, self.config.sk, self.config.agent_id)
        parsed = urlparse(url)
        is_ip = bool(parsed.hostname and parsed.hostname.replace(".", "").isdigit())

        ssl_context = ssl.create_default_context()
        if is_ip:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(url, additional_headers=headers, ssl=ssl_context) as ws:
            self._ws_connections[url_key] = ws
            logger.info(f"XiaoyiChannel 已连接 {url_key}: {url}")

            # 发送初始化消息（必须在 heartbeat 之前）
            await self._send_init_message(url_key)

            # 启动心跳
            self._heartbeat_tasks[url_key] = asyncio.create_task(self._heartbeat_loop(url_key))

            try:
                async for raw in ws:
                    await self._handle_raw_message(raw)
            except Exception as e:
                logger.warning(f"XiaoyiChannel 连接异常 ({url_key}): {e}")
            finally:
                if self._heartbeat_tasks.get(url_key):
                    self._heartbeat_tasks[url_key].cancel()
                    self._heartbeat_tasks[url_key] = None
                self._ws_connections[url_key] = None
                logger.info(f"XiaoyiChannel 连接关闭 {url_key} : {url}")
                await asyncio.sleep(8)
    async def _send_init_message(self, url_key: str) -> None:
        """发送初始化消息 (clawd_bot_init) 到指定通道."""
        ws = self._ws_connections.get(url_key)
        if not ws:
            return
        init_message = {
            "msgType": "clawd_bot_init",
            "agentId": self.config.agent_id,
        }
        try:
            await ws.send(json.dumps(init_message))
            logger.info(f"XiaoyiChannel 已发送初始化消息 ({url_key})")
        except Exception as e:
            logger.warning(f"XiaoyiChannel 发送初始化消息失败 ({url_key}): {e}")
            raise

    async def _heartbeat_loop(self, url_key: str) -> None:
        """应用层心跳循环（20秒间隔）."""
        while self._running and self._ws_connections.get(url_key):
            try:
                heartbeat = {"msgType": "heartbeat", "agentId": self.config.agent_id}
                ws = self._ws_connections.get(url_key)
                if ws:
                    await ws.send(json.dumps(heartbeat))
            except Exception as e:
                logger.warning(f"XiaoyiChannel 心跳发送失败 ({url_key}): {e}")
                break
            await asyncio.sleep(20)

    async def _handle_raw_message(self, raw: str | bytes) -> None:
        """处理接收到的原始消息，转换为 JiuwenClaw 内部格式."""
        try:
            if isinstance(raw, bytes):
                raw = raw.decode("utf-8")
            message = json.loads(raw)
        except (json.JSONDecodeError, UnicodeDecodeError):
            logger.warning("XiaoyiChannel JSON 解析失败")
            return

        msg_type = message.get("msgType")
        if msg_type == "heartbeat":
            return

        method = message.get("method")
        if method == "message/stream":
            await self._handle_message_stream(message)
        elif method == "clearContext":
            await self._handle_clear_context(message)
        elif method == "tasks/cancel":
            await self._handle_tasks_cancel(message)
        else:
            logger.warning(f"XiaoyiChannel 未知方法: {method}")

    async def _handle_message_stream(self, message: dict[str, Any]) -> None:
        """处理 message/stream 消息，转换为 JiuwenClaw Message."""
        session_id = message.get("sessionId") or message.get("params", {}).get("sessionId", "")
        task_id = message.get("params", {}).get("id", "")
        user_message = message.get("params", {}).get("message", {})
        parts = user_message.get("parts", [])

        text = ""
        for part in parts:
            if part.get("kind") == "text":
                text = part.get("text", "")
                break

        self._session_task_map[session_id] = task_id

        # 将最近一次可回发的小艺身份写入 config.yaml，供 cron 推送时使用
        try:
            from jiuwenclaw.config import update_channel_in_config

            update_channel_in_config(
                "xiaoyi",
                {
                    "last_session_id": session_id or "",
                    "last_task_id": task_id or "",
                },
            )
        except Exception:
            pass

        # 平台身份写入 metadata，供回发时使用（与 session_id 解耦，\new_session 后仍可正确回发）
        user_message = Message(
            id=message.get("id", ""),
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params={"query": text, "task_id": task_id},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            metadata={
                "method": "message/stream",
                "xiaoyi_session_id": session_id,
                "xiaoyi_task_id": task_id,
            },
        )

        handled = False
        if self._on_message_cb is not None:
            result = self._on_message_cb(user_message)
            if inspect.isawaitable(result):
                result = await result
            handled = bool(result)

        if not handled:
            await self.bus.route_user_message(user_message)

        # Start session heartbeat to prevent xiaoyi client timeout
        if session_id:
            await self._start_session_heartbeat(session_id, task_id)

    async def _start_session_heartbeat(self, session_id: str, task_id: str) -> None:
        """启动会话心跳任务，每隔5秒发送空消息直到final消息发出."""
        await self._stop_session_heartbeat(session_id)

        async def heartbeat_loop():
            try:
                while self._running:
                    await asyncio.sleep(5)
                    # Send empty heartbeat message (non-final)
                    for url_key, ws in self._ws_connections.items():
                        if ws:
                            try:
                                await self._send_text_response(session_id, task_id, "", url_key, is_final=False)
                            except Exception as e:
                                logger.warning(f"XiaoyiChannel 发送心跳消息面败 ({url_key}): {e}")
            except asyncio.CancelledError:
                logger.info(f"XiaoyiChannel 会话心跳已停止: {session_id}")
            except Exception as e:
                logger.warning(f"XiaoyiChannel 会话心跳异常 ({session_id}): {e}")

        self._session_heartbeat_tasks[session_id] = asyncio.create_task(heartbeat_loop())
        logger.info(f"XiaoyiChannel 会话心跳已启动: {session_id}")

    async def _stop_session_heartbeat(self, session_id: str) -> None:
        """停止会话心跳任务."""
        if session_id in self._session_heartbeat_tasks:
            task = self._session_heartbeat_tasks[session_id]
            if task and not task.done():
                task.cancel()
                try:
                    await task
                except asyncio.CancelledError:
                    pass
            self._session_heartbeat_tasks.pop(session_id, None)
            logger.info(f"XiaoyiChannel 会话心跳已停止: {session_id}")

    async def _handle_clear_context(self, message: dict[str, Any]) -> None:
        """处理清空上下文请求."""
        session_id = message.get("sessionId", "")
        logger.info(f"XiaoyiChannel 清空上下文: {session_id}")

        self._session_task_map.pop(session_id, None)

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"status": {"state": "cleared"}},
        }
        # Send response to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, session_id, response, url_key)

    async def _handle_tasks_cancel(self, message: dict[str, Any]) -> None:
        """处理取消任务请求."""
        session_id = message.get("sessionId", "")
        task_id = message.get("params", {}).get("id") or message.get("taskId", "")
        logger.info(f"XiaoyiChannel 取消任务: {session_id} {task_id}")

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"id": message.get("id", ""), "status": {"state": "canceled"}},
        }
        # Send response to all active connections
        for url_key in list(self._ws_connections.keys()):
            await self._send_agent_response(session_id, task_id, response, url_key)

    async def _send_text_response(self, session_id: str, task_id: str, text: str, url_key: str, is_final: bool = True) -> None:
        """发送文本响应（A2A 格式）到指定通道."""
        response = {
            "jsonrpc": "2.0",
            "id": f"msg_{int(time.time() * 1000)}",
            "result": {
                "taskId": task_id,
                "kind": "artifact-update",
                "append": False,
                "lastChunk": is_final,
                "final": is_final,
                "artifact": {
                    "artifactId": f"artifact_{int(time.time() * 1000)}",
                    "parts": [{"kind": "text", "text": text}],
                },
            },
        }
        await self._send_agent_response(session_id, task_id, response, url_key)

    async def _send_agent_response(self, session_id: str, task_id: str, response: dict[str, Any], url_key: str) -> None:
        """发送 agent_response 包装的消息（A2A 格式）到指定通道."""
        ws = self._ws_connections.get(url_key)
        if not ws:
            return
        wrapper = {
            "msgType": "agent_response",
            "agentId": self.config.agent_id,
            "sessionId": session_id,
            "taskId": task_id,
            "msgDetail": json.dumps(response),
        }
        try:
            await ws.send(json.dumps(wrapper))
        except Exception as e:
            logger.warning(f"XiaoyiChannel 发送响应失败 ({url_key}): {e}")
