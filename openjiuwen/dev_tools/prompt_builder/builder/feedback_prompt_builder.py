# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import json
import re
from typing import Optional, Literal, List, AsyncGenerator

from openjiuwen.dev_tools.prompt_builder.base import BasePromptBuilder
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import prompt_builder_logger, LogEventType
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, BaseMessage
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.dev_tools.prompt_builder.builder.utils import get_string_prompt, select_template

INSERT_STR: str = "[用户要插入的位置]"
MODE_GENERAL: str = "general"
MODE_SELECT: str = "select"
MODE_INSERT: str = "insert"
JSON_STRING_MAX_LENGTH: int = 10000


class FeedbackPromptBuilder(BasePromptBuilder):
    def __init__(self, model_config: ModelRequestConfig, model_client_config: ModelClientConfig):
        super().__init__(model_config, model_client_config)
        self.template = select_template()

    async def build(self,
                    prompt: str | PromptTemplate,
                    *,
                    feedback: str,
                    mode: Literal[MODE_GENERAL, MODE_INSERT, MODE_SELECT] = MODE_GENERAL,
                    start_pos: Optional[int] = None,
                    end_pos: Optional[int] = None,
                    language: Literal["zh-CN", "en-US"] = "zh-CN",
                    ) -> Optional[str]:
        self.template = select_template(language)
        prompt = get_string_prompt(prompt)
        self._is_valid_prompt(prompt, feedback)
        messages = await self._format_feedback_template(prompt, feedback, mode, start_pos, end_pos)
        response = await self._model.invoke(messages)
        if response is None:
            return None
        return response.content

    async def stream_build(self,
                           prompt: str | PromptTemplate,
                           *,
                           feedback: str,
                           mode: Literal[MODE_GENERAL, MODE_INSERT, MODE_SELECT] = MODE_GENERAL,
                           start_pos: Optional[int] = None,
                           end_pos: Optional[int] = None,
                           language: Literal["zh-CN", "en-US"] = "zh-CN",
                           ) -> AsyncGenerator:
        self.template = select_template(language)
        prompt = get_string_prompt(prompt)
        self._is_valid_prompt(prompt, feedback)
        messages = await self._format_feedback_template(prompt, feedback, mode, start_pos, end_pos)
        async for chunk in self._model.stream(messages):
            yield chunk.content

    async def _format_feedback_template(self,
                                        prompt: str,
                                        feedback: str,
                                        mode: Literal[MODE_GENERAL, MODE_INSERT, MODE_SELECT] = MODE_GENERAL,
                                        start_pos: Optional[int] = None,
                                        end_pos: Optional[int] = None,
                                        ) -> List[BaseMessage]:

        if mode == MODE_INSERT:
            return await self._format_feedback_template_insert(prompt, feedback, start_pos)
        elif mode == MODE_SELECT:
            return await self._format_feedback_template_select(prompt, feedback, start_pos, end_pos)
        else:
            if mode != MODE_GENERAL:
                prompt_builder_logger.warning(
                    "Invalid mode, using `general` instead",
                    event_type=LogEventType.AGENT_ERROR,
                    input_data=prompt,
                    metadate={"mode": mode}
                )
            return self._format_feedback_template_general(prompt, feedback)

    def _format_feedback_template_general(self,
                                         prompt: str,
                                         feedback: str,
                                         ) -> List[BaseMessage]:
        feedback_general_template = self.template.PROMPT_FEEDBACK_GENERAL_TEMPLATE
        messages = feedback_general_template.format(
            dict(original_prompt=prompt,
                 suggestion=feedback
                 )
        ).to_messages()
        return messages

    async def _format_feedback_template_insert(self,
                                               prompt: str,
                                               feedback: str,
                                               start_pos: Optional[int] = None,
                                               ) -> List[BaseMessage]:
        self._is_index_within_bounds(prompt, MODE_INSERT, start_pos)
        optimized_feedback = await self._is_feedback_valid(prompt, feedback)
        tagged_prompt = self._insert_sting(prompt, start_pos)
        feedback_insert_template = self.template.PROMPT_FEEDBACK_INSERT_TEMPLATE
        messages = feedback_insert_template.format(
            dict(original_prompt=tagged_prompt,
                 suggestion=optimized_feedback
                 )
        ).to_messages()
        return messages

    async def _format_feedback_template_select(self,
                                         prompt: str,
                                         feedback: str,
                                         start_pos: Optional[int] = None,
                                         end_pos: Optional[int] = None,
                                         ) -> List[BaseMessage]:
        self._is_index_within_bounds(prompt, MODE_SELECT, start_pos, end_pos)
        optimized_feedback = await self._is_feedback_valid(prompt, feedback)
        prompt_to_modify = prompt[start_pos:end_pos]
        feedback_select_template = self.template.PROMPT_FEEDBACK_SELECT_TEMPLATE
        messages = feedback_select_template.format(
            dict(original_prompt=prompt,
                 suggestion=optimized_feedback,
                 pending_optimized_prompt=prompt_to_modify
                 )
        ).to_messages()
        return messages

    def _insert_sting(self,
                      prompt: str,
                      insert: Optional[int]
                      ) -> Optional[str]:
        return prompt[:insert] + INSERT_STR + prompt[insert:]

    async def _is_feedback_valid(self,
                                 prompt: str,
                                 feedback: str
                                 ) -> str:
        feedback_intent_template = self.template.PROMPT_FEEDBACK_INTENT_TEMPLATE
        messages = feedback_intent_template.format(
            dict(original_prompt=prompt,
                 feedbacks=feedback
                 )
        ).to_messages()
        feedback_message = await self._model.invoke(messages)
        try:
            intent, optimized_feedback = self._extract_intent_from_responses(feedback_message.content)
        except build_error:
            prompt_builder_logger.warning(
                "Intent recognition failed, using original feedback instead",
                event_type=LogEventType.LLM_CALL_ERROR,
                model_name=self._model
            )
            return feedback
        if not intent or not optimized_feedback.strip():
            prompt_builder_logger.warning(
                "Intent recognition failed, using original feedback instead",
                event_type=LogEventType.AGENT_ERROR,
                model_name=self._model
            )
            return feedback
        return optimized_feedback.strip()

    def _is_index_within_bounds(self, prompt: str, mode, start_pos: int, end_pos: Optional[int] = None) -> bool:
        if mode == MODE_SELECT:
            if not isinstance(start_pos, int) or not isinstance(end_pos, int):
                raise build_error(
                    StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                    error_msg=f"start_pos and end_pos must be provided for int type"
                )
            if start_pos is not None and end_pos is not None:
                if 0 <= start_pos < end_pos <= len(prompt):
                    return True
            raise build_error(
                StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"start_pos and end_pos must be provided for select mode. "
                            f"Additionally, they must satisfy the conditions: "
                            f"0 <= start_pos < end_pos <= len(prompt)."
            )
        elif mode == MODE_INSERT:
            if not isinstance(start_pos, int):
                raise build_error(
                    StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                    error_msg=f"start_pos must be provided for int type"
                )
            if start_pos is not None:
                if 0 <= start_pos <= len(prompt):
                    return True
            raise build_error(
                StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"start_pos must be provided for insert mode. "
                            f"Additionally, it must satisfy the conditions: "
                            f"0 <= start_pos <= len(prompt)."
            )
        return False

    def _is_valid_prompt(self, prompt: str, feedback: str):
        if prompt is None or feedback is None:
            raise build_error(
                StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"prompt or feedback cannot be None"
            )
        if not prompt.strip() or not feedback.strip():
            raise build_error(
                StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"prompt or feedback cannot be empty"
            )

    def _extract_intent_from_responses(self, input_json: str):
        pattern = rf"```json(.{{1,{JSON_STRING_MAX_LENGTH}}}?)```"
        try:
            match = re.search(pattern, input_json, re.DOTALL)
            if match:
                json_str = match.group(1).strip()
                parsed_json = json.loads(json_str)
                intent = parsed_json.get("intent", False) in ("true", True, "True")
                optimized_feedback = parsed_json.get("optimized_feedback", "").strip()
                return intent, optimized_feedback.strip()
            error = build_error(
                StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"no valid JSON string found"
            )
            return None, error

        except json.JSONDecodeError as e:
            error = build_error(
                StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"an error occurred while parsing JSON: {str(e)}",
                cause=e
            )
            return None, error

        except Exception as e:
            error = build_error(
                StatusCode.TOOLCHAIN_FEEDBACK_TEMPLATE_EXECUTION_ERROR,
                error_msg=f"an error occurred while parsing intent JSON from message: {str(e)}",
                cause=e
            )
            return None, error
