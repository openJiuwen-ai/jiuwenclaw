# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
prompt optimization evaluators
"""
import random
from typing import List, Optional, Dict

import asyncio

from openjiuwen.core.single_agent.legacy import LegacyBaseAgent as BaseAgent
from openjiuwen.core.common.logging import prompt_logger, LogEventType
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.core.operator.legacy.llm_call.base import LLMCall
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.dev_tools.tune.base import Case, TuneConstant, EvaluatedCase
from openjiuwen.dev_tools.tune.utils import TuneUtils
from openjiuwen.dev_tools.tune.optimizer.base import BaseOptimizer


EXAMPLE_SELECTION_TEMPLATE = PromptTemplate(content="""作为提示词优化专家,我的任务是帮助代理高效且成功地完成任务。
当前任务描述:
[任务描述]
{{task_description}}
请从以下回答错误的数据或正确但又代表性的示例集合中选择最具代表性的{{num_examples}}个示例,以解决上述任务中的任何问题。
当前的错误示例集是
{{examples}}

选择出最具代表性示例集的标号,用列表形式输出,输出格式为:
```list
[索引1, 索引2,...]
```
例如输出3个示例:
```list
[0, 2, 4]
```
1. 输出的索引列表必须满足{{num_examples}}个
2. 输出必须被'```list```'包裹

