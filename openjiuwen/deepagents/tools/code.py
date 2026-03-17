# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from typing import Dict, Any, AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.tool.base import Tool, ToolCard
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.tools.base_tool import ToolOutput


class CodeTool(Tool):

    def __init__(self, operation: SysOperation):
        super().__init__(ToolCard(id="CodeTool", name="code", description="执行代码（Python 或 JavaScript）。"))
        self.operation = operation
        self.card.input_params = {
            "type": "object",
            "properties": {
                "code": {"type": "string", "description": "要执行的代码"},
                "language": {"type": "string", "description": "编程语言，支持 python 或 javascript，默认 python"},
                "timeout": {"type": "integer", "description": "超时时间（秒），默认 300"},
            },
            "required": ["code"]
        }

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        code = inputs.get("code")
        language = inputs.get("language", "python")
        timeout = inputs.get("timeout", 300)

        res = await self.operation.code().execute_code(code, language=language, timeout=timeout)
        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)

        return ToolOutput(
            success=(res.data.exit_code == 0) if res.data else False,
            data={
                "stdout": res.data.stdout if res.data else "",
                "stderr": res.data.stderr if res.data else "",
                "exit_code": res.data.exit_code if res.data else -1
            },
            error=res.data.stderr if res.data and res.data.exit_code != 0 else None
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass
