# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Optional, Dict, Any, AsyncIterator

from openjiuwen.core.sys_operation.shell import BaseShellOperation
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.result import (
    ExecuteCmdResult, ExecuteCmdStreamResult
)


@operation(name="shell", mode=OperationMode.SANDBOX, description="sandbox shell operation")
class ShellOperation(BaseShellOperation):
    """Shell operation"""

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
        raise NotImplementedError("Shell operation sandbox mode is not implemented yet.")

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
        raise NotImplementedError("Shell operation sandbox mode is not implemented yet.")
