# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import copy
from typing import List, Optional, AsyncGenerator, Literal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import prompt_builder_logger, LogEventType
from openjiuwen.core.foundation.tool import ToolInfo
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig

from openjiuwen.dev_tools.prompt_builder.base import BasePromptBuilder
from openjiuwen.dev_tools.prompt_builder.builder.utils import get_string_prompt, template_map, select_template

META_TEMPLATE_NAME_PREFIX: str = "META_TEMPLATE_"


class MetaTemplateBuilder(BasePromptBuilder):
    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)
        self._meta_template_manager = dict()
        self.template = select_template()

    def get_meta_template(self, template_name: str) -> Optional[any]:
        return self._meta_template_manager.get(template_name)

    def pop_meta_template(self, template_name: str) -> Optional[any]:
        return self._meta_template_manager.pop(template_name, None)

    def register_meta_template(self, name: str, meta_template: str | PromptTemplate):
        template_name = f"{META_TEMPLATE_NAME_PREFIX}{name}"
        if isinstance(meta_template, str):
            template_to_reg = PromptTemplate(content=meta_template)
        elif isinstance(meta_template, PromptTemplate):
            template_to_reg = copy.deepcopy(meta_template)
        else:
            raise build_error(
                StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"failed to register meta-template: {name}"
            )
        self._meta_template_manager.update({template_name: template_to_reg})

    async def build(self,
                    prompt: str | PromptTemplate,
                    tools: Optional[List[ToolInfo]] = None,
                    template_type: Literal["general", "plan", "other"] = "general",
                    custom_template_name: Optional[str] = None,
                    language: Literal["zh-CN", "en-US"] = "zh-CN",
                    ) -> Optional[str]:
        self.template = select_template(language)
        prompt = get_string_prompt(prompt)
        self._is_valid_prompt(prompt, tools)
        messages = self._format_meta_template(prompt, tools, template_type, custom_template_name)
        response = await self._model.invoke(messages)
        if response is None:
            return None
        return response.content

    async def stream_build(self,
                           prompt: str | PromptTemplate,
                           tools: Optional[List[ToolInfo]] = None,
                           template_type: Literal["general", "plan", "other"] = "general",
                           custom_template_name: Optional[str] = None,
                           language: Literal["zh-CN", "en-US"] = "zh-CN",
                           ) -> AsyncGenerator:
        self.template = select_template(language)
        prompt = get_string_prompt(prompt)
        self._is_valid_prompt(prompt, tools)
        messages = self._format_meta_template(prompt, tools, template_type, custom_template_name)
        async for chunk in self._model.stream(messages):
            yield chunk.content

    def _format_meta_template(self,
                              prompt: str,
                              tools: Optional[List[ToolInfo]] = None,
                              template_type: Literal["general", "plan", "other"] = "general",
                              custom_template_name: Optional[str] = None
                              ) -> str:
        if template_type == "other":
            return self._format_custom_meta_template(custom_template_name, prompt, tools)
        else:
            return self._format_predefined_meta_template(template_type, prompt, tools)

    def _format_predefined_meta_template(self,
                                         template_type: str,
                                         prompt: str,
                                         tools: Optional[List[ToolInfo]] = None,
                                         ):
        if template_type == "plan":
            meta_system_template = self.template.PROMPT_BUILD_PLAN_META_SYSTEM_TEMPLATE
            meta_user_template = self.template.PROMPT_BUILD_PLAN_META_USER_TEMPLATE
        else:
            if template_type != "general":
                prompt_builder_logger.warning(
                    "Invalid template_type, using `general` instead",
                    event_type=LogEventType.AGENT_ERROR,
                    input_data=prompt,
                    metadata={"template_type": template_type}
                )
            meta_system_template = self.template.PROMPT_BUILD_GENERAL_META_SYSTEM_TEMPLATE
            meta_user_template = self.template.PROMPT_BUILD_GENERAL_META_USER_TEMPLATE

        messages = meta_system_template.to_messages()
        messages.extend(meta_user_template.format(
            dict(instruction=prompt, tools=str(tools))
        ).to_messages())
        return messages

    def _format_custom_meta_template(self,
                                     custom_meta_template_name: str,
                                     prompt: str,
                                     tools: Optional[List[ToolInfo]] = None
                                     ):
        if not custom_meta_template_name:
            raise build_error(
                StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR,
                error_msg="failed to get custom meta-template, please provide template name"
            )
        custom_meta_template_name = f"{META_TEMPLATE_NAME_PREFIX}{custom_meta_template_name}"
        custom_meta_template = self._meta_template_manager.get(custom_meta_template_name)
        if not custom_meta_template:
            raise build_error(
                StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"failed to get custom meta-template: {custom_meta_template_name}"
            )
        return custom_meta_template.format(
            dict(instruction=prompt, tools=str(tools))
        ).to_messages()

    def _is_valid_prompt(self, prompt: str, tools: List[ToolInfo]):
        if prompt is None:
            raise build_error(
                StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR,
                error_msg="prompt cannot be None"
            )
        if not prompt.strip():
            raise build_error(
                StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR,
                error_msg="prompt cannot be empty"
            )
        if tools and any(not isinstance(tool, ToolInfo) for tool in tools):
            raise build_error(
                StatusCode.TOOLCHAIN_META_TEMPLATE_EXECUTION_ERROR,
                error_msg="each tool must be an instance of ToolInfo"
            )
