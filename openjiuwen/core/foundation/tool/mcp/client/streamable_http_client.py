# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from contextlib import AsyncExitStack
from typing import Any, Dict, List, Optional

import httpx

from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool import McpServerConfig, McpToolCard
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT
from openjiuwen.core.foundation.tool.mcp.client.mcp_client import McpClient


class StreamableHttpClient(McpClient):
    """Streamable HTTP transport based MCP client."""
    __client_name__ = "streamable-http"

    def __init__(
        self, config: McpServerConfig,
    ):
        super().__init__(config)
        self._name = config.server_name
        self._client = None
        self._session = None
        self._read = None
        self._write = None
        self._exit_stack = AsyncExitStack()
        if config.auth_headers is not None or config.auth_query_params is not None:
            self._auth_provider = AuthHeaderAndQueryProvider(
                auth_headers=config.auth_headers or {},
                auth_query_params=config.auth_query_params or {},
            )
            logger.info("Using custom header and query authorization for streamable-http client")
        else:
            self._auth_provider = None

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        from mcp import ClientSession
        from mcp.client.streamable_http import streamablehttp_client

        try:
            actual_timeout = timeout if timeout != NO_TIMEOUT else 60.0
            self._client = streamablehttp_client(
                self._server_path,
                timeout=actual_timeout,
                auth=self._auth_provider
            )
            client_tuple = await self._exit_stack.enter_async_context(self._client)
            self._read, self._write, *_ = client_tuple
            self._session = await self._exit_stack.enter_async_context(
                ClientSession(self._read, self._write, sampling_callback=None)
            )
            await self._session.initialize()
            logger.info(f"Streamable-http client connected successfully to {self._server_path}")
            return True
        except Exception as e:
            logger.error(f"Streamable-http connection failed to {self._server_path}: {e}")
            await self.disconnect()
            return False

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        """Close streamable-http connection."""
        try:
            await self._exit_stack.aclose()
            self._session = None
            self._client = None
            self._read = None
            self._write = None
            logger.info("Streamable-http client disconnected successfully")
            return True
        except Exception as e:
            logger.error(f"Streamable-http disconnection failed: {e}")
            return False

    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        """List available tools via streamable-http."""
        if not self._session:
            raise RuntimeError("Not connected to streamable-http server")

        try:
            tools_response = await self._session.list_tools()
            tools_list = [
                McpToolCard(
                    name=tool.name,
                    server_name=self._name,
                    description=getattr(tool, "description", ""),
                    input_params=getattr(tool, "inputSchema", {}),
                )
                for tool in tools_response.tools
            ]
            logger.info(f"Retrieved {len(tools_list)} tools from streamable-http server")
            return tools_list
        except Exception as e:
            logger.error(f"Failed to list tools via streamable-http: {e}")
            raise

    async def call_tool(self, tool_name: str, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        """Call tool via streamable-http."""
        if not self._session:
            raise RuntimeError("Not connected to streamable-http server")

        try:
            logger.info(f"Calling tool '{tool_name}' via streamable-http with arguments: {arguments}")
            tool_result = await self._session.call_tool(tool_name, arguments=arguments)
            result_content = None
            if tool_result.content and len(tool_result.content) > 0:
                result_content = tool_result.content[-1].text
            logger.info(f"Tool '{tool_name}' call completed via streamable-http")
            return result_content
        except Exception as e:
            logger.error(f"Tool call failed via streamable-http: {e}")
            raise

    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        """Get specific tool info via streamable-http."""
        tools = await self.list_tools(timeout=timeout)
        for tool in tools:
            if tool.name == tool_name:
                logger.debug(f"Found tool info for '{tool_name}' via streamable-http")
                return tool
        logger.warning(f"Tool '{tool_name}' not found via streamable-http")
        return None


class AuthHeaderAndQueryProvider(httpx.Auth):
    def __init__(self, auth_headers: Dict[str, str], auth_query_params: Dict[str, str]):
        self.headers = auth_headers
        self.query_params = auth_query_params

    async def async_auth_flow(self, request: httpx.Request):
        if self.headers:
            for key, value in self.headers.items():
                request.headers[key] = value
        if self.query_params:
            request.url = request.url.copy_merge_params(self.query_params)
        yield request
