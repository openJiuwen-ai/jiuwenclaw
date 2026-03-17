# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.llm import BaseMessage
import openjiuwen.dev_tools.prompt_builder.builder.prompt_zh as TEMPLATE_ZH
import openjiuwen.dev_tools.prompt_builder.builder.prompt_en as TEMPLATE_EN


template_map = {
    'zh-CN': TEMPLATE_ZH,
    'en-US': TEMPLATE_EN
}


def select_template(language: str = 'zh-CN'):
    return template_map.get(language, TEMPLATE_ZH)


def get_string_prompt(prompt: str | PromptTemplate):
    if isinstance(prompt, str):
        return prompt
    elif isinstance(prompt, PromptTemplate):
        if isinstance(prompt.content, str):
            return prompt.content
        elif isinstance(prompt.content, list) and all(isinstance(item, BaseMessage) for item in prompt.content):
            return "\n".join(str(msg.content) for msg in prompt.content)
        else:
            return "\n".join("\n".join(item.values()) for item in prompt.content)
    else:
        raise build_error(StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                                error_msg=f"Prompt type {str(type(prompt))} is not supported")
