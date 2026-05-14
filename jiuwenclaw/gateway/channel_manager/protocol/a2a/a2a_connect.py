from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass
from typing import Any

from jiuwenclaw.gateway.channel_manager.base import BaseChannel
from jiuwenclaw.common.schema.message import EventType, Message, ReqMethod

logger = logging.getLogger(__name__)


@dataclass
class A2AChannelConfig:
    enabled: bool = False
    host: str = "127.0.0.1"
    port: int = 19100
    rpc_path: str = "/a2a"
    card_path: str = "/.well-known/agent-card.json"
    extended_card_path: str = "/agent/authenticatedExtendedCard"
    protocol_version: str = "1.0.0"
    channel_id: str = "a2a"
    app_name: str = "JiuwenClaw Gateway A2A Server"
    app_description: str = "A2A ingress for JiuwenClaw Gateway"
    app_version: str = "0.1.0"


@dataclass
class _PendingA2ARequest:
    queue: asyncio.Queue[Message]


class _A2AAgentExecutor:
    """A2A SDK AgentExecutor that forwards request via channel callback."""

    def __init__(self, channel: "A2AChannel") -> None:
        self._channel = channel

    async def execute(self, context: Any, event_queue: Any) -> None:
        from a2a.types import (
            Artifact,
            Part,
            TaskArtifactUpdateEvent,
            TaskState,
            TaskStatus,
            TaskStatusUpdateEvent,
        )

        request_id = str(context.task_id or f"a2a_{uuid.uuid4().hex[:12]}")
        task_id = str(context.task_id or request_id)
        context_id = str(context.context_id or f"a2a_ctx_{uuid.uuid4().hex[:8]}")
        query, files = self._channel.map_a2a_parts_to_params(
            getattr(context, "message", None)
        )
        if not query:
            query = str(context.get_user_input() or "").strip()
        if not query:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(
                        state=TaskState.TASK_STATE_FAILED,
                    ),
                )
            )
            await event_queue.close()
            return

        try:
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_WORKING),
                )
            )
            pending = await self._channel.dispatch_a2a_request(
                request_id=request_id,
                session_id=context_id,
                query=query,
                files=files,
                metadata=dict(context.metadata or {}),
            )
            artifact_id = f"{task_id}_response"
            artifact_started = False
            while True:
                response_msg = await pending.queue.get()
                response_parts = self._channel.message_to_a2a_parts(
                    response_msg,
                    fallback_to_text=False,
                )
                is_terminal = self._channel.is_terminal_message(response_msg)
                if (
                    is_terminal
                    and not response_parts
                    and response_msg.event_type == EventType.CHAT_ERROR
                ):
                    response_parts = self._channel.message_to_a2a_parts(
                        response_msg,
                        fallback_to_text=True,
                    )
                filtered_parts = []
                for part in response_parts:
                    part_text = str(getattr(part, "text", "") or "").strip()
                    if self._channel.is_completion_sentinel_text(part_text):
                        continue
                    filtered_parts.append(part)
                response_parts = filtered_parts
                if response_parts:
                    await event_queue.enqueue_event(
                        TaskArtifactUpdateEvent(
                            task_id=task_id,
                            context_id=context_id,
                            artifact=Artifact(
                                artifact_id=artifact_id,
                                name="response",
                                parts=response_parts,
                                metadata=response_msg.metadata or None,
                            ),
                            append=artifact_started,
                            last_chunk=is_terminal,
                        )
                    )
                    artifact_started = True
                if is_terminal:
                    if response_msg.event_type == EventType.CHAT_ERROR:
                        final_state = TaskState.TASK_STATE_FAILED
                    elif response_msg.event_type == EventType.CHAT_INTERRUPT_RESULT:
                        final_state = TaskState.TASK_STATE_CANCELED
                    else:
                        final_state = TaskState.TASK_STATE_COMPLETED
                    await event_queue.enqueue_event(
                        TaskStatusUpdateEvent(
                            task_id=task_id,
                            context_id=context_id,
                            status=TaskStatus(state=final_state),
                        )
                    )
                    break
        except Exception as exc:  # noqa: BLE001
            logger.exception("[A2AChannel] execution failed: request_id=%s err=%s", request_id, exc)
            await event_queue.enqueue_event(
                TaskStatusUpdateEvent(
                    task_id=task_id,
                    context_id=context_id,
                    status=TaskStatus(state=TaskState.TASK_STATE_FAILED),
                )
            )
        finally:
            self._channel.clear_pending_request(request_id)
            await event_queue.close()

    async def cancel(self, context: Any, event_queue: Any) -> None:
        from a2a.types import Message as A2AMessage, Part, Role, TaskState, TaskStatus, TaskStatusUpdateEvent

        task_id = str(context.task_id or "a2a")
        context_id = str(context.context_id or f"a2a_ctx_{uuid.uuid4().hex[:8]}")
        await event_queue.enqueue_event(
            TaskStatusUpdateEvent(
                task_id=task_id,
                context_id=context_id,
                status=TaskStatus(state=TaskState.TASK_STATE_CANCELED),
            )
        )
        await event_queue.close(immediate=True)


