# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentServerClient - Gateway 与 AgentServer 的 WebSocket 客户端."""

from __future__ import annotations

import asyncio
import json
from abc import ABC, abstractmethod
from dataclasses import asdict
from typing import Any, AsyncIterator

from jiuwenclaw.utils import logger
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk


def _to_json(data: Any) -> str:
    """将任意对象序列化为日志友好的 JSON 字符串."""
    try:
        return json.dumps(data, ensure_ascii=False, sort_keys=True, default=str)
    except Exception:
        return repr(data)


class AgentServerClient(ABC):
    """AgentServer WebSocket 客户端接口."""

    @abstractmethod
    async def connect(self, uri: str) -> None:
        """建立与 AgentServer 的 WebSocket 连接."""
        ...

    @abstractmethod
    async def disconnect(self) -> None:
        """断开连接."""
        ...

    @abstractmethod
    async def send_request(self, request: AgentRequest) -> AgentResponse:
        """发送请求，等待完整响应."""
        ...

    @abstractmethod
    async def send_request_stream(
        self, request: AgentRequest
    ) -> AsyncIterator[AgentResponseChunk]:
        """发送请求，流式接收响应."""
        ...


def _request_to_payload(request: AgentRequest) -> dict[str, Any]:
    """将 AgentRequest 转为 WebSocket 发送的 JSON 载荷."""
    payload = asdict(request)
    # req_method 是 Enum，需要转为字符串值
    if payload.get("req_method") is not None:
        payload["req_method"] = payload["req_method"].value
    return payload


def _payload_to_response(data: dict[str, Any]) -> AgentResponse:
    """将服务端返回的 JSON 转为 AgentResponse."""
    return AgentResponse(
        request_id=data["request_id"],
        channel_id=data["channel_id"],
        ok=data.get("ok", True),
        payload=data.get("payload"),
        metadata=data.get("metadata"),
    )


def _payload_to_chunk(data: dict[str, Any]) -> AgentResponseChunk:
    """将服务端返回的 JSON 转为 AgentResponseChunk."""
    return AgentResponseChunk(
        request_id=data["request_id"],
        channel_id=data["channel_id"],
        payload=data.get("payload"),
        is_complete=data.get("is_complete", False),
    )


