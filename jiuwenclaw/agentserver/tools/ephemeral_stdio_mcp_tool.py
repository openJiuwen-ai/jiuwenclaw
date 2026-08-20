# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Stdio MCP without openjiuwen long-lived MCPTool: one subprocess per invoke."""

from __future__ import annotations

import logging
from contextlib import AsyncExitStack
from typing import Any, Callable

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.tool import Tool, ToolCard

logger = logging.getLogger(__name__)


def stdio_params_from_mcp_config(params: dict[str, Any]) -> dict[str, Any]:
    command = params.get("command")
    args = params.get("args")
    if not command or not isinstance(args, list):
        raise ValueError("stdio MCP 需要 params.command 与 params.args（列表）")
    out: dict[str, Any] = {
        "command": str(command).strip(),
        "args": list(args),
    }
    if isinstance(params.get("env"), dict) and params["env"]:
        out["env"] = {str(k): str(v) for k, v in params["env"].items() if k is not None and v is not None}
    if isinstance(params.get("cwd"), str) and params["cwd"].strip():
        out["cwd"] = params["cwd"].strip()
    handler = params.get("encoding_error_handler", "strict")
    if handler in ("strict", "ignore", "replace"):
        out["encoding_error_handler"] = handler
    return out


async def list_stdio_mcp_tool_defs(params: dict[str, Any]) -> list[dict[str, Any]]:
    """单次拉起 stdio MCP，列出工具定义后退出。返回 dict: name, description, input_params。"""
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    sp = stdio_params_from_mcp_config(params)
    stdio_params = StdioServerParameters(
        command=sp["command"],
        args=sp["args"],
        env=sp.get("env"),
        cwd=sp.get("cwd"),
        encoding_error_handler=str(sp.get("encoding_error_handler", "strict")),
    )
    stack = AsyncExitStack()
    try:
        client_cm = stdio_client(stdio_params)
        read_write = await stack.enter_async_context(client_cm)
        read, write = read_write
        session = await stack.enter_async_context(ClientSession(read, write, sampling_callback=None))
        await session.initialize()
        tools_response = await session.list_tools()
        rows: list[dict[str, Any]] = []
        for t in tools_response.tools:
            rows.append(
                {
                    "name": t.name,
                    "description": getattr(t, "description", "") or "",
                    "input_params": getattr(t, "inputSchema", {}) or {},
                }
            )
        return rows
    finally:
        await stack.aclose()


class EphemeralStdioMcpTool(Tool):
    """每次 invoke 单独 connect → call_tool → 关闭子进程。"""

    def __init__(
        self,
        card: ToolCard,
        get_stdio_params: Callable[[], dict[str, Any]],
        raw_tool_name: str | None = None,
    ) -> None:
        super().__init__(card)
        self._get_stdio_params = get_stdio_params
        # card.name 是 LLM 可见的带 server 前缀的 qualified name，不能直接拿去 call_tool；
        # raw_tool_name 才是 MCP server 上真正注册的名字；缺省时回退到 card.name。
        self._raw_tool_name = raw_tool_name if raw_tool_name is not None else card.name

    async def stream(self, inputs: Any, **kwargs: Any):
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    async def invoke(self, inputs: Any, **kwargs: Any) -> dict[str, Any]:
        from mcp import ClientSession, StdioServerParameters
        from mcp.client.stdio import stdio_client

        arguments = inputs if isinstance(inputs, dict) else {}
        sp = self._get_stdio_params()
        stdio_params = StdioServerParameters(
            command=sp["command"],
            args=sp["args"],
            env=sp.get("env"),
            cwd=sp.get("cwd"),
            encoding_error_handler=str(sp.get("encoding_error_handler", "strict")),
        )
        stack = AsyncExitStack()
        try:
            client_cm = stdio_client(stdio_params)
            read_write = await stack.enter_async_context(client_cm)
            read, write = read_write
            session = await stack.enter_async_context(ClientSession(read, write, sampling_callback=None))
            await session.initialize()
            tool_result = await session.call_tool(self._raw_tool_name, arguments=arguments)
            result_content: str | None = None
            if tool_result.content and len(tool_result.content) > 0:
                try:
                    logger.info(
                        "[EphemeralStdioMcp] tool=%s raw_content=%s",
                        self._raw_tool_name,
                        repr(tool_result.content),
                    )
                except Exception:
                    logger.debug(
                        "[EphemeralStdioMcp] tool=%s raw content debug failed",
                        self._raw_tool_name, exc_info=True,
                    )
                result_content = tool_result.content[-1].text
            logger.info("[EphemeralStdioMcp] tool=%s done", self._raw_tool_name)
            return {"result": result_content}
        except Exception as e:
            raise build_error(
                StatusCode.TOOL_MCP_EXECUTION_ERROR,
                cause=e,
                reason=str(e),
                method="invoke",
                card=self._card,
            ) from e
        finally:
            await stack.aclose()
