# -*- coding: UTF-8 -*-
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
from pathlib import Path
from collections import defaultdict
from typing import TYPE_CHECKING, Optional, Union, List, Any, Dict

import yaml
import httpx
import anyio
from jsonschema_path import SchemaPath

from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.tool.mcp.base import NO_TIMEOUT
from openjiuwen.core.foundation.tool import McpClient, McpServerConfig
from openjiuwen.core.foundation.tool import McpToolCard
from openjiuwen.core.common.exception.codes import StatusCode

if TYPE_CHECKING:
    from fastmcp.server.openapi import OpenAPITool
    from fastmcp.tools.tool import ToolResult
    from fastmcp.utilities.openapi import HTTPRoute


class ToolManager:
    def __init__(self):
        self.tools: dict[str, "OpenAPITool"] = {}

    async def get_tool(self, tool_name: str) -> Optional["OpenAPITool"]:
        if tool_name not in self.tools:
            return None
        tool = self.tools[tool_name]
        return tool

    async def get_tools(self) -> dict[str, "OpenAPITool"]:
        return self.tools

    async def call_tool(self, key: str, arguments: dict[str, any]) -> "ToolResult":
        from fastmcp.tools.tool import ToolResult
        tool = await self.get_tool(key)
        if tool is None:
            return ToolResult(None, None)
        try:
            return await tool.run(arguments)
        except Exception as e:
            raise build_error(
                StatusCode.TOOL_OPENAPI_CLIENT_EXECUTION_ERROR, cause=e, reason=f"call tool {key} failed: {e}"
            ) from e


