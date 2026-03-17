# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import inspect
from functools import wraps
from typing import Callable, AsyncIterator

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.utils.schema_utils import SchemaUtils
from openjiuwen.core.foundation.tool.base import Tool, ToolCard, Input, Output
from openjiuwen.core.runner.callback import trigger
from openjiuwen.core.runner.callback.events import ToolCallEvents


def support_args_param(arg_param_name: str, parameters, func: Callable) -> Callable:
    @wraps(func)
    def wrapper(**kwargs):
        if arg_param_name in kwargs:
            params_dict = kwargs
            positional_args = []
            keyword_args = {}
            args_list = params_dict.get(arg_param_name, [])
            used_params = set()
            for i, (param_name, param) in enumerate(parameters.items()):
                if param.kind == inspect.Parameter.VAR_POSITIONAL:
                    used_params.add(param_name)
                    continue
                if param_name in params_dict:
                    if param.kind in (inspect.Parameter.POSITIONAL_ONLY,
                                      inspect.Parameter.POSITIONAL_OR_KEYWORD):
                        used_params.add(param_name)
                        if param.default == inspect.Parameter.empty:
                            positional_args.append(params_dict[param_name])
                    elif param.kind is not inspect.Parameter.KEYWORD_ONLY:
                        used_params.add(param_name)
                        keyword_args[param_name] = params_dict[param_name]
            if len(used_params) < len(params_dict):
                for param_name, param in params_dict.items():
                    if param_name not in used_params:
                        keyword_args[param_name] = param
            positional_args.extend(args_list)
            return func(*positional_args, **keyword_args)
        return func(**kwargs)

    return wrapper


class LocalFunction(Tool):
    def __init__(self, card: ToolCard, func: Callable):
        super().__init__(card)
        if func is None:
            raise build_error(StatusCode.TOOL_LOCAL_FUNCTION_FUNC_NOT_SUPPORTED, card=self._card)

        sig = inspect.signature(func)
        parameters = sig.parameters
        args_names = [p.name for p in parameters.values() if p.kind == inspect.Parameter.VAR_POSITIONAL]
        arg_param_name = None
        if args_names:
            arg_param_name = args_names[0]
        if args_names:
            self._func = support_args_param(arg_param_name, parameters, func)
        else:
            self._func = func

    async def invoke(self, inputs: Input, **kwargs) -> Output:
        if self.card.input_params is not None:
            await trigger(
                ToolCallEvents.TOOL_PARSE_STARTED,
                tool_name=self.card.name, tool_id=self.card.id,
                raw_inputs=inputs, schema=self._card.input_params)
            inputs = SchemaUtils.format_with_schema(inputs,
                                                    self._card.input_params,
                                                    skip_none_value=kwargs.get("skip_none_value", False),
                                                    skip_validate=kwargs.get("skip_inputs_validate", False))
            await trigger(
                ToolCallEvents.TOOL_PARSE_FINISHED,
                tool_name=self.card.name, tool_id=self.card.id,
                formatted_inputs=inputs)
        if inspect.isgeneratorfunction(self._func) or inspect.isasyncgenfunction(self._func):
            raise build_error(StatusCode.TOOL_LOCAL_FUNCTION_EXECUTION_ERROR, method="invoke",
                              reason="func can not be generator", card=self._card)
        if inspect.iscoroutinefunction(self._func):
            res = await self._func(**inputs)
        else:
            res = self._func(**inputs)
        return res

    async def stream(self, inputs: Input, **kwargs) -> AsyncIterator[Output]:
        if self.card.input_params is not None:
            await trigger(
                ToolCallEvents.TOOL_PARSE_STARTED,
                tool_name=self.card.name, tool_id=self.card.id,
                raw_inputs=inputs, schema=self._card.input_params)
            inputs = SchemaUtils.format_with_schema(inputs, self._card.input_params,
                                                    skip_none_value=kwargs.get("skip_none_value", False),
                                                    skip_validate=kwargs.get("skip_inputs_validate"))
            await trigger(
                ToolCallEvents.TOOL_PARSE_FINISHED,
                tool_name=self.card.name, tool_id=self.card.id,
                formatted_inputs=inputs)
        if inspect.isasyncgenfunction(self._func):
            async for item in self._func(**inputs):
                yield item
        elif inspect.isgeneratorfunction(self._func):
            for item in self._func(**inputs):
                yield item
        else:
            raise build_error(StatusCode.TOOL_LOCAL_FUNCTION_EXECUTION_ERROR, method="stream",
                              reason="func is not generator", card=self._card)