class WebSocketAgentServerClient(AgentServerClient):
    """
    基于 websockets 的 AgentServer WebSocket 客户端实现。

    协议约定：
    - 发送：JSON 对象，字段为 AgentRequest 的键（含 is_stream）。
    - 接收（非流式）：一条 JSON 对象，对应 AgentResponse。
    - 接收（流式）：多条 JSON 对象，对应 AgentResponseChunk，最后一条 is_complete=True。
    """

    def __init__(self, *, ping_interval: float | None = 30.0, ping_timeout: float | None = 300.0) -> None:
        self._uri: str | None = None
        self._ws: Any = None
        self._lock = asyncio.Lock()
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._server_ready: bool = False
        # 消息分发机制：根据 request_id 路由到对应队列
        self._message_queues: dict[str, asyncio.Queue] = {}
        self._receiver_task: asyncio.Task | None = None
        self._running = False

    @property
    def server_ready(self) -> bool:
        """AgentServer 是否已发送 connection.ack 确认就绪."""
        return self._server_ready

    async def connect(self, uri: str) -> None:
        if self._ws is not None:
            await self.disconnect()
        logger.info("[WebSocketAgentServerClient] 正在连接: %s", uri)
        self._uri = uri
        self._server_ready = False
        try:
            from websockets.legacy.client import connect as legacy_connect
            connect_fn = legacy_connect
        except ImportError:
            import websockets
            connect_fn = websockets.connect
        self._ws = await connect_fn(
            uri,
            ping_interval=self._ping_interval,
            ping_timeout=self._ping_timeout,
            close_timeout=5.0,
        )
        logger.info("[WebSocketAgentServerClient] 已连接: %s", uri)

        # 读取 AgentServer 的 connection.ack 事件
        try:
            raw = await asyncio.wait_for(self._ws.recv(), timeout=5.0)
            logger.info("[WebSocketAgentServerClient] connect 首帧(raw): %s", raw)
            data = json.loads(raw)
            logger.info("[WebSocketAgentServerClient] connect 首帧(parsed): %s", _to_json(data))
            if data.get("type") == "event" and data.get("event") == "connection.ack":
                self._server_ready = True
                logger.info("[WebSocketAgentServerClient] 收到 connection.ack，AgentServer 已就绪")
            else:
                logger.warning(
                    "[WebSocketAgentServerClient] 首帧非 connection.ack: %s",
                    data.get("type"),
                )
        except asyncio.TimeoutError:
            logger.warning("[WebSocketAgentServerClient] 等待 connection.ack 超时")
        except Exception as e:
            logger.warning("[WebSocketAgentServerClient] 读取 connection.ack 失败: %s", e)

        # 启动消息接收和分发任务
        self._running = True
        self._receiver_task = asyncio.create_task(self._message_receiver_loop())
        logger.info("[WebSocketAgentServerClient] 消息接收任务已启动")

    async def _message_receiver_loop(self) -> None:
        """后台任务：从 WebSocket 接收消息并根据 request_id 分发到对应队列."""
        try:
            while self._running and self._ws is not None:
                try:
                    raw = await self._ws.recv()
                    data = json.loads(raw)
                    request_id = data.get("request_id")

                    if request_id and request_id in self._message_queues:
                        # 将消息放入对应的队列
                        await self._message_queues[request_id].put(data)
                    else:
                        # 没有对应的队列，记录警告
                        logger.warning(
                            "[WebSocketAgentServerClient] 收到无目标队列的消息: request_id=%s",
                            request_id
                        )
                except asyncio.CancelledError:
                    break
                except Exception as e:
                    logger.exception("[WebSocketAgentServerClient] 消息接收循环异常: %s", e)
                    await asyncio.sleep(0.1)  # 避免快速循环
        finally:
            logger.info("[WebSocketAgentServerClient] 消息接收任务已停止")

    async def disconnect(self) -> None:
        # 停止接收任务
        self._running = False
        if self._receiver_task and not self._receiver_task.done():
            self._receiver_task.cancel()
            try:
                await self._receiver_task
            except asyncio.CancelledError:
                pass
            self._receiver_task = None

        # 清理所有队列
        self._message_queues.clear()

        # 关闭 WebSocket
        if self._ws is None:
            return
        try:
            await self._ws.close()
        except Exception as e:
            logger.warning("关闭 AgentServer WebSocket 时异常: %s", e)
        finally:
            self._ws = None
            self._uri = None
        logger.info("[WebSocketAgentServerClient] 已断开")

    def _ensure_connected(self) -> None:
        if self._ws is None:
            raise RuntimeError("未连接 AgentServer，请先调用 connect(uri)")

    async def send_request(self, request: AgentRequest) -> AgentResponse:
        self._ensure_connected()
        logger.info("[WebSocketAgentServerClient] 发送请求(非流式) AgentRequest: %s", _to_json(asdict(request)))

        # 创建该请求的消息队列
        queue = asyncio.Queue()
        self._message_queues[request.request_id] = queue

        try:
            # 发送请求
            async with self._lock:
                payload = _request_to_payload(request)
                logger.info("[WebSocketAgentServerClient] 发送请求(非流式) payload: %s", _to_json(payload))
                await self._ws.send(json.dumps(payload, ensure_ascii=False))

            # 从队列中接收响应
            data = await queue.get()
            logger.info("[WebSocketAgentServerClient] 收到响应(非流式) raw: %s", json.dumps(data, ensure_ascii=False))
            resp = _payload_to_response(data)
            logger.info("[WebSocketAgentServerClient] 收到完整响应 AgentResponse: %s", _to_json(asdict(resp)))
            return resp
        finally:
            # 清理队列
            if request.request_id in self._message_queues:
                del self._message_queues[request.request_id]

    async def send_request_stream(
        self, request: AgentRequest
    ) -> AsyncIterator[AgentResponseChunk]:
        self._ensure_connected()
        request.is_stream = True
        logger.info("[WebSocketAgentServerClient] 发送请求(流式) AgentRequest: %s", _to_json(asdict(request)))

        # 创建该请求的消息队列
        queue = asyncio.Queue()
        self._message_queues[request.request_id] = queue

        try:
            # 发送请求
            async with self._lock:
                payload = _request_to_payload(request)
                logger.info("[WebSocketAgentServerClient] 发送请求(流式) payload: %s", _to_json(payload))
                await self._ws.send(json.dumps(payload, ensure_ascii=False))

            # 从队列中接收流式响应
            chunk_count = 0
            while True:
                data = await queue.get()
                logger.info("[WebSocketAgentServerClient] 收到流式事件 raw: %s", json.dumps(data, ensure_ascii=False))
                chunk = _payload_to_chunk(data)
                chunk_count += 1
                logger.info(
                    "[WebSocketAgentServerClient] 收到流式 chunk #%s AgentResponseChunk: %s",
                    chunk_count, _to_json(asdict(chunk)),
                )
                yield chunk
                if chunk.is_complete:
                    break
            logger.info("[WebSocketAgentServerClient] 流式响应结束: request_id=%s 共 %s 个 chunk", request.request_id, chunk_count)
        except asyncio.CancelledError:
            logger.info("[WebSocketAgentServerClient] 流式接收被取消: request_id=%s", request.request_id)
            raise
        finally:
            # 清理队列
            if request.request_id in self._message_queues:
                del self._message_queues[request.request_id]


