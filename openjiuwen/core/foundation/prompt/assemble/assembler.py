# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from typing import Union, List

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm import BaseMessage
from openjiuwen.core.foundation.prompt.assemble.variables.textable import TextableVariable
from openjiuwen.core.foundation.prompt.assemble.variables.dictable import DictableVariable
from openjiuwen.core.foundation.prompt.assemble.variables.variable import Variable


class PromptAssembler:
    """class for creating prompt based on a given prompt template"""

    def __init__(self,
                 prompt_template_content: Union[List[BaseMessage], str],
                 placeholder_prefix: str = "{{",
                 placeholder_suffix: str = "}}",
                 **variables):
        self.template_content = prompt_template_content
        self.placeholder_prefix = placeholder_prefix
        self.placeholder_suffix = placeholder_suffix
        self.template_formatter: List[Variable] = self._get_formatter_list()
        self.variables = self._get_variables_with_verify(variables)

    @property
    def input_keys(self) -> List[str]:
        """Get the list of argument names for updating all the variables"""
        keys = []
        for variable in self.variables.values():
            keys.extend(variable.input_keys)
        return list(set(keys))

    def _get_formatter_list(self):
        """get prompt template content formatter"""
        template_formatter_list = []
        if isinstance(self.template_content, str):
            template_formatter_list.append(
                TextableVariable(
                    self.template_content,
                    name="__inner__",
                    prefix=self.placeholder_prefix,
                    suffix=self.placeholder_suffix
                )
            )
            return template_formatter_list
        else:
            for msg in self.template_content:
                if isinstance(msg, BaseMessage):
                    if isinstance(msg.content, str):
                        template_formatter_list.append(
                            TextableVariable(
                                msg.content,
                                name="__inner__",
                                prefix=self.placeholder_prefix,
                                suffix=self.placeholder_suffix
                            )
                        )
                    elif isinstance(msg.content, list) and msg.content and isinstance(msg.content[0], dict):
                        template_formatter_list.append(
                            DictableVariable(
                                msg.content,
                                name="__inner__",
                                prefix=self.placeholder_prefix,
                                suffix=self.placeholder_suffix
                            )
                        )
                    else:
                        template_formatter_list.append(None)
                    continue
        return template_formatter_list

    def _get_variables_with_verify(self, variables):
        """verify input variables and summarize with prompt template content variables"""
        input_keys = []
        for formatter in self.template_formatter:
            if not formatter:
                continue
            input_keys.extend(formatter.input_keys)
        input_keys = list(set(input_keys))
        for name, variable in variables.items():
            if name not in input_keys:
                raise build_error(
                    StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED,
                    error_msg=f"variable {name} is not defined in the promptTemplate"
                )
            if not isinstance(variable, Variable):
                raise build_error(
                    StatusCode.PROMPT_ASSEMBLER_VARIABLE_INIT_FAILED,
                    error_msg=f"variable {name} must be instantiated as a `variable` object"
                )
        for placeholder in input_keys:
            if placeholder in variables:
                variables[placeholder].name = placeholder
            else:
                placeholder_str = f"{self.placeholder_prefix}{placeholder}{self.placeholder_suffix}"
                variables[placeholder] = TextableVariable(
                    name=placeholder,
                    text=placeholder_str,
                    prefix=self.placeholder_prefix,
                    suffix=self.placeholder_suffix
                )
        return variables

    def prompt_assemble(self, **kwargs) -> Union[str, List[dict]]:
        """Update the variables and format the prompt template into a string-type or message-type prompt"""
        kwargs = {k: v for k, v in kwargs.items() if v is not None and k in self.input_keys}
        all_kwargs = {}
        for k in self.input_keys:
            if k not in kwargs:
                # replace placeholder with initial content if not exist
                all_kwargs[k] = f"{self.placeholder_prefix}{k}{self.placeholder_suffix}"
        all_kwargs.update(**kwargs)
        self._update(**all_kwargs)
        return self._format()

    def _update(self, **kwargs) -> None:
        """Update the variables based on the arguments passed in as key-value pairs"""
        missing_keys = set(self.input_keys) - set(kwargs.keys())
        if missing_keys:
            raise build_error(
                StatusCode.PROMPT_ASSEMBLER_TEMPLATE_PARAM_ERROR,
                error_msg=f"missing keys for updating the prompt assembler: {list(missing_keys)}"
            )
        unexpected_keys = set(kwargs.keys()) - set(self.input_keys)
        if unexpected_keys:
            raise build_error(
                StatusCode.PROMPT_ASSEMBLER_TEMPLATE_PARAM_ERROR,
                error_msg=f"unexpected keys for updating the prompt assembler: {list(unexpected_keys)}"
            )
        for variable in self.variables.values():
            input_kwargs = {k: v for k, v in kwargs.items() if k in variable.input_keys}
            variable.eval(**input_kwargs)

    def _format(self) -> Union[str, List[BaseMessage]]:
        """Substitute placeholders in the prompt template with variables values and get formatted prompt."""
        format_kwargs = {var.name: var.value for var in self.variables.values()}
        for idx, formatter in enumerate(self.template_formatter):
            if not formatter:
                continue
            formatted_prompt = formatter.eval(**format_kwargs)
            if isinstance(self.template_content, str):
                self.template_content = formatted_prompt
                break
            else:
                self.template_content[idx].content = formatted_prompt
        return self.template_content