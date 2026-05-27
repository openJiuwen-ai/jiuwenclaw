# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Async WebSocket client for agent-runtime-service external APIs."""

from __future__ import annotations

import asyncio
import json
import logging
from typing import Any, AsyncIterator, Callable

logger = logging.getLogger(__name__)

_BASE_PATH = "/agent-runtime-service/v1"
_MOCK_AGENT_RUNTIME_BASE_URL = "localhost"
_WS_MAX_SIZE = 8 * 2**20


def _http_base_to_ws(base_url: str) -> str:
    """将 HTTP(S) base URL 转为 WebSocket base URL；已含 ws(s) 则原样返回。"""
    base = base_url.rstrip("/")
    if base.startswith("https://"):
        return "wss://" + base[8:]
    if base.startswith("http://"):
        return "ws://" + base[7:]
    if base.startswith(("ws://", "wss://")):
        return base
    return f"ws://{base}"


def _get_ws_connect() -> Callable[..., Any]:
    try:
        from websockets.legacy.client import connect as legacy_connect

        return legacy_connect
    except ImportError:
        import websockets

        return websockets.connect


class AgentRuntimeClient:
    """Client for agent-runtime-service WebSocket endpoints."""

    def __init__(
        self,
        base_url: str,
        *,
        timeout: float = 60.0,
        default_headers: dict[str, str] | None = None,
    ) -> None:
        if not base_url or not base_url.strip():
            raise ValueError("base_url is required")
        self._base_url = base_url.rstrip("/")
        self._ws_base = _http_base_to_ws(self._base_url)
        self._timeout = timeout
        self._default_headers = dict(default_headers or {})
        self._connect = _get_ws_connect()

    def _build_ws_url(self, path: str) -> str:
        if not path.startswith("/"):
            path = f"/{path}"
        return f"{self._ws_base}{_BASE_PATH}{path}"

    def _build_headers(self, extra: dict[str, str] | None = None) -> dict[str, str]:
        # TODO: 构造认证、链路 ID、租户等握手头
        headers: dict[str, str] = dict(self._default_headers)
        if extra:
            headers.update(extra)
        headers.pop("Content-Type", None)
        return headers

    def _parse_response(self, raw: str | bytes) -> dict[str, Any]:
        if isinstance(raw, bytes):
            raw = raw.decode("utf-8")
        # TODO: 按统一响应 schema 解析成功/错误响应并映射错误码
        try:
            data = json.loads(raw)
            if isinstance(data, dict):
                status = data.get("status_code", data.get("statusCode"))
                if status is not None and int(status) >= 400:
                    logger.warning(
                        "[AgentRuntimeClient] WebSocket response status=%s body=%s",
                        status,
                        data,
                    )
                return data
            return {"data": data}
        except json.JSONDecodeError:
            return {"raw": raw}

    @staticmethod
    def _is_final_frame(frame: dict[str, Any]) -> bool:
        """流式帧终止判定：响应体中 ``isFinal == True`` 视为最后一帧。"""
        if not isinstance(frame, dict):
            return False
        value = frame.get("isFinal")
        if value is None:
            data = frame.get("data")
            if isinstance(data, dict):
                value = data.get("isFinal")
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() == "true"
        return False

    def _build_connect_kwargs(
        self, headers: dict[str, str] | None
    ) -> dict[str, Any]:
        connect_kwargs: dict[str, Any] = {
            "open_timeout": self._timeout,
            "close_timeout": 5.0,
            "max_size": _WS_MAX_SIZE,
        }
        if headers:
            connect_kwargs["additional_headers"] = headers
        return connect_kwargs

    async def _request(
        self,
        path: str,
        payload: dict[str, Any] | None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> dict[str, Any]:
        """经 WebSocket 调用与 HTTP 路径对应的接口（发送 JSON 请求体，等待单条 JSON 响应）。"""
        if _MOCK_AGENT_RUNTIME_BASE_URL in self._base_url:
            raise ValueError("base_url is invalid")
        url = self._build_ws_url(path)
        headers = self._build_headers(extra_headers)
        body = payload if payload is not None else {}
        message = json.dumps(body, ensure_ascii=False)

        logger.debug("[AgentRuntimeClient] WS %s", url)
        connect_kwargs = self._build_connect_kwargs(headers)

        async with self._connect(url, addtional_headers=headers) as ws:
            await ws.send(message)
            raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)

        return self._parse_response(raw)

    async def _request_stream(
        self,
        path: str,
        payload: dict[str, Any] | None,
        *,
        extra_headers: dict[str, str] | None = None,
    ) -> AsyncIterator[dict[str, Any]]:
        """经 WebSocket 发送请求并以流的方式逐帧返回服务端推送的响应。

        生成器在以下情况结束：服务端关闭连接、单帧等待超时、或上游取消。
        """
        if _MOCK_AGENT_RUNTIME_BASE_URL in self._base_url:
            raise ValueError("base_url is invalid")
        url = self._build_ws_url(path)
        headers = self._build_headers(extra_headers)
        body = payload if payload is not None else {}
        message = json.dumps(body, ensure_ascii=False)

        logger.debug("[AgentRuntimeClient] WS stream %s", url)
        connect_kwargs = self._build_connect_kwargs(headers)

        # 局部导入，避免在未安装 websockets 时模块导入失败。
        try:
            from websockets.exceptions import ConnectionClosed
        except ImportError:  # pragma: no cover - 与 _get_ws_connect 兼容
            ConnectionClosed = Exception  # type: ignore[assignment]

        async with self._connect(url, addtional_headers=headers) as ws:
            await ws.send(message)
            while True:
                try:
                    raw = await asyncio.wait_for(ws.recv(), timeout=self._timeout)
                except asyncio.TimeoutError:
                    logger.warning(
                        "[AgentRuntimeClient] WS stream recv timeout (%ss) on %s",
                        self._timeout,
                        url,
                    )
                    break
                except ConnectionClosed:
                    logger.debug(
                        "[AgentRuntimeClient] WS stream closed by peer on %s", url
                    )
                    break
                frame = self._parse_response(raw)
                yield frame
                if self._is_final_frame(frame):
                    logger.debug(
                        "[AgentRuntimeClient] WS stream end-of-stream marker received on %s",
                        url,
                    )
                    break

    async def run_agent(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """WS /agent/run — 执行智能体、工作流、组件接口（一次性返回最终结果）."""
        # TODO: 请求体构造（将调用方字段映射为外部接口真实字段）
        payload = request if request is not None else {}
        return await self._request("/agent/run", payload)

    def run_agent_stream(
        self, request: dict[str, Any] | None = None
    ) -> AsyncIterator[dict[str, Any]]:
        """WS /agent/run — 流式接收服务端推送的响应数据帧。

        用法：

            async for frame in client.run_agent_stream({...}):
                ...
        """
        # TODO: 请求体构造（将调用方字段映射为外部接口真实字段）
        payload = request if request is not None else {}
        return self._request_stream("/agent/run", payload)

    async def a2a_run(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """WS /agent/a2a/run — Agent2Agent 运行接口."""
        # TODO: 请求体构造（将 agentId/query/filesInfo 等映射为外部接口真实字段）
        payload = request if request is not None else {}
        return await self._request("/agent/a2a/run", payload)

    async def a2a_run_for_jiuwen(
        self, request: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        """WS /agent/a2a/run-for-jiuwen — Jiuwen 专用 Agent2Agent 运行接口."""
        # TODO: 请求体构造
        payload = request if request is not None else {}
        return await self._request("/agent/a2a/run-for-jiuwen", payload)

    async def a2a_operate(self, request: dict[str, Any] | None = None) -> dict[str, Any]:
        """WS /agent/a2a/operate — 清除上下文（对话历史、变量、长期记忆）."""
        # TODO: 请求体构造
        payload = request if request is not None else {}
        return await self._request("/agent/a2a/operate", payload)


__all__ = ["AgentRuntimeClient"]
