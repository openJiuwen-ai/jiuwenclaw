# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import abstractmethod, ABC
from typing import Optional, Dict, Any, AsyncIterator, List

from openjiuwen.core.sys_operation.base import BaseOperation
from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.foundation.tool.utils.callable_schema_extractor import CallableSchemaExtractor
from openjiuwen.core.sys_operation.result import (
    ExecuteCmdResult, ExecuteCmdStreamResult
)


class BaseShellOperation(BaseOperation, ABC):
    """Base shell operation"""

    def list_tools(self) -> List[ToolCard]:
        method_names = [
            "execute_cmd",
            "execute_cmd_stream"
        ]
        return self._generate_tool_cards(method_names)

    @abstractmethod
    async def execute_cmd(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCmdResult:
        """
        Asynchronously execute a command(shell mode only).

        Args:
            command: Command to execute.
            cwd: Working directory for command execution (default: current directory).
            timeout: Command execution timeout in seconds (default: 300 seconds).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            ExecuteCmdResult: Execution result.
        """
        pass

    async def execute_cmd_stream(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        """
        Asynchronously execute a command streaming(shell mode only).

        Args:
            command: Command to execute.
            cwd: Working directory for command execution (default: current directory).
            timeout: Command execution timeout in seconds (default: 300 seconds).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            AsyncIterator[ExecuteCmdStreamResult]: Streaming structured results.
        """
        pass
