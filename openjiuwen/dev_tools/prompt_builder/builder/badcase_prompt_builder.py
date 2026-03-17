# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import re
from typing import Optional, List, AsyncGenerator, Literal

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import prompt_builder_logger, LogEventType
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, AssistantMessage

from openjiuwen.dev_tools.prompt_builder.base import BasePromptBuilder
from openjiuwen.dev_tools.prompt_builder.builder.utils import template_map, get_string_prompt, select_template
from openjiuwen.dev_tools.tune.base import EvaluatedCase, Case

MAX_CASES_LIMIT = 10


class BadCasePromptBuilder(BasePromptBuilder):
    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)
        self.template = select_template()

    async def build(self,
                    prompt: str | PromptTemplate,
                    cases: List[EvaluatedCase],
                    language: Literal["zh-CN", "en-US"] = "zh-CN",
                    ) -> Optional[str]:
        self.template = select_template(language)
        prompt = get_string_prompt(prompt)
        messages = await self._format_bad_case_template(prompt, cases)
        response = await self._model.invoke(messages)
        return response.content

    async def stream_build(self,
                           prompt: str | PromptTemplate,
                           cases: List[EvaluatedCase],
                           language: Literal["zh-CN", "en-US"] = "zh-CN",
                           ) -> AsyncGenerator:
        self.template = select_template(language)
        prompt = get_string_prompt(prompt)
        messages = await self._format_bad_case_template(prompt, cases)
        async for chunk in self._model.stream(messages):
            yield chunk.content

    async def _format_bad_case_template(self,
                                        prompt: str,
                                        cases: List[EvaluatedCase],
                                        ) -> str:
        feedback = await self._get_feedback_from_bad_case(prompt, cases)
        bad_case_optimize_template = self.template.PROMPT_BAD_CASE_OPTIMIZE_TEMPLATE
        messages = bad_case_optimize_template.format(
            dict(original_prompt=prompt,
                 feedback=feedback
                 )
        ).to_messages()
        return messages

    async def _get_feedback_from_bad_case(self, prompt: str, cases: List[EvaluatedCase]) -> Optional[str]:
        self._validate_input(prompt, cases)
        bad_case_string = self._build_bad_case_string(cases)
        analyze_template = self.template.PROMPT_BAD_CASE_ANALYZE_TEMPLATE
        messages = analyze_template.format(
            dict(original_prompt=prompt,
                 bad_cases=bad_case_string
                 )
        ).to_messages()
        response = await self._model.invoke(messages)
        feedback_summary = self._parse_feedback_summary(response)
        return feedback_summary

    def _parse_feedback_summary(self, response: AssistantMessage) -> Optional[str]:
        intent = re.findall(r"<intent>((?:(?!<intent>).)*?)</intent>", response.content, re.DOTALL)
        intent = [intent_text.strip() for intent_text in intent]
        if "false" in intent:
            prompt_builder_logger.warning(
                "Failed to get intent",
                event_type=LogEventType.AGENT_ERROR,
                input_data=response,
            )
        text_match = re.findall(r"<summary>((?:(?!</summary>).)*?)</summary>", response.content, re.DOTALL)
        parse_summary = text_match[-1].strip() if len(text_match) >= 1 else response.content
        return parse_summary

    def _build_bad_case_string(self, cases: List[EvaluatedCase]) -> Optional[str]:
        bad_case_template = self.template.FORMAT_BAD_CASE_TEMPLATE
        bad_case_string = "\n".join(
            bad_case_template.format(
                dict(question=str(case.case.inputs),
                     label=str(case.case.label),
                     answer=str(case.answer),
                     reason=case.reason)
            ).content
            for case in cases
        )
        return bad_case_string

    def _validate_input(self, prompt: str, cases: List[EvaluatedCase]):
        if prompt is None:
            raise build_error(
                StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"prompt cannot be None"
            )
        if not prompt.strip():
            raise build_error(
                StatusCode.TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"prompt cannot be empty"
            )
        if not cases:
            raise build_error(
                StatusCode.TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"The cases cannot be empty"
            )
        if len(cases) > MAX_CASES_LIMIT:
            raise build_error(
                StatusCode.TOOLCHAIN_BAD_CASE_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"The number of cases cannot exceed {MAX_CASES_LIMIT}"
            )
