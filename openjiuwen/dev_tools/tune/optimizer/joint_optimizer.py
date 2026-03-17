# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""
prompt optimization evaluators
"""

import random
import copy
from typing import List, Dict, Optional

from openjiuwen.core.operator.legacy.llm_call.base import LLMCall
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.dev_tools.tune.utils import TuneUtils
from openjiuwen.dev_tools.tune.base import TuneConstant, EvaluatedCase
from openjiuwen.dev_tools.tune.optimizer.base import BaseOptimizer
from openjiuwen.dev_tools.tune.optimizer.instruction_optimizer import InstructionOptimizer
from openjiuwen.dev_tools.tune.optimizer.example_optimizer import ExampleOptimizer


class JointOptimizer(BaseOptimizer):
    def __init__(
            self,
            model_config: ModelRequestConfig,
            model_client_config: ModelClientConfig,
            parameters: Optional[Dict[str, LLMCall]] = None,
            num_examples: int = TuneConstant.DEFAULT_EXAMPLE_NUM,
            ):
        self._instruction_optimizer = InstructionOptimizer(
            model_config, model_client_config, copy.deepcopy(parameters)
        )
        self._example_optimizer = ExampleOptimizer(
            model_config, model_client_config, copy.deepcopy(parameters), num_examples
        )
        super().__init__(parameters)
        self._is_optimize_instruction: bool = True

    def bind_parameter(self, parameters: Dict[str, LLMCall]):
        super().bind_parameter(parameters)
        self._example_optimizer.bind_parameter(copy.deepcopy(parameters))
        self._instruction_optimizer.bind_parameter(copy.deepcopy(parameters))

    def _backward(self,
                 evaluated_cases: List[EvaluatedCase],
                 ):
        self._example_optimizer.init_examples(evaluated_cases)
        self._select_optimize_strategy()
        for name, param in self._parameters.items():
            if self._is_optimize_instruction:
                self._instruction_optimizer.backward(evaluated_cases)
                backward_params = self._instruction_optimizer.parameters()
            else:
                self._example_optimizer.backward(evaluated_cases)
                backward_params = self._example_optimizer.parameters()
            param.set_gradient("system_prompt", backward_params.get(name).get_gradient("system_prompt"))
            param.set_gradient("user_prompt", backward_params.get(name).get_gradient("user_prompt"))

    def _update(self):
        if self._is_optimize_instruction:
            self._instruction_optimizer.update()
        instr_parameters = self._instruction_optimizer.parameters()
        for name, param in self._parameters.items():
            if not param.llm_call.get_freeze_user_prompt():
                optimized_prompt = self._example_optimizer.format_prompt(
                    instr_parameters.get(name).llm_call.get_user_prompt(),
                    self._example_optimizer.parameters().get(name, {}).get_gradient("user_prompt")
                )
                param.llm_call.update_user_prompt(optimized_prompt)
            if not param.llm_call.get_freeze_system_prompt():
                # if user prompt already adds examples, skip add examples to system prompt
                optimized_prompt = TuneUtils.get_content_string_from_template(
                    instr_parameters.get(name).llm_call.get_system_prompt()
                )
                if param.llm_call.get_freeze_user_prompt():
                    optimized_prompt = self._example_optimizer.format_prompt(
                        instr_parameters.get(name).llm_call.get_system_prompt(),
                        self._example_optimizer.parameters().get(name, {}).get_gradient("system_prompt")
                    )
                param.llm_call.update_system_prompt(optimized_prompt)

    def _select_optimize_strategy(self):
        need_optimize_example = self._example_optimizer.num_examples > 0
        self._is_optimize_instruction = random.choice([True, False]) if need_optimize_example else True
