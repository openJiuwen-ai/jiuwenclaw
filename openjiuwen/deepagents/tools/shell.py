# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
from typing import Dict, Any, AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.foundation.tool.base import Tool
from openjiuwen.core.sys_operation import SysOperation
from openjiuwen.deepagents.prompts.sections.tools import build_tool_card
from openjiuwen.deepagents.tools.base_tool import ToolOutput


class BashTool(Tool):

    def __init__(self, operation: SysOperation, language: str = "cn"):
        super().__init__(build_tool_card("bash", "BashTool", language))
        self.operation = operation

    async def invoke(self, inputs: Dict[str, Any], **kwargs) -> ToolOutput:
        command = inputs.get("command")
        timeout = inputs.get("timeout", 30)

        res = await self.operation.shell().execute_cmd(command, timeout=timeout)
        if res.code != StatusCode.SUCCESS.code:
            return ToolOutput(success=False, error=res.message)

        exit_code = res.data.exit_code if res.data else -1
        return ToolOutput(
            success=(exit_code == 0),
            data={
                "stdout": res.data.stdout if res.data else "",
                "stderr": res.data.stderr if res.data else "",
                "exit_code": exit_code
            },
            error=res.data.stderr if res.data and exit_code != 0 else None
        )

    async def stream(self, inputs: Dict[str, Any], **kwargs) -> AsyncIterator[Any]:
        pass
