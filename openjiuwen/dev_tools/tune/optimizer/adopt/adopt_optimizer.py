# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
import asyncio
import copy
import random
import re
from concurrent.futures import ThreadPoolExecutor
from typing import List, Optional, Dict, Tuple, Any

import openjiuwen.dev_tools.tune.optimizer.adopt.utils as ADOPT
from openjiuwen.dev_tools.tune.base import Case, EvaluatedCase
from openjiuwen.dev_tools.tune.optimizer.base import BaseOptimizer
from openjiuwen.dev_tools.tune.optimizer.base import TextualParameter
from openjiuwen.dev_tools.tune.optimizer.instruction_optimizer import InstructionOptimizer
from openjiuwen.dev_tools.tune.utils import TuneUtils
from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import llm_logger, LogEventType
from openjiuwen.core.operator.legacy.llm_call.base import LLMCall
from openjiuwen.core.foundation.llm import BaseMessage, Model
from openjiuwen.core.foundation.tool import ToolInfo

DEFAULT_BAD_CASES_SAMPLE_NUM: int = 5
DEFAULT_MODEL_RETRY_NUM: int = 5
DEFAULT_PARALLEL_NUM: int = 8


class AdoptOptimizer(BaseOptimizer):
    def __init__(self,
                 model: Model,
                 model_name: str,
                 parameters: Optional[Dict[str, LLMCall]] = None,
                 **kwargs
                 ):
        super().__init__(parameters)
        self._model_name = model_name

        class ModelWithRetry(Model):
            def __init__(self, wrapped_model: Model):
                super().__init__(
                    model_client_config=wrapped_model.model_client_config,
                    model_config=wrapped_model.model_config
                )
                self._model = wrapped_model

            async def invoke(self, model_name: str, messages: List[BaseMessage],
                             tools: List[ToolInfo]= None, temperature: float= 0.3,
                       top_p: float= 0.7, **kwargs: Any):
                for i in range(1, DEFAULT_MODEL_RETRY_NUM + 1):
                    try:
                        return await self._model.invoke(messages, tools=tools, temperature=temperature,
                                                        top_p=top_p, model=model_name, **kwargs)
                    except Exception as e:
                        llm_logger.warning(
                            "Failed to invoke model while doing optimization,retry",
                            event_type=LogEventType.LLM_CALL_ERROR,
                            model_name=model_name,
                            exception=str(e),
                            metadata={"retry_num": i / DEFAULT_MODEL_RETRY_NUM}
                        )
                        continue
                llm_logger.error(
                    "Failed to invoke the model, please check if the model is available.",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=model_name
                )
                return build_error(
                    StatusCode.TOOLCHAIN_OPTIMIZER_PARAM_ERROR,
                    error_msg="Failed to invoke the model"
                )
        self._model = ModelWithRetry(model)
        self._agent_description = kwargs.get("agent_description", "No description")
        self._constrain = kwargs.get("constrain", "No constrain")
        self._external_knowledge = kwargs.get("external_knowledge", "No external knowledge")

        self._good_cases: List[EvaluatedCase] = []

    def bind_parameter(self, parameters: Dict[str, LLMCall], **kwargs):
        super().bind_parameter(parameters)
        agent_description = kwargs.get("agent_description")
        if agent_description:
            self._agent_description = agent_description

    def _backward(self,
                 evaluated_cases: List[EvaluatedCase],
                 ):
        # 1. conclude all nodes' description
        self._conclude_job_for_each_node()

        # 2. calculate global gradient
        global_gradient = self._calculate_global_gradient()

        # 3. calculate partial gradient for each llm call
        partial_gradients = self._calculate_partial_gradients(global_gradient)

        # 4. update partial gradients
        for name, system_prompt_gradient, user_prompt_gradient in partial_gradients:
            param = self._parameters.get(name)
            if not param.llm_call.get_freeze_system_prompt():
                param.set_gradient("system_prompt", system_prompt_gradient)
            if not param.llm_call.get_freeze_user_prompt():
                param.set_gradient("user_prompt", user_prompt_gradient)

    def _update(self):
        optimizer = PartialOptimizer(
            model=self._model,
            model_name=self._model_name,
        )
        current_parameters = self.parameters()
        llm_calls = {name: param.llm_call for name, param in current_parameters.items()}
        optimizer.bind_parameter(llm_calls)
        optimizer.update()

    def _calculate_global_gradient(self) -> Dict[str, str]:
        def differential_analysis(case: EvaluatedCase):
            messages = ADOPT.OUTPUT_CHANGE_SYSTEM_PROMPT.to_messages()
            messages.extend(ADOPT.OUTPUT_CHANGE_USER_PROMPT.format(
                dict(workflow_description=self._agent_description,
                     current_output=str(case.answer),
                     ground_truth=str(case.label),
                     metric_fn=str(case.reason),
                     current_score=str(case.score),
                     constrain=self._constrain
                     )
            ).to_messages())
            differences = asyncio.run(self._model.invoke(self._model_name, messages)).content
            messages = ADOPT.DEEP_OUTPUT_ANALYSIS_SYSTEM_PROMPT.to_messages()
            messages.extend(ADOPT.DEEP_OUTPUT_ANALYSIS_USER_PROMPT.format(
                dict(workflow_description=self._agent_description,
                     node_input=str(case.inputs),
                     node_output=str(case.answer),
                     node_expected_output=str(case.label),
                     external_knowledge=str(self._external_knowledge),
                     shallow_difference=str(differences),
                     constrain=self._constrain
                     )
            ).to_messages())
            reflection = asyncio.run(self._model.invoke(self._model_name, messages)).content
            return "\n\n".join([differences, reflection])

        num_workers = max(min(DEFAULT_PARALLEL_NUM, len(self._bad_cases)), 1)
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            analyzed_differences = executor.map(
                differential_analysis, self._bad_cases)
            return dict([(case.case_id, diff) for case, diff in zip(self._bad_cases, list(analyzed_differences))])

    def _calculate_partial_gradients(self, global_gradient: Dict[str, str]):
        def optimize_each_llm_call(node_name: str, param: TextualParameter) -> Tuple[str, str, str]:
            return self._generate_textual_gradient_for_llm_calls(node_name, param, global_gradient)

        num_workers = max(min(DEFAULT_PARALLEL_NUM, len(self._bad_cases)), 1)
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            partial_gradients = executor.map(
                optimize_each_llm_call, list(self._parameters.keys()), list(self._parameters.values()))
            return list(partial_gradients)

    def _generate_textual_gradient_for_llm_calls(self, node_name: str, param: TextualParameter, global_gradient: Dict):
        def generate_each_node_cases(case: EvaluatedCase):
            trace_nodes = self._history.get_llm_call_history(case_id=case.case_id, llm_call_id=node_name)
            if not trace_nodes:
                llm_logger.warning(
                    "failed to get trace nodes for node",
                    event_type=LogEventType.LLM_CALL_ERROR,
                    model_name=self._model_name,
                    metadata={"case_id": case.case_id, "node_name": node_name}
                )
                return None
            input_list = [node.inputs for node in trace_nodes]
            output_list = [node.outputs for node in trace_nodes]
            messages = ADOPT.EXPECTED_OUTPUT_SYSTEM_PROMPT.to_messages()
            messages.extend(ADOPT.EXPECTED_OUTPUT_USER_PROMPT.format(
                dict(workflow_output=str(case.answer),
                     modification=global_gradient.get(case.case_id, ""),
                     dependency_from_this_workflow_final_output=param.get_description(),
                     node_in_block=ADOPT.build_node_io_string(input_list, output_list),
                )
            ).to_messages())
            response = self._model.invoke(self._model_name, messages).content
            revised_node_output = self._extract_content_from_response(response, "REVISED_NODE_OUTPUT")
            node_case = EvaluatedCase(
                case=Case(inputs=dict(inputs=input_list), label=dict(label=revised_node_output)),
                answer=dict(answer=output_list),
                score=0.0,
                reason=response,
            )
            return node_case

        num_workers = max(min(DEFAULT_PARALLEL_NUM, len(self._bad_cases)), 1)
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            node_cases = [
                case
                for case in list(executor.map(generate_each_node_cases, self._bad_cases)) if case is not None
            ]

        callback = param.llm_call.get_optimizer_callback
        param.llm_call.set_optimizer_callback = None
        partial_optimizer = PartialOptimizer(
            model=self._model,
            model_name=self._model_name,
            parameters={node_name: copy.deepcopy(param.llm_call)}
        )
        param.llm_call._optimizer_callback = callback
        partial_optimizer.backward(node_cases)
        partial_param = partial_optimizer.parameters().get(node_name)

        system_prompt_gradient = partial_param.get_gradient("system_prompt")
        user_prompt_gradient = partial_param.get_gradient("user_prompt")

        return node_name, system_prompt_gradient, user_prompt_gradient

    def _conclude_job_for_each_node(self):
        for name, param in self._parameters.items():
            self._conclude_node(name)

    def _conclude_node(self, node_name: str) -> None:
        param = self._parameters.get(node_name)
        if not param:
            raise build_error(
                StatusCode.TOOLCHAIN_AGENT_PARAM_ERROR,
                error_msg=f"Cannot find parameter: {node_name}"
            )
        system_prompt = param.llm_call.get_system_prompt()
        user_prompt = param.llm_call.get_user_prompt()
        messages = ADOPT.CONCLUDE_NODE_SYSTEM_PROMPT.to_messages()
        messages.extend(ADOPT.CONCLUDE_NODE_USER_PROMPT.format(
            dict(
                node_name=node_name,
                agent_description=self._agent_description,
                system_prompt=TuneUtils.get_content_string_from_template(system_prompt),
                user_prompt=TuneUtils.get_content_string_from_template(user_prompt),
                good_cases=TuneUtils.convert_cases_to_examples(self._good_cases)
            )
        ).to_messages())
        node_description = self._model.invoke(self._model_name, messages).content
        param.set_description(node_description)

    def _get_bad_cases(self, evaluated_cases: List[EvaluatedCase]) -> List[EvaluatedCase]:
        bad_cases = [case for case in evaluated_cases if case.score == 0.0]
        if len(bad_cases) > DEFAULT_BAD_CASES_SAMPLE_NUM:
            bad_cases = random.sample(bad_cases, k=DEFAULT_BAD_CASES_SAMPLE_NUM)
        self._bad_cases = bad_cases
        good_cases = [case for case in evaluated_cases if case.score == 1.0]
        if len(good_cases) > DEFAULT_BAD_CASES_SAMPLE_NUM:
            good_cases = random.sample(good_cases, k=DEFAULT_BAD_CASES_SAMPLE_NUM)
        self._good_cases = good_cases
        return bad_cases

    @staticmethod
    def _extract_content_from_response(response: str, tag: str) -> Optional[str]:
        """extract optimized prompt from response"""
        optimized_prompt_pattern = r"<{}>(.*?)</{}>".format(tag, tag)
        match = re.search(optimized_prompt_pattern, response, re.DOTALL)
        if not match:
            return None
        optimized_prompt = match.group(1)
        return optimized_prompt


