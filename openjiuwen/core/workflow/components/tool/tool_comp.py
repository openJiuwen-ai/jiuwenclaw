# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from dataclasses import dataclass
from typing import Union, Any, Optional

from pydantic import BaseModel, ConfigDict, Field, ValidationError

from openjiuwen.core.common.exception.errors import BaseError, build_error
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.security.exception_utils import ExceptionUtils
from openjiuwen.core.context_engine import ModelContext
from openjiuwen.core.foundation.tool import RestfulApi, Tool
from openjiuwen.core.graph.executable import Executable, Input, Output
from openjiuwen.core.session.node import Session
from openjiuwen.core.workflow.components.base import ComponentConfig
from openjiuwen.core.workflow.components.component import ComponentComposable, ComponentExecutable

DEFAULT_EXCEPTION_ERROR_CODE = -1


@dataclass
class ToolComponentConfig(ComponentConfig):
    tool_id: Optional[str] = None


class ToolComponentInput(BaseModel):
    model_config = ConfigDict(extra='allow')  # Allow any extra fields


# RestFul Res
ERR_CODE = "errCode"
ERR_MESSAGE = "errMessage"
RESTFUL_DATA = "data"


class ToolComponentOutput(BaseModel):
    error_code: int = Field(default=0, alias=ERR_CODE)
    error_message: str = Field(default="", alias=ERR_MESSAGE)
    data: Any = Field(default="", alias=RESTFUL_DATA)


class ToolExecutable(ComponentExecutable):

    def __init__(self, config: ToolComponentConfig):
        super().__init__()
        self._config = config
        self._tool: Union[Tool, None] = None

    @staticmethod
    def _validate_inputs(inputs) -> dict:
        try:
            return ToolComponentInput(**inputs).model_dump()
        except ValidationError as e:
            raise build_error(
                StatusCode.COMPONENT_TOOL_INPUT_PARAM_ERROR,
                error_msg=ExceptionUtils.format_validation_error(e),
                cause=e
            ) from e

    async def invoke(self, inputs: Input, session: Session, context: ModelContext) -> Output:
        if self._tool is None:
            raise build_error(
                StatusCode.COMPONENT_TOOL_EXECUTION_ERROR,
                error_msg="tool is not initialized"
            )
        tool_inputs = self._validate_inputs(inputs)

        try:
            response = await self._tool.invoke(tool_inputs, skip_inputs_validate=False, skip_none_value=True)
            response = self._post_process_tool_result(response)
        except Exception as e:
            if isinstance(e, BaseError):
                err_msg = e.message
                err_code = e.code
            else:
                err_msg = StatusCode.TOOL_EXECUTION_ERROR.errmsg.format(card=self._tool.card,
                                                                        reason=str(e) if e else "unknown exception")
                err_code = StatusCode.TOOL_EXECUTION_ERROR.code
            response = {ERR_MESSAGE: err_msg, ERR_CODE: err_code}
        return self._create_output(response)

    def set_tool(self, tool: Tool):
        self._tool = tool
        return self

    def _create_output(self, response: dict):
        return ToolComponentOutput(**response).model_dump()

    def _post_process_tool_result(self, tool_result):
        result = dict()
        if isinstance(self._tool, RestfulApi):
            result[RESTFUL_DATA] = tool_result.get("data", "")
            code = tool_result.get("code", -1)
            try:
                code = int(code)
            except (ValueError, TypeError):
                code = -1
            result[ERR_CODE] = StatusCode.SUCCESS.code if 200 <= code < 300 \
                else StatusCode.TOOL_EXECUTION_ERROR.code
            result[ERR_MESSAGE] = tool_result.get("message", "")
        else:
            if isinstance(tool_result, dict) and all(key in tool_result for key in ("code", "data", "message")):
                result[RESTFUL_DATA] = tool_result.get("data", "")
                result[ERR_CODE] = tool_result.get("code", StatusCode.TOOL_EXECUTION_ERROR.code)
                result[ERR_MESSAGE] = tool_result.get("message", "")
                return result
            result[RESTFUL_DATA] = tool_result
        return result


class ToolComponent(ComponentComposable):

    def __init__(self, config: ToolComponentConfig):
        super().__init__()
        self._config = config
        tool_id = self._config.tool_id
        if tool_id is not None:
            from openjiuwen.core.runner import Runner
            self._tool = Runner.resource_mgr.get_tool(tool_id=tool_id)

    def to_executable(self) -> Executable:
        if self._tool is None:
            raise build_error(
                StatusCode.COMPONENT_TOOL_INIT_FAILED,
                error_msg="tool component not bind a valid tool"
            )
        return ToolExecutable(self._config).set_tool(self._tool)

    def bind_tool(self, tool: Tool):
        self._tool = tool
        return self
