# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""YuanrongFrontendAgentClient - openYuanRong Frontend HTTP 客户端.

通过 HTTP POST 调用 openYuanRong Frontend 的函数 invocation 接口。
保留无 service_id 设计，使用 session_id 进行并发控制。
"""

from __future__ import annotations

import asyncio
import json
import logging
import uuid
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from typing import Any, AsyncIterator

from jiuwenswarm.common.e2a.agent_compat import e2a_to_agent_request
from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.gateway.routing.agent_client import AgentServerClient
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk, AgentRequest


logger = logging.getLogger(__name__)


@dataclass
class SandboxInfo:
    """YuanRong sandbox lifecycle record (placeholder until real APIs land)."""

    sandbox_id: str
    user_id: str
    agent_type: str
    status: str = "ready"
    metadata: dict[str, Any] = field(default_factory=dict)


class YuanrongFrontendAgentClient(AgentServerClient):
    """openYuanRong Frontend HTTP 客户端.

    通过 HTTP POST 调用 openYuanRong frontend 的函数 invocation 接口。
    使用 session_id 进行并发控制，不使用 service_id/agent_id。
    另提供 create_sandbox / delete_sandbox 生命周期占位，供 AgentOS Router 使用。
    """

    def __init__(
        self,
        *,
        frontend_endpoint: str,
        function_version_urn: str,
        concurrency: int = 1,
        invoke_timeout_s: float = 60.0,
    ) -> None:
        self._frontend_endpoint = (frontend_endpoint or "").rstrip("/")
        self._function_version_urn = (function_version_urn or "").strip()
        self._concurrency = max(int(concurrency), 1)
        self._invoke_timeout_s = float(invoke_timeout_s)
        self._connected = False
        self._server_ready = False

    def set_or_update_server_config(
        self,
        *,
        config: dict[str, Any],
        env: dict[str, str] | None = None,
    ) -> None:
        return None

    @property
    def server_ready(self) -> bool:
        return self._server_ready

    async def connect(self, uri: str) -> None:
        endpoint = (uri or "").strip()
        if endpoint and endpoint.lower().startswith(("http://", "https://")):
            self._frontend_endpoint = endpoint.rstrip("/")
        if not self._frontend_endpoint:
            raise ValueError("frontend_endpoint cannot be empty")
        if not self._function_version_urn:
            raise ValueError("function_version_urn cannot be empty")
        self._connected = True
        self._server_ready = True
        logger.info(
            "[YuanrontFrontendAgentClient] connected: endpoint=%s",
            self._frontend_endpoint,
        )

    async def disconnect(self) -> None:
        self._connected = False
        self._server_ready = False
        logger.info("[YuanrongFrontendAgentClient] disconnected")

    async def create_sandbox(
        self,
        *,
        user_id: str,
        agent_type: str,
        agent_id: str | None = None,
        image_name: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> SandboxInfo:
        """Create a sandbox via YuanRong (placeholder).

        Both ``swarm`` / ``jiuwenswarm`` and ``3rd`` agent types are expected to
        provision sandboxes through this API. Real Frontend create calls will
        replace the local stub below.
        """
        self._ensure_connected()
        normalized_user_id = str(user_id or "").strip()
        normalized_agent_type = str(agent_type or "").strip().lower()
        if not normalized_user_id:
            raise ValueError("user_id is required to create sandbox")
        if not normalized_agent_type:
            raise ValueError("agent_type is required to create sandbox")

        sandbox_id = f"sbx_{uuid.uuid4().hex}"
        info = SandboxInfo(
            sandbox_id=sandbox_id,
            user_id=normalized_user_id,
            agent_type=normalized_agent_type,
            status="ready",
            metadata={
                **dict(metadata or {}),
                "agent_id": agent_id,
                "image_name": image_name,
                "provisioning": "yuanrong_create_sandbox_stub",
            },
        )
        logger.info(
            "[YuanrongFrontendAgentClient] create_sandbox stub: "
            "sandbox_id=%s user_id=%s agent_type=%s agent_id=%s",
            sandbox_id,
            normalized_user_id,
            normalized_agent_type,
            agent_id,
        )
        return info

    async def delete_sandbox(
        self,
        sandbox_id: str,
        *,
        user_id: str | None = None,
        agent_type: str | None = None,
    ) -> None:
        """Delete a sandbox via YuanRong (placeholder).

        Applies to both ``swarm`` / ``jiuwenswarm`` and ``3rd`` sandboxes.
        """
        self._ensure_connected()
        normalized_sandbox_id = str(sandbox_id or "").strip()
        if not normalized_sandbox_id:
            raise ValueError("sandbox_id is required to delete sandbox")
        logger.info(
            "[YuanrongFrontendAgentClient] delete_sandbox stub: "
            "sandbox_id=%s user_id=%s agent_type=%s",
            normalized_sandbox_id,
            user_id,
            agent_type,
        )

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("client not connected")

    def _invoke_url(self) -> str:
        urn = urllib.parse.quote(self._function_version_urn, safe="")
        return f"{self._frontend_endpoint}/serverless/v1/functions/{urn}/invocations"

    def _build_invoke_payload(self, request: AgentRequest, *, stream: bool) -> dict[str, Any]:
        """构造 faas invocation 请求体（非流式 / 流式共用，仅 is_stream 不同）."""
        return {
            "request_id": request.request_id,
            "channel_id": request.channel_id,
            "session_id": request.session_id,
            "req_method": request.req_method.value if request.req_method else None,
            "params": request.params,
            "is_stream": stream,
            "timestamp": request.timestamp,
            "metadata": request.metadata,
        }

    @staticmethod
    def _is_faas_envelope(parsed: Any) -> bool:
        """是否为 faas executor 的外层封装形状.

        faas executor 把 clawee 返回值包成 {"body": <result>, "innerCode": ..., ...}，
        仅当确实识别到此形状（含 body+innerCode，且非标准 AgentResponse 形状）时才剥离，
        避免误吞 websocket 直连等其它路径返回的普通 dict。
        """
        if not isinstance(parsed, dict):
            return False
        if "body" not in parsed or "innerCode" not in parsed:
            return False
        # 已是标准 AgentResponse 形状则不当作 faas 封装处理
        return "payload" not in parsed and "ok" not in parsed

    @staticmethod
    def _normalize_faas_body(parsed: Any) -> tuple[Any, str | None]:
        """对 faas 返回体做「剥外层封装 + 二次解析」统一规范化.

        faas executor 把 clawee 返回值包成
        {"body": <result>, "innerCode": "0", "traceId":..., ...} 再 to_json_string，
        clawee.handler 返回 response_to_payload(resp) = json.dumps(asdict(resp)) 即 str，
        故 body 字段常是内层 JSON 字符串。本函数取出内层 body 并二次解析为 AgentResponse dict。

        非流式整体 body 与流式单个 chunk 共用此规范化，保证两条路径解析逻辑一致。
        仅当确实识别到 faas 外层形状（有 body+innerCode 且非 AgentResponse 形状）时剥离，
        避免误吞 websocket 直连等其它路径返回的普通 dict。

        Returns:
            (normalized, faas_error_code):faas_error_code 非 None 表示 faas 层错误（innerCode != "0"）。
        """
        # 剥 faas executor 外层封装
        if YuanrongFrontendAgentClient._is_faas_envelope(parsed):
            inner = parsed.get("body")
            if isinstance(inner, str) and inner.strip():
                try:
                    inner = json.loads(inner)
                except Exception:
                    inner = {"content": inner}
            if inner is None:
                inner = {}
            if not isinstance(inner, dict):
                inner = {"content": inner}
            inner_code = str(parsed.get("innerCode", "0"))
            if inner_code != "0":
                inner = dict(inner)
                inner["_faas_error_code"] = inner_code
                return inner, inner_code
            parsed = inner

        # 二次解析：faas 可能把 JSON 字符串放进 body 后再序列化一次，导致首次 json.loads 拿到 str
        if isinstance(parsed, str) and parsed.strip():
            try:
                parsed = json.loads(parsed)
            except Exception:
                parsed = {"content": parsed}

        return parsed, None

    @staticmethod
    def _is_agent_response_shape(parsed: Any) -> bool:
        """是否为标准 AgentResponse 形状（与 websocket parse_agent_server_wire_unary 透传语义对齐）."""
        return (
            isinstance(parsed, dict)
            and "payload" in parsed
            and "ok" in parsed
            and isinstance(parsed.get("payload"), dict)
        )

    def _parse_invoke_response(
        self,
        body: str,
        status: int,
        request: AgentRequest,
    ) -> AgentResponse:
        """faas 非流式 body → AgentResponse.

        识别到标准 AgentResponse 形状时直接透传 payload/ok（不再二次包成 {"content": parsed}），
        使网关 _session_list 等管理类调用方能直接读到 resp.payload["sessions"]。
        """
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"content": body}

        parsed, faas_err = self._normalize_faas_body(parsed)

        if self._is_agent_response_shape(parsed):
            meta = dict(parsed.get("metadata") or {})
            meta["http_status"] = status
            if faas_err:
                meta["_faas_error_code"] = faas_err
            return AgentResponse(
                request_id=str(parsed.get("request_id") or request.request_id),
                channel_id=str(parsed.get("channel_id") or request.channel_id),
                ok=(200 <= status < 300) and bool(parsed.get("ok", True)),
                payload=parsed.get("payload", {}),
                metadata=meta,
            )

        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=200 <= status < 300,
            payload={"content": parsed},
            metadata={"http_status": status},
        )

    @staticmethod
    def _normalize_invoke_chunk(text: str) -> dict[str, Any]:
        """faas 流式 chunk data 内容 → 规范化 dict（复用非流式 unwrap 前半段）.

        返回 dict 形状以便调用方取 request_id / channel_id / is_complete / payload，
        与原内联 json.loads + {content} 兜底行为一致；额外做 faas 外层剥离 + 二次解析，
        使流式路径与 send_request 解析逻辑对齐。
        """
        try:
            parsed = json.loads(text)
        except Exception:
            parsed = {"content": text}
        parsed, _ = YuanrongFrontendAgentClient._normalize_faas_body(parsed)
        return parsed if isinstance(parsed, dict) else {"content": parsed}

    def _invoke_headers(
        self,
        session_id: str,
        *,
        user_id: str | None = None,
        req_method: str | None = None,
        stream: bool = False,
    ) -> dict[str, str]:
        """构造 faas invocation 请求头.

        除了 X-Instance-Session（会话并发控制）外，当 user_id 非空时附加
        X-Session-Context: {"sessionCtx": <uid>}，faas 据此为 CreateSandbox
        绑定用户标识（function_agent 日志 "Create sandbox for <uid>"）。
        user_id 为空时只记一条 uid_empty=yes 告警，不附加该 header。
        """
        headers = {
            "Content-Type": "application/json",
            "X-Instance-Session": json.dumps(
                {"sessionID": session_id, "concurrency": self._concurrency},
                ensure_ascii=False,
            ),
        }
        if stream:
            headers["Accept"] = "text/event-stream"
        uid = str(user_id or "").strip()
        if uid:
            session_context = json.dumps({"sessionCtx": uid}, ensure_ascii=False)
            headers["X-Session-Context"] = session_context
            logger.debug(
                "[YuanrongFrontendAgentClient] invoke headers: method=%s session_id=%s user_id=%s "
                "X-Session-Context=%s stream=%s",
                req_method,
                session_id,
                uid,
                session_context,
                stream,
            )
        else:
            logger.info(
                "[YuanrongFrontendAgentClient] invoke headers: method=%s session_id=%s "
                "uid_empty=yes X-Session-Context omitted stream=%s",
                req_method,
                session_id,
                stream,
            )
        return headers

    def _do_invoke(
        self,
        payload: dict[str, Any],
        session_id: str,
        user_id: str | None = None,
    ) -> tuple[int, str]:
        headers = self._invoke_headers(
            session_id,
            user_id=user_id,
            req_method=payload.get("req_method"),
        )
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self._invoke_url(), data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self._invoke_timeout_s) as resp:
                status = int(getattr(resp, "status", 200))
                text = resp.read().decode("utf-8", errors="replace")
                return status, text
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", errors="replace") if err.fp else str(err)
            logger.error(
                "[YuanrontFrontendAgentClient] HTTP error: session_id=%s, code=%d",
                session_id,
                getattr(err, "code", 500),
            )
            return int(getattr(err, "code", 500) or 500), text
        except Exception as err:
            logger.error(
                "[YuanrontFrontendAgentClient] request failed: session_id=%s, error=%s",
                session_id,
                str(err),
            )
            return 500, str(err)

    async def send_request(self, envelope: E2AEnvelope) -> AgentResponse:
        """发送非流式请求.

        Args:
            envelope: E2A 信封

        Returns:
            AgentResponse 响应
        """
        self._ensure_connected()
        request = e2a_to_agent_request(envelope)
        payload = self._build_invoke_payload(request, stream=False)
        session_id = request.session_id or ""
        status, body = await asyncio.to_thread(
            self._do_invoke,
            payload,
            session_id,
            envelope.user_id,
        )
        return self._parse_invoke_response(body, status, request)

    async def send_request_stream(self, envelope: E2AEnvelope) -> AsyncIterator[AgentResponseChunk]:
        """发送流式请求.

        Args:
            envelope: E2A 信封

        Yields:
            AgentResponseChunk 响应块
        """
        self._ensure_connected()
        request = e2a_to_agent_request(envelope)
        payload = self._build_invoke_payload(request, stream=True)

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
        session_id = request.session_id or ""
        reader_task = asyncio.create_task(
            asyncio.to_thread(
                self._do_invoke_stream,
                payload,
                session_id,
                queue,
                loop,
                envelope.user_id,
            )
        )
        try:
            while True:
                item_type, text = await queue.get()
                if item_type == "chunk" and text:
                    # SSE 解析已完成，复用非流式 unwrap 前半段规范化 chunk body
                    parsed_obj = self._normalize_invoke_chunk(text)
                    yield AgentResponseChunk(
                        request_id=str(parsed_obj.get("request_id") or request.request_id),
                        channel_id=str(parsed_obj.get("channel_id") or request.channel_id),
                        payload=parsed_obj.get("payload", parsed_obj.get("content")),
                        is_complete=bool(parsed_obj.get("is_complete", False)),
                    )
                elif item_type == "error":
                    yield AgentResponseChunk(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        payload={"error": text or "invoke stream failed"},
                        is_complete=False,
                    )
                elif item_type == "exception":
                    raise RuntimeError(f"invoke stream failed: {text}")
                elif item_type == "done":
                    break

            yield AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload=None,
                is_complete=True,
            )
        finally:
            reader_task.cancel()
            try:
                await reader_task
            except asyncio.CancelledError:
                pass

    def _do_invoke_stream(
        self,
        payload: dict[str, Any],
        session_id: str,
        out_queue: asyncio.Queue[tuple[str, str | None]],
        loop: asyncio.AbstractEventLoop,
        user_id: str | None = None,
    ) -> None:
        """执行流式 HTTP 调用（在线程中运行）.

        Args:
            payload: 请求负载
            session_id: 会话ID
            out_queue: 输出队列
            loop: 事件循环
            user_id: 用户ID（透传给 faas 的 X-Session-Context）
        """
        headers = self._invoke_headers(
            session_id,
            user_id=user_id,
            req_method=payload.get("req_method"),
            stream=True,
        )
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        req = urllib.request.Request(self._invoke_url(), data=data, headers=headers, method="POST")

        try:
            with urllib.request.urlopen(req, timeout=self._invoke_timeout_s) as resp:
                status = int(getattr(resp, "status", 200))

                if not (200 <= status < 300):
                    text = resp.read().decode("utf-8", errors="replace")
                    logger.error("[YuanrontFrontendAgentClient] HTTP错误状态码: %d, 响应: %s", status, text[:500])
                    loop.call_soon_threadsafe(
                        out_queue.put_nowait,
                        ("error", json.dumps({"http_status": status, "body": text}, ensure_ascii=False)),
                    )
                    return

                # SSE 解析：按行处理
                chunk_count = 0
                total_bytes = 0
                sse_line_buffer = ""
                while True:
                    chunk = resp.read(1024)
                    if not chunk:
                        # 处理缓冲区中剩余的数据
                        if sse_line_buffer.strip():
                            self._process_sse_chunk(sse_line_buffer, out_queue, loop)
                        break

                    chunk_text = chunk.decode("utf-8", errors="replace")
                    total_bytes += len(chunk)
                    chunk_count += 1

                    # SSE 解析：按行处理
                    sse_line_buffer += chunk_text
                    lines = sse_line_buffer.split('\n')
                    # 保留最后一个可能不完整的行
                    sse_line_buffer = lines[-1] if lines else ""

                    for line in lines[:-1]:
                        line_stripped = line.strip()
                        if line_stripped.startswith('data: '):
                            data_content = line_stripped[6:]  # 去掉 "data: " 前缀
                            self._process_sse_chunk(data_content, out_queue, loop)
        except urllib.error.HTTPError as err:
            text = err.read().decode("utf-8", errors="replace") if err.fp else str(err)
            logger.error(
                "[YuanrontFrontendAgentClient] stream HTTP error: session_id=%s, code=%d",
                session_id,
                getattr(err, "code", 500),
            )
            loop.call_soon_threadsafe(
                out_queue.put_nowait,
                (
                    "error",
                    json.dumps({
                        "http_status": int(getattr(err, "code", 500) or 500),
                        "body": text
                    }, ensure_ascii=False),
                ),
            )
        except Exception as err:
            logger.error(
                "[YuanrontFrontendAgentClient] stream request failed: session_id=%s, error=%s",
                session_id,
                str(err),
            )
            loop.call_soon_threadsafe(out_queue.put_nowait, ("exception", str(err)))
        finally:
            loop.call_soon_threadsafe(out_queue.put_nowait, ("done", None))

    def _process_sse_chunk(
        self,
        data_content: str,
        out_queue: asyncio.Queue,
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """处理 SSE 数据块.

        Args:
            data_content: data: 后的内容（已去掉前缀）
            out_queue: 输出队列
            loop: 事件循环
        """
        data_content_stripped = data_content.strip()

        # 检查是否是结束标记
        if data_content_stripped == "[DONE]":
            loop.call_soon_threadsafe(out_queue.put_nowait, ("done", None))
            return

        # 发送 JSON 数据
        loop.call_soon_threadsafe(out_queue.put_nowait, ("chunk", data_content_stripped))