class OpenApiClient(McpClient):
    """
    example:
    import asyncio
    from openjiuwen.core.utils.tool.mcp.openapi_client import OpenApiClient


    async def main():
        client = OpenApiClient("openapi.json,openapi.yaml", "test")
        # 执行转换流程
        _ = await client.connect()

        tools = await client.list_tools()
        print("Available tools:", [t.name for t in tools])

        items = await client.call_tool("list_items",{})
        print("list_items", items)

        _ = client.disconnect()

    asyncio.run(main())
    """
    __client_name__ = "openapi"

    def __init__(self, server_config: McpServerConfig):
        super().__init__(server_config)
        self._director = None
        self._spec = None
        self.openapi_spec = None
        self._name = server_config.server_name
        self._client = httpx.AsyncClient()
        self._tool_manager = ToolManager()
        self._used_names: defaultdict[str, int] = defaultdict(int)
        self._server_path = server_config.server_path

    def _generate_tool_name(self, route: "HTTPRoute") -> str:
        if route.operation_id:
            name = route.operation_id.split("__")[0]
        else:
            name = route.summary or f"{route.method}_{route.path}"

        # Truncate to 64 characters maximum
        if len(name) > 64:
            name = name[:64]

        return name

    def _get_unique_name(self, name: str) -> str:
        self._used_names[name] = self._used_names.get(name, 0) + 1

        if self._used_names.get(name, 0) == 1:
            return name
        else:
            new_name = f"{name}_{self._used_names[name]}"
            logger.debug(
                f"tool_ame collision: '{name}' already used,using '{new_name}' instead. "
            )

        return new_name

    def _create_openapi_tool(
            self,
            http_route: "HTTPRoute",
            original_name: str,
            http_tags: set[str],
            timout: float,
    ):
        from fastmcp.utilities.openapi import (
            extract_output_schema_from_responses,
            format_simple_description,
        )
        from fastmcp.server.openapi import OpenAPITool
        
        tool_instruction = format_simple_description(
            request_body=http_route.request_body,
            base_description=(http_route.description or
                              http_route.summary or
                              f"Executes {http_route.method} {http_route.path}"
                              ),
            parameters=http_route.parameters,
        )

        output_schema = extract_output_schema_from_responses(
            http_route.responses,
            http_route.response_schemas,
            http_route.openapi_version,
        )

        generated_name = self._get_unique_name(original_name)

        openapi_tool = OpenAPITool(
            name=self._get_unique_name(original_name),
            route=http_route,
            client=self._client,
            director=self._director,
            description=tool_instruction,
            parameters=http_route.flat_param_schema,
            output_schema=output_schema,
            tags=set(http_route.tags or []) | http_tags,
            timeout=timout,
        )

        # add tool to tool_manager
        self._tool_manager.tools[generated_name] = openapi_tool

    async def connect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        from fastmcp.utilities.openapi.director import RequestDirector
        from fastmcp.utilities.openapi import parse_openapi_to_http_routes
        
        files = self._server_path.split(",")
        for file_path in files:
            self.openapi_spec = await load_conf(file_path)

            try:
                self._spec = SchemaPath.from_dict(self.openapi_spec)
                self._director = RequestDirector(self._spec)
            except Exception as e:
                logger.error(f"Invalid openapi spec: {e}")
                return False

            http_routes = parse_openapi_to_http_routes(self.openapi_spec)

            # start to convert openapi to mcp_tool
            for route in http_routes:
                tool_name = self._generate_tool_name(route)
                tool_tags = set(route.tags or [])
                self._create_openapi_tool(route, tool_name, tool_tags, timeout)

        return True

    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        await self._client.aclose()
        return True

    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[McpToolCard]:
        tools = await self._tool_manager.get_tools()
        tools_info = []

        for tool_name, tool in tools.items():
            tools_info.append(McpToolCard(
                name=tool_name,
                server_name=self._name,
                description=getattr(tool, "description", ""),
                input_params=getattr(tool, "inputSchema", {}),
            )
            )
        return tools_info

    async def call_tool(self, tool_name, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        try:
            tool_result = await self._tool_manager.call_tool(tool_name, arguments)
            return tool_result.to_mcp_result()
        except Exception as e:
            raise build_error(StatusCode.TOOL_OPENAPI_CLIENT_EXECUTION_ERROR, cause=e, reason=e)

    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Any:
        tool = await self._tool_manager.get_tool(tool_name)
        return McpToolCard(
            name=tool_name,
            server_name=self._name,
            description=getattr(tool, "description", ""),
            input_params=getattr(tool, "inputSchema", {}),
        )


async def load_conf(file: Union[str, Path]) -> Dict[str, Any]:
    path = Path(file).expanduser().resolve()
    if not path.exists():
        raise build_error(StatusCode.TOOL_OPENAPI_CLIENT_EXECUTION_ERROR, reason=f"path not exists: {path}")
    if not path.is_file():
        raise build_error(StatusCode.TOOL_OPENAPI_CLIENT_EXECUTION_ERROR, reason=f"the {path} is not a file")
    if path.is_symlink():
        raise build_error(StatusCode.TOOL_OPENAPI_CLIENT_EXECUTION_ERROR, reason=f"symbolic link not allowed:{path}")

    suffix = path.suffix.lower()

    async with await anyio.open_file(path, mode="r", encoding="utf-8") as f:
        content = await f.read()

    def parse():
        try:
            if suffix == ".json":
                return json.loads(content)
            elif suffix in {".yaml", ".yml"}:
                return yaml.safe_load(content)
            else:
                raise build_error(StatusCode.TOOL_OPENAPI_CLIENT_EXECUTION_ERROR,
                                  reason=f"only supports. json/. yaml/. yml, "
                                         f"current extension: {suffix}")
        except Exception as e:
            raise build_error(StatusCode.TOOL_OPENAPI_CLIENT_EXECUTION_ERROR, cause=e, reason=e)

    data = await anyio.to_thread.run_sync(parse)

    if not isinstance(data, dict):
        raise build_error(StatusCode.TOOL_OPENAPI_CLIENT_EXECUTION_ERROR,
                          reason=f"only support dict type: {type(data)}")
    return data