[请选择示例]
""")


class ExampleOptimizer(BaseOptimizer):
    def __init__(self,
                 model_config: ModelRequestConfig,
                 model_client_config: ModelClientConfig,
                 parameters: Optional[Dict[str, LLMCall]] = None,
                 num_examples: int = TuneConstant.DEFAULT_EXAMPLE_NUM,
                 ):
        super().__init__(parameters)
        self._model = Model(model_client_config, model_config)
        if num_examples < TuneConstant.MIN_EXAMPLE_NUM or num_examples > TuneConstant.MAX_EXAMPLE_NUM:
            raise build_error(
                StatusCode.TOOLCHAIN_OPTIMIZER_PARAM_ERROR,
                error_msg=f"num_examples should be between {TuneConstant.MIN_EXAMPLE_NUM} "
                            f"and {TuneConstant.MAX_EXAMPLE_NUM}"
            )
        self._num_examples = num_examples

    @property
    def num_examples(self) -> int:
        return self._num_examples

    def _backward(self,
                  evaluated_cases: List[EvaluatedCase],
                  ):
        """optimize instruction"""
        if self._num_examples <= 0:
            prompt_logger.info(
                "skip do example optimization.",
                event_type=LogEventType.AGENT_RESPONSE,
                input_data=evaluated_cases
            )
            return

        for name, param in self._parameters.items():
            selected_examples = self._select_best_examples(
                param.llm_call.get_system_prompt(),
                param.llm_call.get_user_prompt(),
                evaluated_cases
            )
            if not param.llm_call.get_freeze_system_prompt():
                param.set_gradient(
                    "system_prompt",
                    TuneUtils.convert_cases_to_examples(selected_examples)
                )
            if not param.llm_call.get_freeze_user_prompt():
                param.set_gradient(
                    "user_prompt",
                    TuneUtils.convert_cases_to_examples(selected_examples)
                )

    def _update(self) -> Optional[BaseAgent]:
        for name, param in self._parameters.items():
            if not param.llm_call.get_freeze_user_prompt():
                optimized_prompt = self._format_prompt(
                    param.llm_call.get_user_prompt(), param.get_gradient("user_prompt")
                )
                param.llm_call.update_user_prompt(optimized_prompt)
            elif not param.llm_call.get_freeze_system_prompt():
                optimized_prompt = self._format_prompt(
                    param.llm_call.get_system_prompt(), param.get_gradient("system_prompt")
                )
                param.llm_call.update_system_prompt(optimized_prompt)

    def init_examples(self, evaluated_cases: List[EvaluatedCase]):
        """prepare few-shot examples"""
        pre_select_examples = self._sample_example(self._num_examples, evaluated_cases)
        for name, param in self._parameters.items():
            if not param.llm_call.get_freeze_system_prompt():
                param.set_gradient(
                    "system_prompt",
                    TuneUtils.convert_cases_to_examples(pre_select_examples)
                )
            if not param.llm_call.get_freeze_user_prompt():
                param.set_gradient(
                    "user_prompt",
                    TuneUtils.convert_cases_to_examples(pre_select_examples)
                )

    @staticmethod
    def _format_prompt(prompt: PromptTemplate, gradient: str):
        content = TuneUtils.get_content_string_from_template(prompt)
        if gradient is None:
            return content
        return "\n".join([content, gradient])

    def format_prompt(self, prompt: PromptTemplate, gradient: str) -> str:
        return self._format_prompt(prompt, gradient)

    def _sample_example(self, num_examples: int, evaluated_cases: List[EvaluatedCase]) -> Optional[List[Case]]:
        """sample example"""
        dataset = evaluated_cases
        error_cases = self._get_bad_cases(evaluated_cases)
        if num_examples >= len(dataset):
            return [eval_case.case for eval_case in dataset]
        sampled_examples = []
        if error_cases:
            num_error_examples = min(num_examples, len(error_cases))
            sampled_examples.extend(random.sample(error_cases, num_error_examples))

        if len(sampled_examples) < num_examples:
            num_remaining_examples = num_examples - len(sampled_examples)
            remaining_examples = [ex for ex in dataset if ex not in sampled_examples]
            sampled_examples.extend(random.sample(remaining_examples, num_remaining_examples))
        else:
            sampled_examples = random.sample(sampled_examples, num_examples)

        return [eval_case.case for eval_case in sampled_examples]

    def _fill_missing_example(self,
                              selected_examples: List[Case],
                              evaluated_cases: List[EvaluatedCase],
                              ):
        num_to_select = min(self._num_examples, len(evaluated_cases))
        num_examples_to_fill = num_to_select - len(selected_examples)
        remaining_cases = [case.case for case in evaluated_cases
                           if case.case_id not in [case.case_id for case in selected_examples]]
        return selected_examples + random.sample(remaining_cases, num_examples_to_fill)

    def _select_best_examples(self,
                              system_prompt: PromptTemplate,
                              user_prompt: PromptTemplate,
                              evaluated_cases: List[EvaluatedCase],
                              ) -> List[Case]:
        """select best examples"""
        pre_selected_examples = self._sample_examples_from_cases(evaluated_cases)
        if len(pre_selected_examples) <= self._num_examples:
            return pre_selected_examples
        examples_string = "\n".join(
            f"index: {i}\n"
            f"question: {example.inputs}\n"
            f"assistant answer: {example.label}"
            for i, example in enumerate(pre_selected_examples)
        )

        messages = EXAMPLE_SELECTION_TEMPLATE.format(
            dict(task_description=TuneUtils.get_content_string_from_template(system_prompt) + "\n" +
                                  TuneUtils.get_content_string_from_template(user_prompt),
                 num_examples=self._num_examples,
                 examples=examples_string)
        ).to_messages()

        try:
            response = asyncio.run(self._model.invoke(messages)).content
            selected_examples = self._extract_selected_examples_from_response(response, pre_selected_examples)
            if len(selected_examples) < self._num_examples:
                selected_examples = self._fill_missing_example(
                    selected_examples,
                    evaluated_cases,
                )
            return selected_examples

        except Exception as e:
            prompt_logger.warning(
                "Error occur while selecting best examples",
                event_type=LogEventType.AGENT_ERROR,
                exception=e
            )
            return self._sample_example(self._num_examples, evaluated_cases)

    def _sample_examples_from_cases(self,
                                    evaluated_cases: List[EvaluatedCase],
                                    ) -> List[Case]:
        if self._num_examples >= len(evaluated_cases):
            return [eval_case.case for eval_case in evaluated_cases]

        error_cases = self._bad_cases
        examples = [eval_case.case for eval_case in error_cases]
        if len(examples) > TuneConstant.DEFAULT_MAX_NUM_SAMPLE_ERROR_CASES:
            examples = random.sample(
                error_cases,
                TuneConstant.DEFAULT_MAX_NUM_SAMPLE_ERROR_CASES
            )
            return examples

        if len(examples) < min(self._num_examples, len(evaluated_cases)):
            examples = self._fill_missing_example(
                examples,
                evaluated_cases,
            )
        return examples

    def _extract_selected_examples_from_response(self, response: str, error_cases: List[Case]) -> List[Case]:
        best_example_list = TuneUtils.parse_list_from_llm_response(response)
        return [error_cases[index] for index in best_example_list[:self._num_examples]]