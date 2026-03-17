# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from abc import ABC, abstractmethod
from typing import Literal, Optional, Dict, Any, AsyncIterator, List

from openjiuwen.core.foundation.tool import ToolCard
from openjiuwen.core.sys_operation.base import BaseOperation
from openjiuwen.core.sys_operation.result import ExecuteCodeResult, ExecuteCodeStreamResult


class BaseCodeOperation(BaseOperation, ABC):
    """Base code operation"""

    def list_tools(self) -> List[ToolCard]:
        method_names = [
            "execute_code",
            "execute_code_stream"
        ]
        return self._generate_tool_cards(method_names)

    @abstractmethod
    async def execute_code(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCodeResult:
        """
        Execute arbitrary code asynchronously.

        Args:
            code: Non-empty string containing the source code to execute (required positional argument).
            language: Programming language of the code. Strict type constraint to 'python' or 'javascript'.
            timeout: Maximum execution time in seconds. Defaults to 300 seconds (5 minutes).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            ExecuteCodeResult: Execution result.
        """
        pass

    @abstractmethod
    async def execute_code_stream(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        """
        Execute arbitrary code asynchronously, by streaming.

        Args:
            code: Non-empty string containing the source code to execute (required positional argument).
            language: Programming language of the code. Strict type constraint to 'python' or 'javascript'.
                Defaults to "python".
            timeout: Maximum execution time in seconds. Terminates the process if exceeded.
                Must be a positive integer. Defaults to 300 seconds (5 minutes).
            environment: Key-value dict of custom environment variables.
            options: Additional execution configuration options.

        Returns:
            AsyncIterator[ExecuteCodeStreamResult]: Streaming structured results.
        """
        pass