# ---------------------------------------------------------------------------
# Mock AgentServer（协议兼容，供示例或测试使用）
# ---------------------------------------------------------------------------


async def mock_agent_server_handler(ws: Any) -> None:
    """
    协议兼容的 Mock AgentServer 处理函数：根据 is_stream 回一条完整响应或多条 chunk；
    同一连接可处理多请求。可与 websockets.serve(..., host, port) 一起使用。
    """
    import websockets
    try:
        while True:
            raw = await ws.recv()
            data = json.loads(raw)
            req_id = data.get("request_id", "")
            ch_id = data.get("channel_id", "")
            params = data.get("params", {})
            is_stream = data.get("is_stream", False)
            params_str = json.dumps(params, ensure_ascii=False) if isinstance(params, dict) else str(params)

            if is_stream:
                # 流式：发 3 个 chunk，最后 is_complete=True
                for i, part in enumerate(["流式-1 ", "流式-2 ", "流式-3(完)"]):
                    chunk = {
                        "request_id": req_id,
                        "channel_id": ch_id,
                        "payload": {"content": part},
                        "is_complete": i == 2,
                    }
                    await ws.send(json.dumps(chunk, ensure_ascii=False))
            else:
                # 非流式：一条完整响应
                resp = {
                    "request_id": req_id,
                    "channel_id": ch_id,
                    "ok": True,
                    "payload": {"content": f"Echo: {params_str}"},
                    "metadata": data.get("metadata"),
                }
                await ws.send(json.dumps(resp, ensure_ascii=False))
    except websockets.exceptions.ConnectionClosed:
        pass
    except Exception as e:
        logger.exception("[MockAgentServer] 处理异常: %s", e)


async def run_mock_agent_server(
    host: str = "127.0.0.1",
    port: int = 8000,
) -> Any:
    """
    启动 Mock AgentServer（使用 mock_agent_server_handler），监听 host:port。
    返回 Server，调用方需在结束时 server.close(); await server.wait_closed()。
    websockets 14+ 使用 legacy.server.serve，与 legacy 客户端一致，避免 InvalidMessage。
    """
    try:
        from websockets.legacy.server import serve as legacy_serve
        server = await legacy_serve(mock_agent_server_handler, host, port)
    except ImportError:
        import websockets
        server = await websockets.serve(mock_agent_server_handler, host, port)
    logger.info("[MockAgentServer] 已启动: ws://%s:%s", host, port)
    return server


# ---------------------------------------------------------------------------
# 自验证：内存 Mock 服务端 + main
# ---------------------------------------------------------------------------


async def _run_verification() -> None:
    """用内存 Mock 服务端验证 WebSocketAgentServerClient 的 connect/send_request/send_request_stream."""
    port = 18765
    uri = f"ws://127.0.0.1:{port}"
    server = await run_mock_agent_server("127.0.0.1", port)
    logger.info("[main] Mock AgentServer 已启动: %s", uri)

    client = WebSocketAgentServerClient()
    try:
        await client.connect(uri)

        # 1. 非流式请求
        req1 = AgentRequest(
            request_id="req-1",
            channel_id="ch-1",
            session_id="sess-1",
            params={"message": "你好"},
        )
        resp1 = await client.send_request(req1)
        assert resp1.request_id == "req-1"
        assert resp1.ok is True
        assert "Echo:" in str(resp1.payload)
        logger.info("[main] 非流式验证通过: payload=%s", resp1.payload)

        # 2. 流式请求
        req2 = AgentRequest(
            request_id="req-2",
            channel_id="ch-1",
            session_id="sess-1",
            params={"message": "流式测试"},
        )
        chunks = []
        async for ch in client.send_request_stream(req2):
            chunks.append(ch)
        assert len(chunks) == 3
        assert chunks[-1].is_complete
        full_content = "".join(c.payload.get("content", "") for c in chunks if c.payload)
        logger.info("[main] 流式验证通过: 共 %s 个 chunk, 拼接内容=%r", len(chunks), full_content)
    finally:
        await client.disconnect()
        server.close()
        await server.wait_closed()
    logger.info("[main] 验证完成，功能正常")


def main() -> None:
    """入口：配置日志并运行自验证."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s.%(msecs)03d %(name)s %(levelname)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    asyncio.run(_run_verification())


if __name__ == "__main__":
    main()