class A2AChannel(BaseChannel):
    name = "a2a"

    def __init__(self, config: A2AChannelConfig, router: Any):
        super().__init__(config, router)
        self.config = config
        self._on_message_cb = None
        self._pending: dict[str, _PendingA2ARequest] = {}
        self._uvicorn_server: Any | None = None
        self._server_task: asyncio.Task | None = None

    @property
    def channel_id(self) -> str:
        return str(self.config.channel_id or self.name).strip() or self.name

    def on_message(self, callback) -> None:
        self._on_message_cb = callback

    async def start(self) -> None:
        if self._running:
            return
        if not self.config.enabled:
            logger.info("[A2AChannel] disabled by config")
            return

        from a2a.server.apps import A2AFastAPIApplication
        from a2a.server.request_handlers import DefaultRequestHandler
        from a2a.server.tasks import InMemoryPushNotificationConfigStore, InMemoryTaskStore
        from a2a.types import AgentCapabilities, AgentCard, AgentInterface, AgentSkill
        import uvicorn

        agent_card = AgentCard(
            name=self.config.app_name,
            description=self.config.app_description,
            version=self.config.app_version,
            supported_interfaces=[
                AgentInterface(
                    url=f"http://{self.config.host}:{self.config.port}{self.config.rpc_path}",
                    protocol_binding="jsonrpc",
                    protocol_version=self.config.protocol_version,
                )
            ],
            capabilities=AgentCapabilities(streaming=True, push_notifications=False),
            default_input_modes=["text/plain"],
            default_output_modes=["text/plain"],
            skills=[
                AgentSkill(
                    id="chat",
                    name="chat",
                    description="Send user prompt to JiuwenClaw via Gateway",
                    tags=["chat", "gateway", "jiuwenclaw"],
                    examples=["Hello", "Summarize this"],
                    input_modes=["text/plain"],
                    output_modes=["text/plain"],
                )
            ],
        )
        request_handler = DefaultRequestHandler(
            agent_executor=_A2AAgentExecutor(self),
            task_store=InMemoryTaskStore(),
            push_config_store=InMemoryPushNotificationConfigStore(),
        )
        app_builder = A2AFastAPIApplication(
            agent_card=agent_card,
            http_handler=request_handler,
        )
        fastapi_app = app_builder.build(
            rpc_url=self.config.rpc_path,
            agent_card_url=self.config.card_path,
            extended_agent_card_url=self.config.extended_card_path,
        )

        uv_cfg = uvicorn.Config(
            app=fastapi_app,
            host=self.config.host,
            port=self.config.port,
            log_level="info",
            access_log=False,
        )
        self._uvicorn_server = uvicorn.Server(uv_cfg)
        self._server_task = asyncio.create_task(self._uvicorn_server.serve(), name="a2a-channel-server")
        await asyncio.sleep(0.2)
        if self._server_task.done():
            exc = self._server_task.exception()
            if exc:
                raise exc
        self._running = True
        logger.info(
            "[A2AChannel] started: http://%s:%s%s",
            self.config.host,
            self.config.port,
            self.config.rpc_path,
        )

    async def stop(self) -> None:
        self._running = False
        if self._uvicorn_server is not None:
            self._uvicorn_server.should_exit = True
        if self._server_task is not None:
            try:
                await self._server_task
            except Exception as exc:  # noqa: BLE001
                logger.warning("[A2AChannel] shutdown with error: %s", exc)
        self._uvicorn_server = None
        self._server_task = None
        for pending in list(self._pending.values()):
            # Wake waiting executors during shutdown.
            await pending.queue.put(
                Message(
                    id="a2a_shutdown",
                    type="event",
                    channel_id=self.channel_id,
                    session_id=None,
                    params={},
                    timestamp=time.time(),
                    ok=False,
                    payload={"error": "a2a channel stopped", "is_complete": True},
                    event_type=EventType.CHAT_ERROR,
                )
            )
        self._pending.clear()
        logger.info("[A2AChannel] stopped")

    async def send(self, msg: Message) -> None:
        pending = self._pending.get(str(msg.id))
        if pending is None:
            return
        await pending.queue.put(msg)

    async def dispatch_a2a_request(
        self,
        *,
        request_id: str,
        session_id: str | None,
        query: str,
        files: list[dict[str, Any]] | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> _PendingA2ARequest:
        if self._on_message_cb is None:
            raise RuntimeError("A2AChannel on_message callback is not set")
        pending = _PendingA2ARequest(queue=asyncio.Queue())
        self._pending[request_id] = pending
        try:
            msg = Message(
                id=request_id,
                type="req",
                channel_id=self.channel_id,
                session_id=session_id or f"a2a_{uuid.uuid4().hex[:8]}",
                params=self._build_request_params(query=query, files=files),
                timestamp=time.time(),
                ok=True,
                req_method=ReqMethod.CHAT_SEND,
                is_stream=True,
                metadata=metadata or None,
            )
            result = self._on_message_cb(msg)
            if asyncio.iscoroutine(result):
                await result
            return pending
        finally:
            # Keep pending entry for send() until terminal message is consumed by executor.
            pass

    def clear_pending_request(self, request_id: str) -> None:
        self._pending.pop(str(request_id), None)

    @staticmethod
    def message_to_text(msg: Message) -> str:
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        if msg.type == "event" and msg.event_type == EventType.CHAT_ERROR:
            return str(payload.get("error") or payload.get("content") or "agent request failed")
        if "content" in payload:
            return str(payload.get("content") or "")
        if payload:
            return str(payload)
        return ""

    @staticmethod
    def message_to_a2a_parts(msg: Message, *, fallback_to_text: bool = True) -> list[Any]:
        """Map internal message payload to A2A response parts."""
        from a2a.types import Part

        payload = msg.payload if isinstance(msg.payload, dict) else {}
        parts: list[Any] = []

        # Keep error response readable for A2A callers.
        if msg.type == "event" and msg.event_type == EventType.CHAT_ERROR:
            error_text = str(payload.get("error") or payload.get("content") or "agent request failed")
            return [Part(text=error_text)]

        content = payload.get("content")
        if isinstance(content, str) and content.strip():
            normalized_content = content.strip()
            if not A2AChannel.is_completion_sentinel_text(normalized_content):
                parts.append(Part(text=normalized_content))
        elif content is not None and not isinstance(content, (dict, list)):
            parts.append(Part(text=str(content)))
        result = payload.get("result")
        if isinstance(result, str) and result.strip():
            normalized_result = result.strip()
            if not A2AChannel.is_completion_sentinel_text(normalized_result):
                parts.append(Part(text=normalized_result))
        # Surface tool events in stream mode so callers can observe progress.
        if msg.event_type == EventType.CHAT_TOOL_CALL and isinstance(payload.get("tool_call"), dict):
            tool_call = payload.get("tool_call") or {}
            tool_name = str(tool_call.get("name") or "tool").strip()
            parts.append(Part(text=f"[tool_call] {tool_name}"))
        if msg.event_type == EventType.CHAT_TOOL_RESULT:
            tool_name = str(payload.get("tool_name") or "").strip()
            tool_result = payload.get("result")
            if isinstance(tool_result, str) and tool_result.strip():
                label = f"[tool_result:{tool_name}] " if tool_name else "[tool_result] "
                parts.append(Part(text=f"{label}{tool_result.strip()}"))

        raw_files = payload.get("files")
        files = raw_files if isinstance(raw_files, list) else []
        for idx, file_item in enumerate(files):
            if not isinstance(file_item, dict):
                continue
            file_name = str(file_item.get("filename") or file_item.get("name") or f"file_{idx}").strip()
            media_type = str(file_item.get("media_type") or file_item.get("type") or "").strip()
            url = str(file_item.get("url") or file_item.get("uri") or "").strip()
            data = str(file_item.get("data") or "").strip()
            raw = str(file_item.get("raw") or "").strip()

            common_fields: dict[str, str] = {}
            if file_name:
                common_fields["filename"] = file_name
            if media_type:
                common_fields["media_type"] = media_type
            if url:
                parts.append(Part(url=url, **common_fields))
            if data:
                parts.append(Part(data=data, **common_fields))
            if raw:
                parts.append(Part(raw=raw, **common_fields))

        if parts:
            return parts
        if fallback_to_text:
            return [Part(text=A2AChannel.message_to_text(msg))]
        return []

    @staticmethod
    def is_completion_sentinel_text(text: str) -> bool:
        compact = "".join(text.split()).lower()
        return compact in {"{'is_complete':true}", '{"is_complete":true}'}

    @staticmethod
    def is_terminal_message(msg: Message) -> bool:
        if msg.type == "res":
            return True
        if msg.type != "event":
            return False
        if msg.event_type in {
            EventType.CHAT_ERROR,
            EventType.CHAT_INTERRUPT_RESULT,
        }:
            return True
        payload = msg.payload if isinstance(msg.payload, dict) else {}
        if payload.get("is_complete") is True:
            return True
        return False

    @staticmethod
    def _build_request_params(
        *,
        query: str,
        files: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        params: dict[str, Any] = {"query": query}
        if files:
            params["files"] = files
        return params

    @staticmethod
    def map_a2a_parts_to_params(a2a_message: Any) -> tuple[str, list[dict[str, Any]]]:
        """Map A2A message parts to JiuwenClaw-friendly query/files params."""
        if a2a_message is None:
            return "", []

        text_segments: list[str] = []
        files: list[dict[str, Any]] = []
        parts = getattr(a2a_message, "parts", None) or []
        for idx, part in enumerate(parts):
            text = str(getattr(part, "text", "") or "").strip()
            if text:
                text_segments.append(text)

            file_name = str(getattr(part, "filename", "") or "").strip()
            media_type = str(getattr(part, "media_type", "") or "").strip()
            url = str(getattr(part, "url", "") or "").strip()
            data = str(getattr(part, "data", "") or "").strip()
            raw = str(getattr(part, "raw", "") or "").strip()

            # Preserve non-text parts as files metadata for downstream tools.
            if url or data or raw:
                normalized_name = file_name or f"a2a_part_{idx}"
                entry: dict[str, Any] = {
                    # web_channel compatibility keys
                    "name": normalized_name,
                    "filename": normalized_name,
                }
                if media_type:
                    entry["media_type"] = media_type
                    # common consumers check `type`
                    entry["type"] = media_type
                if url:
                    entry["url"] = url
                    entry["uri"] = url
                if data:
                    entry["data"] = data
                    entry["encoding"] = "base64"
                if raw:
                    entry["raw"] = raw
                files.append(entry)

        query = "\n".join(seg for seg in text_segments if seg).strip()
        return query, files
