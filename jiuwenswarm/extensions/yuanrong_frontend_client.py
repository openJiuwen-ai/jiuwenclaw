# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""YuanrongFrontendAgentClient - openYuanRong Frontend HTTP 客户端.

通过 HTTP POST 调用 openYuanRong Frontend 的函数 invocation 接口。
保留无 service_id 设计，使用 session_id 进行并发控制。
"""

from __future__ import annotations

import asyncio
import json
import logging
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, AsyncIterator

from jiuwenswarm.common.e2a.agent_compat import e2a_to_agent_request
from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.gateway.routing.agent_client import AgentServerClient
from jiuwenswarm.common.schema.agent import AgentResponse, AgentResponseChunk


logger = logging.getLogger(__name__)


class YuanrongFrontendAgentClient(AgentServerClient):
    """openYuanRong Frontend HTTP 客户端.

    通过 HTTP POST 调用 openYuanRong frontend 的函数 invocation 接口。
    使用 session_id 进行并发控制，不使用 service_id/agent_id。
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

    def _ensure_connected(self) -> None:
        if not self._connected:
            raise RuntimeError("client not connected")

    def _invoke_url(self) -> str:
        urn = urllib.parse.quote(self._function_version_urn, safe="")
        return f"{self._frontend_endpoint}/serverless/v1/functions/{urn}/invocations"

    def _do_invoke(self, payload: dict[str, Any], session_id: str) -> tuple[int, str]:
        headers = {
            "Content-Type": "application/json",
            "X-Instance-Session": json.dumps(
                {"sessionID": session_id, "concurrency": self._concurrency},
                ensure_ascii=False,
            ),
        }
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
        payload = {
            "request_id": request.request_id,
            "channel_id": request.channel_id,
            "session_id": request.session_id,
            "req_method": request.req_method.value if request.req_method else None,
            "params": request.params,
            "is_stream": False,
            "timestamp": request.timestamp,
            "metadata": request.metadata,
        }
        session_id = request.session_id or ""
        status, body = await asyncio.to_thread(self._do_invoke, payload, session_id)
        try:
            parsed = json.loads(body) if body else {}
        except Exception:
            parsed = {"content": body}
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=200 <= status < 300,
            payload={"content": parsed},
            metadata={"http_status": status},
        )

    async def send_request_stream(self, envelope: E2AEnvelope) -> AsyncIterator[AgentResponseChunk]:
        """发送流式请求.

        Args:
            envelope: E2A 信封

        Yields:
            AgentResponseChunk 响应块
        """
        self._ensure_connected()
        request = e2a_to_agent_request(envelope)
        payload = {
            "request_id": request.request_id,
            "channel_id": request.channel_id,
            "session_id": request.session_id,
            "req_method": request.req_method.value if request.req_method else None,
            "params": request.params,
            "is_stream": True,
            "timestamp": request.timestamp,
            "metadata": request.metadata,
        }

        loop = asyncio.get_running_loop()
        queue: asyncio.Queue[tuple[str, str | None]] = asyncio.Queue()
        session_id = request.session_id or ""
        reader_task = asyncio.create_task(
            asyncio.to_thread(self._do_invoke_stream, payload, session_id, queue, loop)
        )
        try:
            while True:
                item_type, text = await queue.get()
                if item_type == "chunk" and text:
                    # SSE 解析已完成，这里直接解析 JSON
                    try:
                        parsed = json.loads(text)
                    except Exception:
                        parsed = {"content": text}
                    parsed_obj = parsed if isinstance(parsed, dict) else {"content": parsed}
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
    ) -> None:
        """执行流式 HTTP 调用（在线程中运行）.

        Args:
            payload: 请求负载
            session_id: 会话ID
            out_queue: 输出队列
            loop: 事件循环
        """
        headers = {
            "Content-Type": "application/json",
            "Accept": "text/event-stream",
            "X-Instance-Session": json.dumps(
                {"sessionID": session_id, "concurrency": self._concurrency},
                ensure_ascii=False,
            ),
        }
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
