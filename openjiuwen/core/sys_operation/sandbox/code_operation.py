# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Any, Dict, Optional, Literal, AsyncIterator

from openjiuwen.core.sys_operation.code import BaseCodeOperation
from openjiuwen.core.sys_operation.registry import operation
from openjiuwen.core.sys_operation.base import OperationMode
from openjiuwen.core.sys_operation.sandbox.run_config import SandboxRunConfig
from openjiuwen.core.sys_operation.sandbox.sandbox_mixin import BaseSandboxMixin
from openjiuwen.core.sys_operation.result import ExecuteCodeResult, ExecuteCodeStreamResult


@operation(name="code", mode=OperationMode.SANDBOX, description="Sandbox code execution operation")
class CodeOperation(BaseCodeOperation, BaseSandboxMixin):
    """Sandbox mode code operation"""

    def __init__(self, name: str, mode: OperationMode, description: str, run_config: SandboxRunConfig):
        super().__init__(name, mode, description, run_config)
        self._init_sandbox_context(run_config, op_type="code")

    async def execute_code(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> ExecuteCodeResult:
        raw = await self.invoke(
            "execute_code", code=code, language=language,
            timeout=timeout, environment=environment, options=options
        )
        return raw if isinstance(raw, ExecuteCodeResult) else ExecuteCodeResult(**raw)

    async def execute_code_stream(
            self,
            code: str,
            *,
            language: Literal['python', 'javascript'] = "python",
            timeout: int = 300,
            environment: Optional[Dict[str, str]] = None,
            options: Optional[Dict[str, Any]] = None
    ) -> AsyncIterator[ExecuteCodeStreamResult]:
        async for item in self.invoke_stream(
            "execute_code_stream", code=code, language=language,
            timeout=timeout, environment=environment, options=options
        ):
            yield ExecuteCodeStreamResult(**item) if isinstance(item, dict) else item
