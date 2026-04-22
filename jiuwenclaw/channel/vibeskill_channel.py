from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable

from jiuwenclaw.channel.base import BaseChannel
from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenclaw.channel.vibeskill_session import (
    VIBESKILL_CHANNEL_ID,
    VibeSkillSessionState,
    VibeSkillSessionStore,
    _VIBESKILL_ORIGINAL_SESSION_ID_KEY,
)
from jiuwenclaw.schema.message import Message, ReqMethod

logger = logging.getLogger(__name__)


@dataclass
class VibeSkillConfig:
    """VibeSkill Channel 配置。"""

    enabled: bool = True
    channel_id: str = VIBESKILL_CHANNEL_ID
    default_session_id: str = "vibeskill_session"
    http_port: int = 19002  # 独立 HTTP 端口
    ws_port: int = 19003  # 独立 WebSocket 端口


class VibeSkillChannel(BaseChannel):
    """VibeSkill Channel.

    基于 BaseChannel 实现，统一接入 MessageHandler 总线。
    - HTTP Server：独立处理 REST 请求（不走 GatewayServer websockets）
    - WebSocket Server：独立处理 WebSocket 连接（不走 GatewayServer）
    """

    name = VIBESKILL_CHANNEL_ID

    def __init__(
        self,
        config: VibeSkillConfig,
        router,
        agent_client,
    ) -> None:
        super().__init__(config, router)
        self.config: VibeSkillConfig = config
        self._agent_client = agent_client
        self._store = VibeSkillSessionStore()
        self._ws_sessions: dict[Any, set[str]] = {}  # ws -> set of internal_ids
        self._session_to_ws: dict[str, Any] = {}  # internal_id -> ws
        self._ws_sessions_lock = asyncio.Lock()
        self._clients: set[Any] = set()
        self._on_message_cb: Callable[[Message], Any] | None = None
        self._http_server: asyncio.Server | None = None
        self._ws_server: Any | None = None

    def on_message(self, callback: Callable[[Message], Any]) -> None:
        self._on_message_cb = callback

    @property
    def channel_id(self) -> str:
        return str(self.config.channel_id or self.name).strip() or self.name

    async def start(self) -> None:
        if self._running:
            return
        self._running = True

        # 启动独立 HTTP 服务器处理 VibeSkill REST 请求
        self._http_server = await asyncio.start_server(
            self._handle_http_connection,
            "127.0.0.1",
            self.config.http_port,
        )
        logger.info("[VibeSkillChannel] HTTP server started: http://127.0.0.1:%d/api/v1", self.config.http_port)

        # 启动独立 WebSocket 服务器
        import websockets
        self._ws_server = await websockets.serve(
            self._handle_ws_connection,
            "127.0.0.1",
            self.config.ws_port,
            ping_interval=20,
            ping_timeout=60,
        )
        logger.info(
            "[VibeSkillChannel] WebSocket server started: ws://127.0.0.1:%d/api/v1/messages",
            self.config.ws_port,
        )

    async def stop(self) -> None:
        self._running = False
        if self._http_server is not None:
            self._http_server.close()
            await self._http_server.wait_closed()
            self._http_server = None
        if self._ws_server is not None:
            self._ws_server.close()
            await self._ws_server.wait_closed()
            self._ws_server = None

    async def _handle_ws_connection(self, ws: Any) -> None:
        """处理 WebSocket 连接。"""
        request = getattr(ws, 'request', None)
        if request is None:
            logger.warning("[VibeSkillChannel] No request object found")
            await ws.close(code=1008, reason="no request")
            return

        # aiohttp: request.path 是路径, request.query_string 是 query string
        from urllib.parse import urlparse, parse_qs
        path = getattr(request, 'path', '/')
        query_string = getattr(request, 'query_string', '')
        full_path = f"{path}?{query_string}" if query_string else path
        parsed = urlparse(full_path)
        request_path = parsed.path
        query_string = parsed.query

        remote = getattr(ws, 'remote_address', 'unknown')
        logger.info(
            "[VibeSkillChannel] WS connection from %s, path=%s, query_string=%s",
            remote, path, query_string,
        )
        try:
            # 验证路径
            if request_path != "/api/v1/messages":
                logger.warning(f"[VibeSkillChannel] Invalid path: {request_path}")
                await ws.close(code=1008, reason="unsupported path")
                return

            # 从 query string 中提取 sessionID
            from urllib.parse import parse_qs
            query_params = parse_qs(query_string)
            session_ids = query_params.get("sessionID", [])
            external_session_id = session_ids[0] if session_ids else None

            self._clients.add(ws)
            logger.info(f"[VibeSkillChannel] Clients: {len(self._clients)}")

            # 发送 server.connected
            await ws.send(json.dumps({
                "type": "server.connected",
                "properties": {},
            }, ensure_ascii=False))
            logger.info("[VibeSkillChannel] server.connected sent")

            async for raw in ws:
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError:
                    await ws.send(json.dumps({
                        "type": "res", "id": "", "ok": False, "error": "invalid json"
                    }, ensure_ascii=False))
                    continue

                # 调用 inbound_intercept 处理入站消息
                handled = await self.inbound_intercept(ws, data)
                if not handled:
                    await ws.send(json.dumps({
                        "type": "res", "id": "", "ok": False, "error": "unhandled"
                    }, ensure_ascii=False))
        except Exception as e:
            logger.exception(f"[VibeSkillChannel] WS error: {e}")
        finally:
            self._clients.discard(ws)
            self.cleanup(ws)
            logger.info(f"[VibeSkillChannel] WS disconnected, clients: {len(self._clients)}")

    async def _handle_http_connection(self, reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        """处理 HTTP 连接（用于 REST 请求）。"""
        try:
            # 读取请求行
            request_line = await reader.readline()
            if not request_line:
                return

            request_line_str = request_line.decode("utf-8", errors="replace").strip()
            parts = request_line_str.split(" ")
            if len(parts) < 2:
                return

            method = parts[0]
            raw_path = parts[1] if len(parts) > 1 else "/"

            # 读取 headers
            headers: dict[str, str] = {}
            content_length = 0
            while True:
                line = await reader.readline()
                if not line or line == b"\r\n":
                    break
                line_str = line.decode("utf-8", errors="replace").strip()
                if ":" in line_str:
                    key, value = line_str.split(":", 1)
                    headers[key.strip().lower()] = value.strip()
                    if key.strip().lower() == "content-length":
                        try:
                            content_length = int(value.strip())
                        except ValueError:
                            pass

            # 读取 body
            body = b""
            if content_length > 0:
                body = await reader.readexactly(content_length)

            # 调用 http_handler 处理请求
            status, resp_headers, resp_body = await self.http_handler(
                method, raw_path, headers, body
            )

            # 发送响应
            resp_headers_str = "\r\n".join(f"{k}: {v}" for k, v in resp_headers.items())
            response = (
                f"HTTP/1.1 {status}\r\n"
                f"{resp_headers_str}\r\n"
                f"\r\n"
            )
            writer.write(response.encode("utf-8"))
            writer.write(resp_body)
            await writer.drain()
        except Exception as e:
            logger.warning("[VibeSkillChannel] HTTP handler error: %s", e)
        finally:
            try:
                writer.close()
                await writer.wait_closed()
            except Exception as e:
                logger.debug("[VibeSkillChannel] Error closing writer: %s", e)

    async def send(self, msg: Message) -> None:
        """ChannelManager 分发的出站消息，推送给对应的 WebSocket client。"""
        session_id = str(msg.session_id or "").strip()
        if not session_id:
            logger.warning("[VibeSkillChannel] send() called with empty session_id")
            return

        # 查找对应的 WebSocket
        ws = self._session_to_ws.get(session_id)
        if ws is None:
            # session_id 可能是 external ID，尝试解析为 internal ID 后再查找
            internal_id = await self._store.resolve_internal(session_id)
            if internal_id:
                ws = self._session_to_ws.get(internal_id)
            if ws is None:
                logger.warning(f"[VibeSkillChannel] send() no ws found for session_id={session_id}")
                return
        if bool(getattr(ws, "closed", False)):
            logger.warning(f"[VibeSkillChannel] send() ws already closed for session_id={session_id}")
            return

        # 使用 outbound_intercept 转换消息格式并推送
        handled = await self.outbound_intercept(msg, ws)
        if not handled:
            # fallback: 直接推送 chat.delta 或 chat.final
            if msg.type == "event" and isinstance(msg.payload, dict):
                event_type = str(msg.payload.get("event_type") or "").strip()
                if event_type == "chat.delta":
                    text = str(msg.payload.get("content") or "")
                    if text:
                        external_sid = await self._resolve_external_session_id(session_id, msg.metadata)
                        response = {
                            "type": "message.part.delta",
                            "sessionId": external_sid,
                            "part": {"type": "text", "text": text},
                        }
                        await ws.send(json.dumps(response, ensure_ascii=False))
                elif event_type == "chat.final":
                    text = str(msg.payload.get("content") or "")
                    external_sid = await self._resolve_external_session_id(session_id, msg.metadata)
                    response = {
                        "type": "message.updated",
                        "sessionId": external_sid,
                        "message": {"role": "assistant", "content": text},
                    }
                    await ws.send(json.dumps(response, ensure_ascii=False))

    async def inbound_intercept(self, ws: Any, data: dict[str, Any]) -> bool:
        """拦截 VibeSkill WebSocket 消息。

        处理 message.send，将其转换为 Message 送入 MessageHandler。
        """
        if not isinstance(data, dict):
            return False

        msg_type = str(data.get("type") or "").strip()

        if msg_type == "message.send":
            return await self._handle_message_send(ws, data)

        return False

    async def _handle_message_send(self, ws: Any, data: dict[str, Any]) -> bool:
        """处理 message.send 类型的消息，封装为 skilldev.start 并发送到 MessageHandler。"""
        external_session_id = str(data.get("sessionID") or "").strip()
        parts = data.get("parts", [])
        msg_model = data.get("model")
        agent = data.get("agent", "coder")
        system_prompt = data.get("system")
        request_id = f"vibeskill-{int(time.time() * 1000):x}-{secrets.token_hex(3)}"

        # 提取 query (text parts)
        query = ""
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "text":
                query += str(part.get("text") or "")

        # 提取 files 和 skill_packages (file parts)
        files = []
        skill_packages = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "file":
                file_info = {
                    "filename": part.get("filename", ""),
                    "url": part.get("url", ""),
                    "mime": part.get("mime", ""),
                }
                resource_type = part.get("resourceType", "")
                if resource_type == "skill":
                    # skill 文件作为 skill_packages
                    skill_packages.append({
                        "filename": part.get("filename", ""),
                        "url": part.get("url", ""),
                    })
                else:
                    files.append(file_info)

        # 提取 tools (toolDefinition parts)
        tools = []
        for part in parts:
            if isinstance(part, dict) and part.get("type") == "toolDefinition":
                tools.append({
                    "toolId": part.get("toolId", ""),
                    "toolType": part.get("toolType", ""),
                    "name": part.get("name", ""),
                    "description": part.get("description", ""),
                    "parameters": part.get("parameters", {}),
                })

        session = await self._store.get_or_create(external_id=external_session_id or None)

        if external_session_id and not session.external_id:
            await self._store.bind_external(session.internal_id, external_session_id)
        elif not external_session_id and session.external_id:
            external_session_id = session.external_id

        await self._store.set_state(session.internal_id, VibeSkillSessionState.BUSY)

        async with self._ws_sessions_lock:
            if ws not in self._ws_sessions:
                self._ws_sessions[ws] = set()
            self._ws_sessions[ws].add(session.internal_id)
            self._session_to_ws[session.internal_id] = ws

        # 构建 skilldev.start 格式的 params
        params: dict[str, Any] = {
            "session_id": session.internal_id,
            "query": query,
        }

        # files (非 skill 的文件)
        if files:
            params["files"] = files

        # skill_packages
        if skill_packages:
            params["skill_packages"] = skill_packages

        # tools (toolDefinition)
        if tools:
            params["tools"] = tools

        # 可选字段
        task_id = data.get("taskId") or data.get("task_id")
        if task_id:
            params["task_id"] = task_id

        # model
        if msg_model and isinstance(msg_model, dict):
            if msg_model.get("providerID"):
                params["provider_id"] = msg_model["providerID"]
            if msg_model.get("modelID"):
                params["model_id"] = msg_model["modelID"]

        if agent:
            params["agent"] = agent

        if system_prompt:
            params["system"] = system_prompt

        msg_metadata = {_VIBESKILL_ORIGINAL_SESSION_ID_KEY: external_session_id} if external_session_id else None

        # 构建 Message 并直接发送到 MessageHandler
        msg = Message(
            id=request_id,
            type="req",
            channel_id=VIBESKILL_CHANNEL_ID,
            session_id=session.internal_id,
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.SKILLDEV_START,
            is_stream=True,
            metadata=msg_metadata,
        )

        # 直接发送到 MessageHandler
        self.bus.deliver_to_message_handler(msg)

        return True

    async def outbound_intercept(self, msg: Message, ws: Any) -> bool:
        """拦截 AgentServer 出站消息，转换为 VibeSkill 流式事件。"""
        if msg.type != "event":
            return False

        payload = msg.payload
        if not isinstance(payload, dict):
            return False

        event_type = str(payload.get("event_type") or "").strip()
        external_sid = await self._resolve_external_session_id(msg.session_id, msg.metadata)

        # SkillDev 事件处理
        skilldev_events = {
            "skilldev.started": self._handle_skilldev_started,
            "skilldev.stage_changed": self._handle_skilldev_stage_changed,
            "skilldev.progress": self._handle_skilldev_progress,
            "skilldev.agent_thinking": self._handle_skilldev_agent_thinking,
            "skilldev.agent_output": self._handle_skilldev_agent_output,
            "skilldev.tool_call": self._handle_skilldev_tool_call,
            "skilldev.tool_result": self._handle_skilldev_tool_result,
            "skilldev.test_progress": self._handle_skilldev_test_progress,
            "skilldev.todos_update": self._handle_skilldev_todos_update,
            "skilldev.confirm_request": self._handle_skilldev_confirm_request,
            "skilldev.artifact_ready": self._handle_skilldev_artifact_ready,
            "skilldev.eval_ready": self._handle_skilldev_eval_ready,
            "skilldev.validate_result": self._handle_skilldev_validate_result,
            "skilldev.desc_opt_ready": self._handle_skilldev_desc_opt_ready,
            "skilldev.error": self._handle_skilldev_error,
            "skilldev.suspended": self._handle_skilldev_suspended,
            "skilldev.completed": self._handle_skilldev_completed,
        }

        handler = skilldev_events.get(event_type)
        if handler:
            response = await handler(payload, external_sid, msg.session_id)
            if response:
                await ws.send(json.dumps(response, ensure_ascii=False))
                return True
            return False

        # 通用 chat 事件处理
        if event_type in ("chat.final", "chat.cancel"):
            if msg.session_id:
                await self._store.set_state(msg.session_id, VibeSkillSessionState.IDLE)

        if event_type == "chat.delta":
            text = str(payload.get("content") or "")
            if not text:
                return False
            response = {
                "type": "message.part.delta",
                "sessionId": external_sid,
                "part": {"type": "text", "text": text},
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            return True

        if event_type == "chat.final":
            text = str(payload.get("content") or "")
            response = {
                "type": "message.updated",
                "sessionId": external_sid,
                "message": {"role": "assistant", "content": text},
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            return True

        if event_type == "chat.processing_status":
            is_processing = bool(payload.get("is_processing", False))
            response = {
                "type": "message.processing",
                "sessionId": external_sid,
                "processing": is_processing,
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            return True

        return False

    async def _handle_skilldev_started(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.started - 任务已开始"""
        return {
            "type": "skilldev.started",
            "sessionId": external_sid,
            "payload": payload,
        }

    async def _handle_skilldev_stage_changed(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.stage_changed - 阶段变化"""
        return {
            "type": "skilldev.stage_changed",
            "sessionId": external_sid,
            "stage": payload.get("stage", ""),
            "payload": payload,
        }

    async def _handle_skilldev_progress(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.progress - 进度更新"""
        return {
            "type": "skilldev.progress",
            "sessionId": external_sid,
            "progress": payload.get("progress", 0),
            "payload": payload,
        }

    async def _handle_skilldev_agent_thinking(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.agent_thinking - Agent 思考中"""
        return {
            "type": "skilldev.agent_thinking",
            "sessionId": external_sid,
            "thinking": payload.get("thinking", ""),
            "payload": payload,
        }

    async def _handle_skilldev_agent_output(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.agent_output - Agent 输出"""
        return {
            "type": "skilldev.agent_output",
            "sessionId": external_sid,
            "output": payload.get("output", ""),
            "payload": payload,
        }

    async def _handle_skilldev_tool_call(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.tool_call - 工具调用"""
        return {
            "type": "skilldev.tool_call",
            "sessionId": external_sid,
            "tool": payload.get("tool", ""),
            "params": payload.get("params", {}),
            "payload": payload,
        }

    async def _handle_skilldev_tool_result(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.tool_result - 工具结果"""
        return {
            "type": "skilldev.tool_result",
            "sessionId": external_sid,
            "tool": payload.get("tool", ""),
            "result": payload.get("result", ""),
            "payload": payload,
        }

    async def _handle_skilldev_test_progress(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.test_progress - 测试进度"""
        return {
            "type": "skilldev.test_progress",
            "sessionId": external_sid,
            "progress": payload.get("progress", 0),
            "payload": payload,
        }

    async def _handle_skilldev_todos_update(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.todos_update - Todo 更新"""
        return {
            "type": "skilldev.todos_update",
            "sessionId": external_sid,
            "todos": payload.get("todos", []),
            "payload": payload,
        }

    async def _handle_skilldev_confirm_request(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.confirm_request - 确认请求"""
        return {
            "type": "skilldev.confirm_request",
            "sessionId": external_sid,
            "message": payload.get("message", ""),
            "options": payload.get("options", []),
            "payload": payload,
        }

    async def _handle_skilldev_artifact_ready(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.artifact_ready - 产物就绪"""
        return {
            "type": "skilldev.artifact_ready",
            "sessionId": external_sid,
            "artifact": payload.get("artifact", {}),
            "payload": payload,
        }

    async def _handle_skilldev_eval_ready(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.eval_ready - 评估就绪"""
        return {
            "type": "skilldev.eval_ready",
            "sessionId": external_sid,
            "eval": payload.get("eval", {}),
            "payload": payload,
        }

    async def _handle_skilldev_validate_result(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.validate_result - 验证结果"""
        return {
            "type": "skilldev.validate_result",
            "sessionId": external_sid,
            "result": payload.get("result", {}),
            "payload": payload,
        }

    async def _handle_skilldev_desc_opt_ready(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.desc_opt_ready - 描述优化就绪"""
        return {
            "type": "skilldev.desc_opt_ready",
            "sessionId": external_sid,
            "description": payload.get("description", ""),
            "payload": payload,
        }

    async def _handle_skilldev_error(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.error - 错误"""
        if session_id:
            await self._store.set_state(session_id, VibeSkillSessionState.IDLE)
        return {
            "type": "skilldev.error",
            "sessionId": external_sid,
            "error": payload.get("error", ""),
            "payload": payload,
        }

    async def _handle_skilldev_suspended(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.suspended - 暂停"""
        if session_id:
            await self._store.set_state(session_id, VibeSkillSessionState.IDLE)
        return {
            "type": "skilldev.suspended",
            "sessionId": external_sid,
            "reason": payload.get("reason", ""),
            "payload": payload,
        }

    async def _handle_skilldev_completed(
        self,
        payload: dict[str, Any],
        external_sid: str | None,
        session_id: str | None,
    ) -> dict | None:
        """skilldev.completed - 完成"""
        if session_id:
            await self._store.set_state(session_id, VibeSkillSessionState.IDLE)
        return {
            "type": "skilldev.completed",
            "sessionId": external_sid,
            "result": payload.get("result", {}),
            "payload": payload,
        }

    def cleanup(self, ws: Any) -> None:
        """ws 断开时清理关联的会话映射。"""
        internal_ids = self._ws_sessions.pop(ws, set())
        for sid in internal_ids:
            self._session_to_ws.pop(sid, None)

    async def _resolve_external_session_id(
        self,
        session_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> str | None:
        """将内部 sessionId 解析为外部 sessionId。"""
        sid = str(session_id or "").strip()
        if not sid:
            return None

        original = ""
        if isinstance(metadata, dict):
            original = str(metadata.get(_VIBESKILL_ORIGINAL_SESSION_ID_KEY) or "").strip()
        if original:
            return original

        return await self._store.resolve_external(sid)

    async def http_handler(
        self,
        method: str,
        path: str,
        headers: dict[str, str],
        body: bytes,
    ) -> tuple[int, dict[str, str], bytes]:
        """处理 VibeSkill HTTP REST 请求。

        统一入口：HTTP 和 WebSocket 共用同一端口 /api/v1。
        """
        path_str = str(path or "").strip()

        # Session 路由
        if path_str == "/api/v1/session" and method == "POST":
            return await self._handle_http_session_create(headers, body)
        if path_str.startswith("/api/v1/session/") and method == "GET":
            session_id = path_str.split("/api/v1/session/", 1)[-1]
            return await self._handle_http_session_get(session_id)
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/abort") and method == "POST":
            session_id = path_str.replace("/api/v1/session/", "").replace("/abort", "")
            return await self._handle_http_session_abort(session_id)
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/message") and method == "GET":
            session_id = path_str.replace("/api/v1/session/", "").replace("/message", "")
            return await self._handle_http_session_message(session_id, headers)
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/summarize") and method == "POST":
            session_id = path_str.replace("/api/v1/session/", "").replace("/summarize", "")
            return await self._handle_http_session_summarize(session_id)
        if path_str.startswith("/api/v1/session/") and method == "DELETE":
            session_id = path_str.split("/api/v1/session/", 1)[-1]
            return await self._handle_http_session_delete(session_id)

        # 文件路由
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/file") and method == "GET":
            session_id = path_str.split("/api/v1/session/", 1)[-1].replace("/file", "")
            return await self._handle_http_file_list(session_id, headers)
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/file/content") and method == "GET":
            session_id = path_str.split("/api/v1/session/", 1)[-1].replace("/file/content", "")
            return await self._handle_http_file_content(session_id, headers)
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/file/status") and method == "GET":
            session_id = path_str.split("/api/v1/session/", 1)[-1].replace("/file/status", "")
            return await self._handle_http_file_status(session_id, headers)

        # 搜索路由
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/find") and method == "GET":
            session_id = path_str.split("/api/v1/session/", 1)[-1].replace("/find", "")
            return await self._handle_http_find(session_id, headers)
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/find/file") and method == "GET":
            session_id = path_str.split("/api/v1/session/", 1)[-1].replace("/find/file", "")
            return await self._handle_http_find_file(session_id, headers)

        # VCS 路由
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/vcs") and method == "GET":
            session_id = path_str.split("/api/v1/session/", 1)[-1].replace("/vcs", "")
            return await self._handle_http_vcs(session_id)

        # 版本路由
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/version") and method == "POST":
            session_id = path_str.split("/api/v1/session/", 1)[-1].replace("/version", "")
            return await self._handle_http_version_create(session_id, body)
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/version") and method == "GET":
            session_id = path_str.split("/api/v1/session/", 1)[-1].replace("/version", "")
            return await self._handle_http_version_list(session_id, headers)
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/rollback") and method == "POST":
            # /api/v1/session/{sessionID}/version/{commitHash}/rollback
            parts = path_str.replace("/api/v1/session/", "").replace("/rollback", "").split("/version/")
            session_id = parts[0]
            commit_hash = parts[1] if len(parts) > 1 else ""
            return await self._handle_http_version_rollback(session_id, commit_hash)
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/diff") and method == "GET":
            # /api/v1/session/{sessionID}/version/{commitHash}/diff
            parts = path_str.replace("/api/v1/session/", "").replace("/diff", "").split("/version/")
            session_id = parts[0]
            commit_hash = parts[1] if len(parts) > 1 else ""
            return await self._handle_http_version_diff(session_id, commit_hash, headers)

        # 导出路由
        if path_str.startswith("/api/v1/session/") and path_str.endswith("/export") and method == "POST":
            session_id = path_str.split("/api/v1/session/", 1)[-1].replace("/export", "")
            return await self._handle_http_export(session_id, body)

        return (404, {"Content-Type": "application/json"}, b'{"error": "Not found"}')

    def _json_response(self, status: int, data: Any) -> tuple[int, dict[str, str], bytes]:
        """构建 JSON 响应。"""
        body = json.dumps(data, ensure_ascii=False)
        return (status, {"Content-Type": "application/json", "Connection": "close"}, body.encode("utf-8"))

    async def _send_agent_request(self, env) -> Any:
        """发送请求到 AgentServer 并返回响应。"""
        return await self._agent_client.send_request(env)

    async def _handle_http_session_create(self, headers: dict, body: bytes) -> tuple[int, dict, bytes]:
        """POST /api/v1/session - 创建会话。

        仅创建本地 session 记录，配置数据由 message.send 的 parts 传入。
        """
        # 创建本地 session
        session = await self._store.get_or_create(external_id=None)
        session_id = session.internal_id

        response_data = {
            "sessionID": session_id,
            "time": {
                "created": int(session.created_at * 1000),
                "updated": int(time.time() * 1000),
            },
            "status": {
                "sessionStatus": session.state.value,
                "sandboxStatus": "none",
            },
        }
        return self._json_response(200, response_data)

    async def _handle_http_session_get(self, session_id: str) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id} - 查询会话状态。"""
        internal_id = await self._store.resolve_internal(session_id)
        if not internal_id:
            internal_id = session_id

        state = await self._store.get_state(internal_id)
        session_obj = await self._store.get_session(internal_id)

        response_data = {
            "sessionID": session_id,
            "time": {
                "created": int((session_obj.created_at if session_obj else time.time()) * 1000),
                "updated": int((session_obj.updated_at if session_obj else time.time()) * 1000),
            },
            "status": {
                "sessionStatus": state.value,
                "sandboxStatus": "none",
            },
        }
        return self._json_response(200, response_data)

    async def _handle_http_session_abort(self, session_id: str) -> tuple[int, dict, bytes]:
        """POST /api/v1/session/{id}/abort - 中止 AI 处理。"""
        internal_id = await self._store.resolve_internal(session_id)
        if not internal_id:
            internal_id = session_id

        request_id = f"vibeskill-session-abort-{int(time.time() * 1000):x}-{secrets.token_hex(3)}"

        env = e2a_from_agent_fields(
            request_id=request_id,
            channel_id=VIBESKILL_CHANNEL_ID,
            session_id=internal_id,
            req_method=ReqMethod.CHAT_CANCEL,
            params={"session_id": internal_id},
            is_stream=False,
            timestamp=time.time(),
        )

        await self._send_agent_request(env)
        await self._store.set_state(internal_id, VibeSkillSessionState.IDLE)

        return self._json_response(200, {"aborted": True})

    async def _handle_http_session_message(self, session_id: str, headers: dict) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id}/message - 获取历史消息。"""
        return self._json_response(200, {"total": 0, "messages": []})

    async def _handle_http_session_summarize(self, session_id: str) -> tuple[int, dict, bytes]:
        """POST /api/v1/session/{id}/summarize - 触发会话总结。"""
        return self._json_response(202, {"triggered": True})

    async def _handle_http_session_delete(self, session_id: str) -> tuple[int, dict, bytes]:
        """DELETE /api/v1/session/{id} - 删除会话。"""
        internal_id = await self._store.resolve_internal(session_id)
        if internal_id:
            await self._store.delete_session(internal_id)
        return self._json_response(200, {"deleted": True})

    async def _handle_http_file_list(self, session_id: str, headers: dict) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id}/file - 列目录。"""
        return self._json_response(200, [])

    async def _handle_http_file_content(self, session_id: str, headers: dict) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id}/file/content - 读取文件。"""
        return self._json_response(200, {"type": "text", "content": "", "encoding": "utf8", "mimeType": "text/plain"})

    async def _handle_http_file_status(self, session_id: str, headers: dict) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id}/file/status - Git 状态。"""
        return self._json_response(200, [])

    async def _handle_http_find(self, session_id: str, headers: dict) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id}/find - 全文搜索。"""
        return self._json_response(200, [])

    async def _handle_http_find_file(self, session_id: str, headers: dict) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id}/find/file - 文件名搜索。"""
        return self._json_response(200, [])

    async def _handle_http_vcs(self, session_id: str) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id}/vcs - VCS 信息。"""
        return self._json_response(200, {"branch": "main"})

    async def _handle_http_version_create(self, session_id: str, body: bytes) -> tuple[int, dict, bytes]:
        """POST /api/v1/session/{id}/version - 创建版本快照。"""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return self._json_response(400, {"error": "Invalid JSON"})
        return self._json_response(200, {
            "commitHash": secrets.token_hex(6),
            "files": [],
            "time": {"created": int(time.time() * 1000)},
        })

    async def _handle_http_version_list(self, session_id: str, headers: dict) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id}/version - 版本列表。"""
        return self._json_response(200, {"branch": "main", "total": 0, "commits": []})

    async def _handle_http_version_rollback(self, session_id: str, commit_hash: str) -> tuple[int, dict, bytes]:
        """POST /api/v1/session/{id}/version/{hash}/rollback - 版本回滚。"""
        return self._json_response(200, {"rolledBack": True})

    async def _handle_http_version_diff(
        self, session_id: str, commit_hash: str, headers: dict
    ) -> tuple[int, dict, bytes]:
        """GET /api/v1/session/{id}/version/{hash}/diff - 版本差异。"""
        return self._json_response(
            200,
            {
                "format": "STAT",
                "stats": {
                    "additions": 0,
                    "deletions": 0,
                    "files": 0,
                    "filesChanged": [],
                },
            },
        )

    async def _handle_http_export(self, session_id: str, body: bytes) -> tuple[int, dict, bytes]:
        """POST /api/v1/session/{id}/export - 导出 Skill 产物。"""
        try:
            data = json.loads(body) if body else {}
        except json.JSONDecodeError:
            return self._json_response(400, {"error": "Invalid JSON"})
        commit_hash = data.get("commitHash", "")
        return self._json_response(200, {
            "exportId": f"exp_{secrets.token_hex(4)}",
            "url": f"https://obs.internal/skill-exports/exp_{secrets.token_hex(4)}.tar.gz",
            "mimeType": "application/gzip",
            "exportedAt": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        })