class PartialOptimizer(InstructionOptimizer):
    def __init__(self,
                 model: Model,
                 model_name: str,
                 parameters: Optional[Dict[str, LLMCall]] = None,
                 **kwargs):
        super().__init__(model, model_name, parameters, **kwargs)

    def _get_textual_gradient(self,
                              name: str,
                              param: TextualParameter,
                              tools: Optional[list] = None) -> str:
        local_gradients = self._calculate_textual_gradient_by_bad_cases(param, tools)
        if not local_gradients:
            raise build_error(
                StatusCode.TOOLCHAIN_OPTIMIZER_BACKWARD_EXECUTION_ERROR,
                error_msg=f"Calculate local gradient of parameter: {name} failed."
            )
        return self._reduce_textual_gradient(param, local_gradients)

    def _calculate_textual_gradient_by_bad_cases(self,
                                                 param: TextualParameter,
                                                 tools: Optional[list] = None) -> List[str]:
        node_prompt = (
            f"system prompt: {TuneUtils.get_content_string_from_template(param.llm_call.get_system_prompt())}"
            f"\nuser prompt: {TuneUtils.get_content_string_from_template(param.llm_call.get_user_prompt())}"
        )
        local_gradients = []
        for bad_case in self._bad_cases:
            messages = ADOPT.GRADIENT_GENERATE_SYSTEM_PROMPT.to_messages()
            messages.extend(ADOPT.GRADIENT_GENERATE_USER_PROMPT.format(
                dict(node_job=param.get_description(),
                     node_input=str(bad_case.inputs.get("inputs")),
                     node_output=str(bad_case.answer.get("answer")),
                     modification=bad_case.reason,
                     node_expected_output=str(bad_case.label.get("label")),
                     node_prompt=node_prompt,
                     )
            ).to_messages())
            local_gradients.append(asyncio.run(self._model.invoke(self._model_name, messages)).content)
        return local_gradients

    def _reduce_textual_gradient(self, param: TextualParameter, local_gradients: List[str]):
        node_prompt = (
            f"system prompt: {TuneUtils.get_content_string_from_template(param.llm_call.get_system_prompt())}"
            f"\nuser prompt: {TuneUtils.get_content_string_from_template(param.llm_call.get_user_prompt())}"
        )
        messages = ADOPT.GRADIENT_REDUCE_SYSTEM_PROMPT.to_messages()
        messages.extend(ADOPT.GRADIENT_REDUCE_USER_PROMPT.format(
            dict(
                node_job=param.get_description(),
                all_reasons=str([grad for grad in local_gradients]),
                current_prompt=node_prompt
            )
        ).to_messages())
        reduced_gradient = asyncio.run(self._model.invoke(self._model_name, messages)).content
        return reduced_gradient