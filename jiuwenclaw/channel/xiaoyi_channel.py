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

from loguru import logger

from jiuwenclaw.channel.base import BaseChannel, ChannelMetadata, RobotMessageRouter
from jiuwenclaw.schema.message import Message, ReqMethod


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
        self._ws: Any = None
        self._running = False
        self._heartbeat_task: asyncio.Task | None = None
        self._reconnect_task: asyncio.Task | None = None
        self._session_task_map: dict[str, str] = {}
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
        self._reconnect_task = asyncio.create_task(self._reconnect_loop())
        logger.info("XiaoyiChannel 已启动（客户端模式）")

    async def stop(self) -> None:
        self._running = False
        if self._heartbeat_task:
            self._heartbeat_task.cancel()
            self._heartbeat_task = None
        if self._reconnect_task:
            self._reconnect_task.cancel()
            self._reconnect_task = None
        if self._ws:
            await self._ws.close()
            self._ws = None
        logger.info("XiaoyiChannel 已停止")

    async def send(self, msg: Message) -> None:
        """发送消息到小艺服务端（A2A 格式）."""
        if not self._ws:
            return
        session_id = msg.session_id or ""
        task_id = self._session_task_map.get(session_id, session_id)
        logger.info("XiaoyiChannel 发送消息: {}", msg)

        content = ""
        if isinstance(msg.payload, dict):
            content = msg.payload.get("content", "")
            if isinstance(content, dict):
                content = content.get("output", str(content))
            content = str(content)
        elif msg.payload:
            content = str(msg.payload)

        await self._send_text_response(session_id, task_id, content)

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

    async def _reconnect_loop(self) -> None:
        """自动重连循环."""
        urls = [self.config.ws_url1, self.config.ws_url2]
        url_index = 0
        while self._running:
            url = urls[url_index]
            try:
                await self._connect(url)
                url_index = 0
            except Exception as e:
                logger.warning("XiaoyiChannel 连接失败 ({}): {}", url, e)
                url_index = (url_index + 1) % len(urls)
                await asyncio.sleep(5)

    async def _connect(self, url: str) -> None:
        """连接到小艺服务器."""
        import websockets

        headers = _generate_auth_headers(self.config.ak, self.config.sk, self.config.agent_id)
        parsed = urlparse(url)
        is_ip = bool(parsed.hostname and parsed.hostname.replace(".", "").isdigit())

        ssl_context = ssl.create_default_context()
        if is_ip:
            ssl_context.check_hostname = False
            ssl_context.verify_mode = ssl.CERT_NONE

        async with websockets.connect(url, additional_headers=headers, ssl=ssl_context) as ws:
            self._ws = ws
            logger.info("XiaoyiChannel 已连接: {}", url)

            # 发送初始化消息（必须在 heartbeat 之前）
            await self._send_init_message()

            # 启动心跳
            self._heartbeat_task = asyncio.create_task(self._heartbeat_loop())

            try:
                async for raw in ws:
                    await self._handle_raw_message(raw)
            except Exception as e:
                logger.warning("XiaoyiChannel 连接异常: {}", e)
            finally:
                if self._heartbeat_task:
                    self._heartbeat_task.cancel()
                    self._heartbeat_task = None
                self._ws = None
                logger.info("XiaoyiChannel 连接关闭")

    async def _send_init_message(self) -> None:
        """发送初始化消息 (clawd_bot_init)."""
        if not self._ws:
            return
        init_message = {
            "msgType": "clawd_bot_init",
            "agentId": self.config.agent_id,
        }
        try:
            await self._ws.send(json.dumps(init_message))
            logger.info("XiaoyiChannel 已发送初始化消息")
        except Exception as e:
            logger.warning("XiaoyiChannel 发送初始化消息失败: {}", e)
            raise

    async def _heartbeat_loop(self) -> None:
        """应用层心跳循环（20秒间隔）."""
        while self._running and self._ws:
            try:
                heartbeat = {"msgType": "heartbeat", "agentId": self.config.agent_id}
                await self._ws.send(json.dumps(heartbeat))
            except Exception as e:
                logger.warning("XiaoyiChannel 心跳发送失败: {}", e)
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
            logger.warning("XiaoyiChannel 未知方法: {}", method)

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

        user_message = Message(
            id=message.get("id", ""),
            type="req",
            channel_id=self.channel_id,
            session_id=session_id,
            params={"query": text, "task_id": task_id},
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.CHAT_SEND,
            metadata={"method": "message/stream"},
        )

        handled = False
        if self._on_message_cb is not None:
            result = self._on_message_cb(user_message)
            if inspect.isawaitable(result):
                result = await result
            handled = bool(result)

        if not handled:
            await self.bus.route_user_message(user_message)

    async def _handle_clear_context(self, message: dict[str, Any]) -> None:
        """处理清空上下文请求."""
        session_id = message.get("sessionId", "")
        logger.info("XiaoyiChannel 清空上下文: {}", session_id)

        self._session_task_map.pop(session_id, None)

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"status": {"state": "cleared"}},
        }
        await self._send_agent_response(session_id, session_id, response)

    async def _handle_tasks_cancel(self, message: dict[str, Any]) -> None:
        """处理取消任务请求."""
        session_id = message.get("sessionId", "")
        task_id = message.get("params", {}).get("id") or message.get("taskId", "")
        logger.info("XiaoyiChannel 取消任务: {} {}", session_id, task_id)

        response = {
            "jsonrpc": "2.0",
            "id": message.get("id", ""),
            "result": {"id": message.get("id", ""), "status": {"state": "canceled"}},
        }
        await self._send_agent_response(session_id, task_id, response)

    async def _send_text_response(self, session_id: str, task_id: str, text: str) -> None:
        """发送文本响应（A2A 格式）."""
        response = {
            "jsonrpc": "2.0",
            "id": f"msg_{int(time.time() * 1000)}",
            "result": {
                "taskId": task_id,
                "kind": "artifact-update",
                "append": False,
                "lastChunk": True,
                "final": True,
                "artifact": {
                    "artifactId": f"artifact_{int(time.time() * 1000)}",
                    "parts": [{"kind": "text", "text": text}],
                },
            },
        }
        await self._send_agent_response(session_id, task_id, response)

    async def _send_agent_response(self, session_id: str, task_id: str, response: dict[str, Any]) -> None:
        """发送 agent_response 包装的消息（A2A 格式）."""
        if not self._ws:
            return
        wrapper = {
            "msgType": "agent_response",
            "agentId": self.config.agent_id,
            "sessionId": session_id,
            "taskId": task_id,
            "msgDetail": json.dumps(response),
        }
        try:
            await self._ws.send(json.dumps(wrapper))
        except Exception as e:
            logger.warning("XiaoyiChannel 发送响应失败: {}", e)
