from __future__ import annotations

import asyncio
import json
import logging
import secrets
import time
from dataclasses import dataclass, field
from typing import Any, Callable
from urllib.parse import parse_qs, urlparse

from jiuwenclaw.channel.base import BaseChannel
from jiuwenclaw.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenclaw.channel.vibeskill_session import (
    VIBESKILL_CHANNEL_ID,
    VibeSkillSessionState,
    VibeSkillSessionStore,
    _VIBESKILL_ORIGINAL_SESSION_ID_KEY,
)
from jiuwenclaw.channel.vibeskill_file_utils import skilldev_tree_to_opencode_file_nodes
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
        self._ws_heartbeat_tasks: dict[Any, asyncio.Task] = {}
        self._message_ctx: dict[str, dict[str, Any]] = {}
        self._pending_confirms: dict[str, dict[str, Any]] = {}

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
        path = getattr(request, 'path', '/')
        query_string = getattr(request, 'query_string', '')
        full_path = f"{path}?{query_string}" if query_string else path
        parsed = urlparse(full_path)
        request_path = parsed.path
        query_params = parse_qs(parsed.query or "")

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

            self._clients.add(ws)
            logger.info(f"[VibeSkillChannel] Clients: {len(self._clients)}")

            session_ids = [str(s).strip() for s in query_params.get("sessionID", []) if str(s).strip()]
            if session_ids:
                session_id = session_ids[0]
                internal_id = await self._store.resolve_internal(session_id)
                if not internal_id:
                    session = await self._store.get_or_create(external_id=session_id)
                    internal_id = session.internal_id
                async with self._ws_sessions_lock:
                    if ws not in self._ws_sessions:
                        self._ws_sessions[ws] = set()
                    self._ws_sessions[ws].add(internal_id)
                    self._session_to_ws[internal_id] = ws

            await self._emit_ws_event(ws, "server.connected", {})
            logger.info("[VibeSkillChannel] server.connected sent")
            self._start_heartbeat_task(ws)

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
            await self.cleanup(ws)
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
                        ctx = self._ensure_message_context(session_id)
                        part = self._ensure_text_part(session_id, "text")
                        part["text"] = str(part.get("text") or "") + text
                        response = {
                            "type": "message.part.delta",
                            "properties": {
                                "sessionID": external_sid,
                                "messageID": ctx["message_id"],
                                "partID": part["id"],
                                "field": "text",
                                "delta": text,
                            },
                        }
                        await ws.send(json.dumps(response, ensure_ascii=False))
                elif event_type == "chat.final":
                    text = str(msg.payload.get("content") or "")
                    external_sid = await self._resolve_external_session_id(session_id, msg.metadata)
                    ctx = self._ensure_message_context(session_id)
                    part = self._ensure_text_part(session_id, "text")
                    part["text"] = str(part.get("text") or "") + text
                    response = {
                        "type": "message.updated",
                        "properties": {
                            "info": {
                                "id": ctx["message_id"],
                                "sessionID": external_sid,
                                "role": "assistant",
                                "parts": self._serialize_parts(ctx["parts"], external_sid),
                            }
                        },
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
        if msg_type == "question.replied":
            return await self._handle_question_replied(data)
        if msg_type == "review.replied":
            return await self._handle_review_replied(data)
        if msg_type == "desc_optimize.replied":
            return await self._handle_desc_optimize_replied(data)

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
        await self._emit_session_status(
            ws=ws,
            external_sid=(
                external_session_id
                or await self._resolve_external_session_id(session.internal_id)
                or session.internal_id
            ),
            status_type=VibeSkillSessionState.BUSY.value,
        )

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

    async def _handle_question_replied(self, data: dict[str, Any]) -> bool:
        """处理 question.replied，封装为 skilldev.respond。"""
        properties = data.get("properties") if isinstance(data.get("properties"), dict) else data
        session_id = str(properties.get("sessionID") or "").strip()
        request_id = str(properties.get("requestID") or "").strip()
        raw_answers = properties.get("answers", [])
        if not request_id:
            return False

        internal_id = await self._store.resolve_internal(session_id) if session_id else None
        if not internal_id and session_id:
            internal_id = session_id
        if not internal_id:
            return False

        pending = self._pending_confirms.get(request_id, {})
        task_id = (
            str(pending.get("task_id") or "").strip()
            or str(pending.get("session_id") or "").strip()
            or internal_id
        )
        questions = pending.get("questions", [])
        answers = self._convert_question_answers(questions, raw_answers)
        self._pending_confirms.pop(request_id, None)

        return self._dispatch_skilldev_respond(
            internal_id=internal_id,
            external_session_id=session_id,
            params={
                "task_id": task_id,
                "action": "submit",
                "answers": answers,
            },
        )

    async def _handle_review_replied(self, data: dict[str, Any]) -> bool:
        """处理 review.replied，封装为 skilldev.respond。"""
        properties = data.get("properties") if isinstance(data.get("properties"), dict) else data
        session_id = str(properties.get("sessionID") or "").strip()
        request_id = str(properties.get("id") or "").strip()
        if not request_id:
            return False

        internal_id = await self._store.resolve_internal(session_id) if session_id else None
        if not internal_id and session_id:
            internal_id = session_id
        if not internal_id:
            return False

        pending = self._pending_confirms.get(request_id, {})
        task_id = (
            str(pending.get("task_id") or "").strip()
            or str(pending.get("session_id") or "").strip()
            or internal_id
        )
        accept = bool(properties.get("accept", False))
        action = "accept" if accept else "improve"

        params: dict[str, Any] = {"task_id": task_id, "action": action}
        if action == "improve":
            feedback = str(properties.get("feedback") or "").strip()
            if feedback:
                params["feedback"] = feedback

        self._pending_confirms.pop(request_id, None)
        return self._dispatch_skilldev_respond(
            internal_id=internal_id,
            external_session_id=session_id,
            params=params,
        )

    async def _handle_desc_optimize_replied(self, data: dict[str, Any]) -> bool:
        """处理 desc_optimize.replied，封装为 skilldev.respond。"""
        properties = data.get("properties") if isinstance(data.get("properties"), dict) else data
        session_id = str(properties.get("sessionID") or "").strip()
        request_id = str(properties.get("id") or "").strip()
        if not request_id:
            return False

        internal_id = await self._store.resolve_internal(session_id) if session_id else None
        if not internal_id and session_id:
            internal_id = session_id
        if not internal_id:
            return False

        pending = self._pending_confirms.get(request_id, {})
        task_id = (
            str(pending.get("task_id") or "").strip()
            or str(pending.get("session_id") or "").strip()
            or internal_id
        )
        accept = bool(properties.get("accept", False))
        action = "optimize" if accept else "skip"
        self._pending_confirms.pop(request_id, None)
        return self._dispatch_skilldev_respond(
            internal_id=internal_id,
            external_session_id=session_id,
            params={"task_id": task_id, "action": action},
        )

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
            "skilldev.skill_name_ready": self._handle_skilldev_skill_name_ready,
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
            responses = await handler(payload, external_sid, msg.session_id)
            for response in responses:
                await ws.send(json.dumps(response, ensure_ascii=False))
            if responses:
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
                "properties": {
                    "sessionID": external_sid,
                    "messageID": self._ensure_message_context(msg.session_id).get("message_id"),
                    "partID": self._ensure_text_part(msg.session_id, "text").get("id"),
                    "field": "text",
                    "delta": text,
                },
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            return True

        if event_type == "chat.final":
            text = str(payload.get("content") or "")
            ctx = self._ensure_message_context(msg.session_id)
            text_part = self._ensure_text_part(msg.session_id, "text")
            text_part["text"] = str(text_part.get("text") or "") + text
            response = {
                "type": "message.updated",
                "properties": {
                    "info": {
                        "id": ctx["message_id"],
                        "sessionID": external_sid,
                        "role": "assistant",
                        "parts": self._serialize_parts(ctx["parts"], external_sid),
                    }
                },
            }
            await ws.send(json.dumps(response, ensure_ascii=False))
            return True

        return False

    async def _handle_skilldev_started(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.started - 任务已开始"""
        return []

    async def _handle_skilldev_stage_changed(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.stage_changed - 阶段变化"""
        return []

    async def _handle_skilldev_progress(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.progress - 进度更新"""
        return []

    async def _handle_skilldev_skill_name_ready(
        self,
        payload: dict,
        external_sid: str | None
    ) -> list[dict]:
        """skilldev.skill_name_ready - 技能名就绪"""
        skill_name = str(payload.get("skill_name") or "").strip()
        if not skill_name:
            return []
        return [{
            "type": "session.updated",
            "properties": {
                "sessionID": external_sid,
                "title": skill_name,
            },
        }]

    async def _handle_skilldev_agent_thinking(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.agent_thinking - Agent 思考中"""
        return self._build_text_stream_events(
            session_id=session_id,
            external_sid=external_sid,
            payload=payload,
            part_type="reasoning",
            text_field="thinking",
        )

    async def _handle_skilldev_agent_output(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.agent_output - Agent 输出"""
        return self._build_text_stream_events(
            session_id=session_id,
            external_sid=external_sid,
            payload=payload,
            part_type="text",
            text_field="output",
        )

    async def _handle_skilldev_tool_call(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.tool_call - 工具调用"""
        if not session_id:
            return []
        ctx = self._ensure_message_context(session_id)
        call_id = str(
            payload.get("tool_call_id")
            or payload.get("toolCallId")
            or payload.get("callID")
            or f"call_{secrets.token_hex(4)}"
        ).strip()
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "").strip()
        tool_input = payload.get("arguments") or payload.get("params") or payload.get("input") or {}
        now_ms = int(time.time() * 1000)
        part = self._ensure_tool_part(session_id, call_id, tool_name)
        part["state"] = {
            "status": "running",
            "input": tool_input,
            "title": payload.get("title") or f"执行 {tool_name or call_id}",
            "metadata": payload.get("metadata", {}),
            "time": {"start": now_ms, "end": None},
        }
        responses = self._ensure_message_announced(ctx, external_sid)
        responses.append(
            {
                "type": "message.part.updated",
                "properties": self._serialize_part(part, external_sid),
            }
        )
        return responses

    async def _handle_skilldev_tool_result(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.tool_result - 工具结果"""
        if not session_id:
            return []
        ctx = self._ensure_message_context(session_id)
        call_id = str(
            payload.get("tool_call_id")
            or payload.get("toolCallId")
            or payload.get("callID")
            or payload.get("task_id")
            or f"call_{secrets.token_hex(4)}"
        ).strip()
        tool_name = str(payload.get("tool_name") or payload.get("tool") or "").strip()
        success = bool(payload.get("success", True))
        result = payload.get("result") or payload.get("output") or ""
        part = self._ensure_tool_part(session_id, call_id, tool_name)
        start = part.get("state", {}).get("time", {}).get("start") or int(time.time() * 1000)
        part["state"] = {
            "status": "completed" if success else "error",
            "output": result,
            "title": payload.get("title") or f"{tool_name or call_id} 执行结果",
            "metadata": payload.get("metadata", {}),
            "time": {"start": start, "end": int(time.time() * 1000)},
        }
        responses = self._ensure_message_announced(ctx, external_sid)
        responses.append(
            {
                "type": "message.part.updated",
                "properties": self._serialize_part(part, external_sid),
            }
        )
        return responses

    async def _handle_skilldev_test_progress(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.test_progress - 测试进度"""
        return []

    async def _handle_skilldev_todos_update(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.todos_update - Todo 更新"""
        return [{
            "type": "todo.updated",
            "properties": {
                "sessionID": external_sid,
                "todos": payload.get("todos", []),
            },
        }]

    async def _handle_skilldev_confirm_request(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.confirm_request - 确认请求"""
        confirm_type = str(payload.get("confirm_type") or "").strip()
        request_id = str(payload.get("request_id") or f"req_{secrets.token_hex(4)}")
        task_id = str(payload.get("task_id") or "")
        if confirm_type == "review":
            self._pending_confirms[request_id] = {
                "task_id": task_id,
                "session_id": session_id or "",
                "confirm_type": confirm_type,
            }
            data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
            return [{
                "type": "review.asked",
                "properties": {
                    "id": request_id,
                    "sessionID": external_sid or session_id,
                    "benchmark": data.get("benchmark"),
                    "report": str(data.get("report") or ""),
                    "iteration": data.get("iteration"),
                },
            }]
        if confirm_type == "desc_optimize_confirm":
            self._pending_confirms[request_id] = {
                "task_id": task_id,
                "session_id": session_id or "",
                "confirm_type": confirm_type,
            }
            data = payload.get("data", {}) if isinstance(payload.get("data"), dict) else {}
            return [{
                "type": "desc_optimize.asked",
                "properties": {
                    "id": request_id,
                    "sessionID": external_sid or session_id,
                    "current_description": str(data.get("current_description") or ""),
                },
            }]

        raw_questions = payload.get("data", {}).get("questions", [])
        questions = []
        for idx, item in enumerate(raw_questions):
            if not isinstance(item, dict):
                continue
            options = []
            for option in item.get("options", []) or []:
                if not isinstance(option, dict):
                    continue
                options.append({
                    "id": str(option.get("id") or f"opt_{idx}_{len(options)}"),
                    "label": str(option.get("label") or ""),
                    "description": str(option.get("description") or ""),
                })
            questions.append({
                "id": str(item.get("id") or f"q_{idx + 1}"),
                "question": str(item.get("question") or ""),
                "header": str(item.get("header") or payload.get("title") or ""),
                "options": options,
                "multiple": bool(item.get("multiple") or item.get("allow_multiple") or False),
            })
        self._pending_confirms[request_id] = {
            "task_id": task_id,
            "session_id": session_id or "",
            "confirm_type": confirm_type or "question_clarify",
            "questions": questions,
        }
        return [{
            "type": "question.asked",
            "properties": {
                "id": request_id,
                "sessionID": external_sid or session_id,
                "questions": [
                    {
                        "question": q["question"],
                        "header": q["header"],
                        "options": [
                            {"label": opt["label"], "description": opt.get("description", "")}
                            for opt in q["options"]
                        ],
                        "multiple": q["multiple"],
                    }
                    for q in questions
                ],
                "tool": payload.get("tool") or confirm_type or "",
            },
        }]

    def _dispatch_skilldev_respond(
        self,
        internal_id: str,
        external_session_id: str,
        params: dict[str, Any],
    ) -> bool:
        msg = Message(
            id=f"vibeskill-respond-{int(time.time() * 1000):x}-{secrets.token_hex(3)}",
            type="req",
            channel_id=VIBESKILL_CHANNEL_ID,
            session_id=internal_id,
            params=params,
            timestamp=time.time(),
            ok=True,
            req_method=ReqMethod.SKILLDEV_RESPOND,
            is_stream=True,
            metadata={_VIBESKILL_ORIGINAL_SESSION_ID_KEY: external_session_id} if external_session_id else None,
        )
        self.bus.deliver_to_message_handler(msg)
        return True

    async def _handle_skilldev_artifact_ready(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.artifact_ready - 产物就绪"""
        return []

    async def _handle_skilldev_eval_ready(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.eval_ready - 评估就绪"""
        return []

    async def _handle_skilldev_validate_result(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.validate_result - 验证结果"""
        return []

    async def _handle_skilldev_desc_opt_ready(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.desc_opt_ready - 描述优化就绪"""
        return []

    async def _handle_skilldev_error(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.error - 错误"""
        responses: list[dict] = []
        if session_id:
            await self._store.set_state(session_id, VibeSkillSessionState.RETRY)
        responses.append({
            "type": "session.status",
            "properties": {
                "sessionID": external_sid,
                "status": {
                    "type": "retry",
                    "attempt": int(payload.get("retry_count") or 1),
                    "message": str(payload.get("error") or "skilldev error"),
                    "next": int(payload.get("retry_next") or (int(time.time() * 1000) + 10000)),
                },
            },
        })
        return responses

    async def _handle_skilldev_suspended(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.suspended - 暂停"""
        if session_id:
            await self._store.set_state(session_id, VibeSkillSessionState.IDLE)
        return [{
            "type": "session.status",
            "properties": {
                "sessionID": external_sid,
                "status": {"type": "idle"},
            },
        }]

    async def _handle_skilldev_completed(
        self,
        payload: dict,
        external_sid: str | None,
        session_id: str | None,
    ) -> list[dict]:
        """skilldev.completed - 完成"""
        if session_id:
            await self._store.set_state(session_id, VibeSkillSessionState.IDLE)
        return [{
            "type": "session.status",
            "properties": {
                "sessionID": external_sid,
                "status": {"type": "idle"},
            },
        }]

    async def cleanup(self, ws: Any) -> None:
        """ws 断开时清理关联的会话映射。"""
        heartbeat = self._ws_heartbeat_tasks.pop(ws, None)
        if heartbeat and not heartbeat.done():
            heartbeat.cancel()
            try:
                await heartbeat
            except asyncio.CancelledError:
                pass
            except Exception as exc:
                logger.debug("[VibeSkillChannel] cleanup heartbeat wait failed: %s", exc)
        internal_ids = self._ws_sessions.pop(ws, set())
        for sid in internal_ids:
            self._session_to_ws.pop(sid, None)
            self._message_ctx.pop(sid, None)

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

    async def _emit_ws_event(self, ws: Any, event_type: str, properties: dict[str, Any]) -> None:
        await ws.send(json.dumps({"type": event_type, "properties": properties}, ensure_ascii=False))

    def _start_heartbeat_task(self, ws: Any) -> None:
        task = asyncio.create_task(self._heartbeat_loop(ws))
        self._ws_heartbeat_tasks[ws] = task

    async def _heartbeat_loop(self, ws: Any) -> None:
        try:
            while True:
                await asyncio.sleep(10)
                if ws not in self._clients or bool(getattr(ws, "closed", False)):
                    return
                try:
                    await self._emit_ws_event(ws, "server.heartbeat", {"timestamp": int(time.time() * 1000)})
                except Exception:
                    # ws 可能在检查后立刻关闭；结束 heartbeat 循环即可。
                    return
        except asyncio.CancelledError:
            return
        except Exception as exc:
            logger.debug("[VibeSkillChannel] heartbeat loop stopped: %s", exc)

    async def _emit_session_status(
        self,
        ws: Any,
        external_sid: str | None,
        status_type: str,
        extra: dict[str, Any] | None = None,
    ) -> None:
        if not external_sid:
            return
        status: dict[str, Any] = {"type": status_type}
        if extra:
            status.update(extra)
        await self._emit_ws_event(ws, "session.status", {"sessionID": external_sid, "status": status})

    def _ensure_message_context(self, session_id: str | None) -> dict[str, Any]:
        sid = str(session_id or "").strip()
        if not sid:
            sid = "_default"
        ctx = self._message_ctx.get(sid)
        if ctx is None:
            ctx = {
                "message_id": f"msg_{secrets.token_hex(6)}",
                "parts": [],
                "part_by_type": {},
                "tool_parts": {},
                "message_announced": False,
            }
            self._message_ctx[sid] = ctx
        return ctx

    def _ensure_text_part(self, session_id: str | None, part_type: str) -> dict[str, Any]:
        ctx = self._ensure_message_context(session_id)
        part = ctx["part_by_type"].get(part_type)
        if part is None:
            part = {
                "id": f"prt_{secrets.token_hex(6)}",
                "sessionID": session_id,
                "messageID": ctx["message_id"],
                "type": part_type,
                "text": "",
            }
            ctx["part_by_type"][part_type] = part
            ctx["parts"].append(part)
        return part

    def _ensure_tool_part(self, session_id: str | None, call_id: str, tool_name: str) -> dict[str, Any]:
        ctx = self._ensure_message_context(session_id)
        part = ctx["tool_parts"].get(call_id)
        if part is None:
            part = {
                "id": f"prt_{secrets.token_hex(6)}",
                "sessionID": session_id,
                "messageID": ctx["message_id"],
                "type": "tool",
                "callID": call_id,
                "tool": tool_name,
                "state": {},
            }
            ctx["tool_parts"][call_id] = part
            ctx["parts"].append(part)
        return part

    def _ensure_message_announced(self, ctx: dict[str, Any], external_sid: str | None) -> list[dict]:
        if ctx.get("message_announced"):
            return []
        ctx["message_announced"] = True
        return [{
            "type": "message.updated",
            "properties": {
                "info": {
                    "id": ctx["message_id"],
                    "sessionID": external_sid,
                    "role": "assistant",
                    "parts": self._serialize_parts(ctx["parts"], external_sid),
                }
            },
        }]

    def _build_text_stream_events(
        self,
        session_id: str | None,
        external_sid: str | None,
        payload: dict[str, Any],
        part_type: str,
        text_field: str,
    ) -> list[dict]:
        delta = str(payload.get(text_field) or payload.get("text") or "")
        if not delta:
            return []
        ctx = self._ensure_message_context(session_id)
        part = self._ensure_text_part(session_id, part_type)
        part["text"] = str(part.get("text") or "") + delta
        responses = self._ensure_message_announced(ctx, external_sid)
        responses.append(
            {
                "type": "message.part.updated",
                "properties": self._serialize_part(part, external_sid),
            }
        )
        responses.append({
            "type": "message.part.delta",
            "properties": {
                "sessionID": external_sid,
                "messageID": ctx["message_id"],
                "partID": part["id"],
                "type": part_type,
                "delta": delta,
            },
        })
        return responses

    def _serialize_parts(self, parts: list[dict[str, Any]], external_sid: str | None) -> list[dict[str, Any]]:
        return [self._serialize_part(part, external_sid) for part in parts]

    def _serialize_part(self, part: dict[str, Any], external_sid: str | None) -> dict[str, Any]:
        serialized = dict(part)
        part_id = serialized.pop("id", None)
        if part_id is not None:
            serialized["partID"] = part_id
        serialized["sessionID"] = external_sid
        return serialized

    def _convert_question_answers(self, questions: list[dict[str, Any]], raw_answers: Any) -> list[dict[str, Any]]:
        if not isinstance(raw_answers, list):
            return []
        mapped: list[dict[str, Any]] = []
        for idx, answer_item in enumerate(raw_answers):
            question = questions[idx] if idx < len(questions) else {}
            question_id = str(question.get("id") or f"q_{idx + 1}")
            options = question.get("options") if isinstance(question.get("options"), list) else []
            label_to_id = {
                str(opt.get("label")): str(opt.get("id"))
                for opt in options if isinstance(opt, dict)
            }
            values = answer_item if isinstance(answer_item, list) else [answer_item]
            normalized_values: list[str] = []
            for value in values:
                text = str(value)
                normalized_values.append(label_to_id.get(text, text))
            answer: Any = normalized_values
            multiple = bool(question.get("multiple"))
            if not multiple:
                answer = normalized_values[0] if normalized_values else ""
            mapped.append({"question_id": question_id, "answer": answer})
        return mapped

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
        request_path = urlparse(path_str).path

        # Session 路由
        if path_str == "/api/v1/session" and method == "POST":
            return await self._handle_http_session_create(headers, body)
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/file/content") and method == "GET":
            session_id = request_path.split("/api/v1/session/", 1)[-1].replace("/file/content", "")
            return await self._handle_http_file_content(session_id, headers, path_str)
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/abort") and method == "POST":
            session_id = request_path.replace("/api/v1/session/", "").replace("/abort", "")
            return await self._handle_http_session_abort(session_id)
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/message") and method == "GET":
            session_id = request_path.replace("/api/v1/session/", "").replace("/message", "")
            return await self._handle_http_session_message(session_id, headers)
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/summarize") and method == "POST":
            session_id = request_path.replace("/api/v1/session/", "").replace("/summarize", "")
            return await self._handle_http_session_summarize(session_id)
        if request_path.startswith("/api/v1/session/") and method == "DELETE":
            session_id = request_path.split("/api/v1/session/", 1)[-1]
            return await self._handle_http_session_delete(session_id)

        # 文件路由
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/file") and method == "GET":
            session_id = request_path.split("/api/v1/session/", 1)[-1].replace("/file", "")
            return await self._handle_http_file_list(session_id, headers)
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/file/status") and method == "GET":
            session_id = request_path.split("/api/v1/session/", 1)[-1].replace("/file/status", "")
            return await self._handle_http_file_status(session_id, headers)

        # 搜索路由
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/find") and method == "GET":
            session_id = request_path.split("/api/v1/session/", 1)[-1].replace("/find", "")
            return await self._handle_http_find(session_id, headers)
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/find/file") and method == "GET":
            session_id = request_path.split("/api/v1/session/", 1)[-1].replace("/find/file", "")
            return await self._handle_http_find_file(session_id, headers)

        # VCS 路由
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/vcs") and method == "GET":
            session_id = request_path.split("/api/v1/session/", 1)[-1].replace("/vcs", "")
            return await self._handle_http_vcs(session_id)

        # 版本路由
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/version") and method == "POST":
            session_id = request_path.split("/api/v1/session/", 1)[-1].replace("/version", "")
            return await self._handle_http_version_create(session_id, body)
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/version") and method == "GET":
            session_id = request_path.split("/api/v1/session/", 1)[-1].replace("/version", "")
            return await self._handle_http_version_list(session_id, headers)
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/rollback") and method == "POST":
            # /api/v1/session/{sessionID}/version/{commitHash}/rollback
            parts = request_path.replace("/api/v1/session/", "").replace("/rollback", "").split("/version/")
            session_id = parts[0]
            commit_hash = parts[1] if len(parts) > 1 else ""
            return await self._handle_http_version_rollback(session_id, commit_hash)
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/diff") and method == "GET":
            # /api/v1/session/{sessionID}/version/{commitHash}/diff
            parts = request_path.replace("/api/v1/session/", "").replace("/diff", "").split("/version/")
            session_id = parts[0]
            commit_hash = parts[1] if len(parts) > 1 else ""
            return await self._handle_http_version_diff(session_id, commit_hash, headers)

        # 导出路由
        if request_path.startswith("/api/v1/session/") and request_path.endswith("/export") and method == "POST":
            session_id = request_path.split("/api/v1/session/", 1)[-1].replace("/export", "")
            return await self._handle_http_export(session_id, body)

        # Session GET 兜底必须放在最后，避免覆盖更具体的子路由（如 /file）。
        if request_path.startswith("/api/v1/session/") and method == "GET":
            session_id = request_path.split("/api/v1/session/", 1)[-1]
            return await self._handle_http_session_get(session_id)

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
        """GET /api/v1/.../file — 列目录（skilldev.file.list → FileNode[]）。"""
        internal_id = await self._store.resolve_internal(session_id) or session_id
        request_id = f"vibeskill-file-list-{int(time.time() * 1000):x}-{secrets.token_hex(3)}"
        env = e2a_from_agent_fields(
            request_id=request_id,
            channel_id=VIBESKILL_CHANNEL_ID,
            session_id=internal_id,
            req_method=ReqMethod.SKILLDEV_FILE_LIST,
            params={"task_id": session_id, "session_id": internal_id},
            is_stream=False,
            timestamp=time.time(),
        )
        resp = await self._send_agent_request(env)
        if not resp.ok:
            pl = dict(resp.payload) if isinstance(resp.payload, dict) else {}
            return self._json_response(502, {"error": str(pl.get("error") or "request failed")})
        payload = dict(resp.payload) if isinstance(resp.payload, dict) else {}
        if payload.get("event_type") == "skilldev.error":
            return self._json_response(400, {"error": str(payload.get("error") or "skilldev.error")})
        if not payload.get("ok", True):
            return self._json_response(400, {"error": str(payload.get("error") or "failed")})
        tree = payload.get("tree")
        if not isinstance(tree, list):
            tree = []
        file_nodes = skilldev_tree_to_opencode_file_nodes(tree, task_id=session_id)
        return self._json_response(200, file_nodes)

    async def _handle_http_file_content(
        self, session_id: str, headers: dict, raw_request_path: str
    ) -> tuple[int, dict, bytes]:
        """GET .../file/content?path= — skilldev.file.read。"""
        parsed = urlparse(raw_request_path)
        qs = parse_qs(parsed.query)

        def _q(name: str, default: str) -> str:
            vals = qs.get(name)
            if vals and str(vals[0]).strip():
                return str(vals[0]).strip()
            return default

        file_path = _q("path", "")
        if not file_path:
            return self._json_response(400, {"error": "path query parameter is required"})

        internal_id = await self._store.resolve_internal(session_id) or session_id
        request_id = f"vibeskill-file-read-{int(time.time() * 1000):x}-{secrets.token_hex(3)}"
        env = e2a_from_agent_fields(
            request_id=request_id,
            channel_id=VIBESKILL_CHANNEL_ID,
            session_id=internal_id,
            req_method=ReqMethod.SKILLDEV_FILE_READ,
            params={"task_id": session_id, "path": file_path, "session_id": internal_id},
            is_stream=False,
            timestamp=time.time(),
        )
        resp = await self._send_agent_request(env)
        if not resp.ok:
            pl = dict(resp.payload) if isinstance(resp.payload, dict) else {}
            return self._json_response(502, {"error": str(pl.get("error") or "request failed")})
        payload = dict(resp.payload) if isinstance(resp.payload, dict) else {}
        if payload.get("event_type") == "skilldev.error":
            return self._json_response(400, {"error": str(payload.get("error") or "skilldev.error")})
        if not payload.get("ok", True):
            return self._json_response(400, {"error": str(payload.get("error") or "failed")})
        out = {
            "type": "text",
            "content": str(payload.get("content") or ""),
            "encoding": "utf8",
            "mimeType": "text/plain",
        }
        return self._json_response(200, out)

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
            json.loads(body) if body else {}
        except json.JSONDecodeError:
            return self._json_response(400, {"error": "Invalid JSON"})
        request_id = f"vibeskill-export-{int(time.time() * 1000):x}-{secrets.token_hex(3)}"
        env = e2a_from_agent_fields(
            request_id=request_id,
            channel_id=VIBESKILL_CHANNEL_ID,
            session_id=session_id,
            req_method=ReqMethod.SKILLDEV_DOWNLOAD,
            params={"sessionID": session_id},
            is_stream=False,
            timestamp=time.time(),
        )
        resp = await self._send_agent_request(env)
        payload = resp.payload if isinstance(resp.payload, dict) else {}
        if not resp.ok or not bool(payload.get("ok", True)):
            err = str(payload.get("error") or "skilldev.download failed")
            return self._json_response(502, {"error": err})

        result = {
            "exportId": payload.get("exportId"),
            "url": payload.get("url"),
            "mimeType": payload.get("mimeType"),
            "exportedAt": payload.get("exportedAt"),
        }
        if not result["exportId"] or not result["url"] or not result["mimeType"]:
            return self._json_response(502, {"error": "Invalid response from skilldev.download"})
        return self._json_response(200, result)
