# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Any, Dict, Optional, AsyncIterator

from openjiuwen.core.sys_operation.shell import BaseShellOperation
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.sandbox.run_config import SandboxRunConfig
from openjiuwen.core.sys_operation.sandbox.sandbox_mixin import BaseSandboxMixin
from openjiuwen.core.sys_operation.result import ExecuteCmdResult, ExecuteCmdStreamResult


@operation(name="shell", mode=OperationMode.SANDBOX, description="Sandbox shell execution operation")
class ShellOperation(BaseShellOperation, BaseSandboxMixin):
    """Sandbox mode shell operation"""

    def __init__(self, name: str, mode: OperationMode, description: str, run_config: SandboxRunConfig):
        super().__init__(name, mode, description, run_config)
        self._init_sandbox_context(run_config, op_type="shell")

    async def execute_cmd(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCmdResult:
        raw = await self.invoke(
            "execute_cmd", command=command, cwd=cwd,
            timeout=timeout, environment=environment, options=options
        )
        return raw if isinstance(raw, ExecuteCmdResult) else ExecuteCmdResult(**raw)

    async def execute_cmd_stream(
            self,
            command: str,
            *,
            cwd: Optional[str] = None,
            timeout: Optional[int] = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCmdStreamResult]:
        async for item in self.invoke_stream(
            "execute_cmd_stream", command=command, cwd=cwd,
            timeout=timeout, environment=environment, options=options
        ):
            yield ExecuteCmdStreamResult(**item) if isinstance(item, dict) else item
