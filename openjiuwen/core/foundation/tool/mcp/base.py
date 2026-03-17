# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import uuid
from typing import Any, AsyncIterator, Dict

from pydantic import Field, BaseModel

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.tool.base import Tool, ToolCard, Input, Output
from openjiuwen.core.foundation.tool.schema import McpToolInfo
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import ToolCallEvents

NO_TIMEOUT = -1


class McpServerConfig(BaseModel):
    server_id: str = Field(default_factory=lambda: uuid.uuid4().hex)
    server_name: str
    server_path: str
    client_type: str = 'sse'
    params: Dict[str, Any] = Field(default_factory=dict)
    auth_headers: dict = Field(default_factory=dict)
    auth_query_params: Dict[str, str] = Field(default_factory=dict)


class McpToolCard(ToolCard):
    server_name: str
    server_id: str = ''

    def tool_info(self):
        return McpToolInfo(name=self.name, description=self.description, parameters=self.input_params,
                           server_name=self.server_name)


class MCPTool(Tool):
    """MCP Tool class that wraps MCP server tools for LLM modules"""

    def __init__(self, mcp_client: Any, tool_info: McpToolCard):  # McpClient or its subclasses
        """
        Initialize MCP Tool

        Args:
            mcp_client: Instance of McpToolClient or its subclasses
            tool_name: Name of the MCP tool
            server_name: Name of the MCP server (for logging and identification)
        """
        super().__init__(tool_info)
        if mcp_client is None:
            raise build_error(StatusCode.TOOL_MCP_CLIENT_NOT_SUPPORTED, card=self._card)
        self._mcp_client = mcp_client

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        raise build_error(StatusCode.TOOL_STREAM_NOT_SUPPORTED, card=self._card)

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        try:
            # Prepare arguments for MCP tool call
            arguments = inputs if isinstance(inputs, dict) else {}
            if self._card.input_params is not None:
                await trigger(
                    ToolCallEvents.TOOL_PARSE_STARTED,
                    tool_name=self.card.name, tool_id=self.card.id,
                    raw_inputs=inputs, schema=self._card.input_params)
                arguments = SchemaUtils.format_with_schema(inputs, self._card.input_params,
                                                           skip_none_value=kwargs.get("skip_none_value", False),
                                                           skip_validate=kwargs.get("skip_inputs_validate", False))
                await trigger(
                    ToolCallEvents.TOOL_PARSE_FINISHED,
                    tool_name=self.card.name, tool_id=self.card.id,
                    formatted_inputs=arguments)

            result = await self._mcp_client.call_tool(tool_name=self._card.name, arguments=arguments)
            return {"result": result}

        except Exception as e:
            raise build_error(StatusCode.TOOL_MCP_EXECUTION_ERROR, cause=e, reason=str(e), method="invoke",
                              card=self._card)
