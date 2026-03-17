# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from abc import abstractmethod
from typing import Any, List, Optional

from openjiuwen.core.foundation.tool.mcp.base import McpServerConfig, NO_TIMEOUT
from openjiuwen.core.common.clients import BaseClient


class McpClient(BaseClient):
    __client_type__ = "mcp"

    def __init__(self, config: McpServerConfig):
        super().__init__()
        self._server_path = config.server_path

    @abstractmethod
    async def connect(self, *, retry_times: int = 1, timeout: float = NO_TIMEOUT) -> bool:
        pass

    @abstractmethod
    async def disconnect(self, *, timeout: float = NO_TIMEOUT) -> bool:
        pass

    @abstractmethod
    async def list_tools(self, *, timeout: float = NO_TIMEOUT) -> List[Any]:
        pass

    @abstractmethod
    async def call_tool(self, tool_name, arguments: dict, *, timeout: float = NO_TIMEOUT) -> Any:
        pass

    @abstractmethod
    async def get_tool_info(self, tool_name: str, *, timeout: float = NO_TIMEOUT) -> Optional[Any]:
        pass

    async def close(self) -> bool:
        return await self.disconnect()
