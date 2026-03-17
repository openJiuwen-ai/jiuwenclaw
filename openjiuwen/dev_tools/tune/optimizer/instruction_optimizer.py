# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
prompt optimization evaluators
"""
import random
import re
from typing import List, Optional, Dict

import asyncio

from openjiuwen.dev_tools.tune.utils import TuneUtils
from openjiuwen.core.operator.legacy.llm_call.base import LLMCall
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.core.foundation.prompt.assemble.assembler import PromptAssembler
from openjiuwen.dev_tools.tune.base import EvaluatedCase, TuneConstant
from openjiuwen.dev_tools.tune.optimizer.base import BaseOptimizer, TextualParameter

PROMPT_INSTRUCTION_OPTIMIZE_TEMPLATE = PromptTemplate(content="""
你是一位提示词优化专家，你的任务是根据提供的信息对提示词进行优化。具体信息如下:
首先，请阅读以下提示词:
<prompt_base>
{{prompt_instruction}}
</prompt_base>

你拥有的的工具和API说明如下:
<tools_description>
{{tools_description}}
</tools_description>

提示词在应用的过程中出现的错误case如下：
<bad_cases>
{{bad_cases}}
</bad_cases>

对这些错误case的反思如下:
<reflections_on_bad_cases>
{{reflections_on_bad_cases}}
</reflections_on_bad_cases>

在优化提示词模版时，请遵循如下要求:
1. 在<思考>标签中，请根据错误示例及其对应的反思内容，深入、全面地分析提示词中可能导致错误的部分。分析应覆盖：错误原因的识别、原始提示词中存在的问题，以及通过哪些具体修改可以有效规避这些问题。
2. 在<PROMPT_OPTIMIZED>标签中，基于上述分析，输出优化后的提示词版本。
3. 分析过程中应聚焦于问题的具体成因，结合模板结构、语意表达和格式规范等方面，系统性地进行优化。
4. 优化过程中务必信息表达完整、逻辑侵袭，不可遗漏重要内容或引入模糊表达
5. 不可直接使用给定的示例，也不要在提示词中加入示例中的具体信息，可以通过抽象、改写的方式总结

输出格式：
<思考>
[在此详细说明你对提示词的优化分析]
</思考>
<PROMPT_OPTIMIZED>
[在此输出优化后的提示词]
</PROMPT_OPTIMIZED>
请确保优化后的内容能够有效避免之前出现的错误case。
""")


PROMPT_INSTRUCTION_OPTIMIZE_BOTH_TEMPLATE = PromptTemplate(content="""
你是一位提示词优化专家，你的任务是根据提供的信息对提示词进行优化。具体信息如下:
首先，请阅读以下system和user提示词:
<system_prompt_base>
{{system_prompt}}
</system_prompt_base>

<user_prompt_base>
{{user_prompt}}
</user_prompt_base>

你拥有的的工具和API说明如下:
<tools_description>
{{tools_description}}
</tools_description>

提示词在应用的过程中出现的错误case如下：
<bad_cases>
{{bad_cases}}
</bad_cases>

对这些错误case的反思如下:
<reflections_on_bad_cases>
{{reflections_on_bad_cases}}
</reflections_on_bad_cases>

在优化提示词模版时，请遵循如下要求:
1. 在<思考>标签中，请根据错误示例及其对应的反思内容，深入、全面地分析提示词中可能导致错误的部分。分析应覆盖：错误原因的识别、原始提示词中存在的问题，以及通过哪些具体修改可以有效规避这些问题。
2. 在<SYSTEM_PROMPT_OPTIMIZED>和<USER_PROMPT_OPTIMIZED>标签中，基于上述分析，输出优化后的system和user提示词版本。
3. 分析过程中应聚焦于问题的具体成因，结合模板结构、语意表达和格式规范等方面，系统性地进行优化。
4. 优化过程中务必信息表达完整、逻辑侵袭，不可遗漏重要内容或引入模糊表达
5. 不可直接使用给定的示例，也不要在提示词中加入示例中的具体信息，可以通过抽象、改写的方式总结

输出格式：
<思考>
[在此详细说明你对提示词的优化分析]
</思考>
<SYSTEM_PROMPT_OPTIMIZED>
[在此输出优化后的system提示词]
</SYSTEM_PROMPT_OPTIMIZED>
<USER_PROMPT_OPTIMIZED>
[在此输出优化后的user提示词]
</USER_PROMPT_OPTIMIZED>
请确保优化后的内容能够有效避免之前出现的错误case。
""")


CREATE_PROMPT_TEXTUAL_GRADIENT_TEMPLATE = PromptTemplate(content="""
作为提示词优化专家，我的目标是帮助代理高效且成功地完成任务
当前的system和user提示词是:
<system_prompt_base>
{{system_prompt}}
</system_prompt_base>

<user_prompt_base>
{{user_prompt}}
</user_prompt_base>

提示词涉及的可用的工具列表如下：
<tools_description>
{{tools_description}}
</tools_description>

然而，这个提示词在以下实例中并未能给出正确的结果
<bad_cases>
{{bad_cases}}
</bad_cases>

请提供详细的反馈，分析指令可能出错的原因。
针对每个实例，具体说明指令中的问题，解释代理为何会误解指令，并提出如何让指令更加清晰和精确的建议
针对因模型调用失败导致的失败原因，可以不分析
每个反馈信息请用<INS>和</INS>包裹
""")


CREATE_BAD_CASE_TEMPLATE = PromptTemplate(content="""
[question]: {{question}}
[expected answer]: {{label}}
[assistant answer]: {{answer}}
[reason]: {{reason}}
=== 
""")

PLACEHOLDER_RESTORE_TEMPLATE = PromptTemplate(content="""
作为提示词优化专家，你的任务是根据给定信息补全提示词中的占位符
原始提示词：
<original_prompt>
{{original_prompt}}
</original_prompt>>

修改后的提示词：
<revised_prompt>
{{revised_prompt}}
</revised_prompt>

原提示词的占位符全集为：
<all_placeholders>
{{all_placeholders}}
</all_placeholders>

经比较，修改后的提示词比原提示词缺少了以下占位符：
<missing_placeholders>
{{missing_placeholders}}
</missing_placeholders>

你的目标是：
1. 恢复所有缺失的占位符到修改后的提示词<revised_prompt>中，参考原提示词，将占位符添加到合适的位置
2. 占位符需要以双花括号的形式添加到提示词中，例如“{{占位符名称}}”
3. 除了占位符的必要修改外，不许修改提示词内容
4. 直接返回添加完占位符的提示词，不要添加思考过程或其他额外内容
""")


class InstructionOptimizer(BaseOptimizer):
    def __init__(self,
                 model_config: ModelRequestConfig,
                 model_client_config: ModelClientConfig,
                 parameters: Optional[Dict[str, LLMCall]] = None,
                 **kwargs):
        super().__init__(parameters)
        self._model = Model(model_client_config, model_config)

    def _backward(self,
                 evaluated_cases: List[EvaluatedCase],
                 ):
        """optimize Instruction"""
        for name, param in self._parameters.items():
            textual_gradient = self._get_textual_gradient(name, param)
            if not param.llm_call.get_freeze_system_prompt():
                param.set_gradient("system_prompt", textual_gradient)
            if not param.llm_call.get_freeze_user_prompt():
                param.set_gradient("user_prompt", textual_gradient)


    def _update(self):
        for name, param in self._parameters.items():
            if not param.llm_call.get_freeze_system_prompt() and not param.llm_call.get_freeze_user_prompt():
                self._optimize_both_system_and_user_prompt(param)
            elif not param.llm_call.get_freeze_system_prompt():
                self._optimize_system_or_user_prompt(param, "system_prompt")
            elif not param.llm_call.get_freeze_user_prompt():
                self._optimize_system_or_user_prompt(param, "user_prompt")

    def update(self):
        return self._update()

    def _optimize_both_system_and_user_prompt(self, param: TextualParameter):
        optimized_system_prompt, optimized_user_prompt = self._optimize_both_instruction(
            param.llm_call.get_system_prompt(),
            param.llm_call.get_user_prompt(),
            param.get_gradient("system_prompt"),
            None
        )
        optimized_system_prompt = self._validate_and_revise_optimized_prompt(
            TuneUtils.get_content_string_from_template(param.llm_call.get_system_prompt()),
            optimized_system_prompt
        )
        optimized_user_prompt = self._validate_and_revise_optimized_prompt(
            TuneUtils.get_content_string_from_template(param.llm_call.get_user_prompt()),
            optimized_user_prompt
        )
        param.llm_call.update_system_prompt(optimized_system_prompt)
        param.llm_call.update_user_prompt(optimized_user_prompt)

    def _optimize_system_or_user_prompt(self, param: TextualParameter, prompt_type: str):
        target_prompt = param.llm_call.get_system_prompt() \
            if prompt_type == "system_prompt" \
            else param.llm_call.get_user_prompt()
        optimized_prompt = self._optimize_instruction(
            target_prompt,
            param.get_gradient("system_prompt"),
            None
        )
        optimized_prompt = self._validate_and_revise_optimized_prompt(
            TuneUtils.get_content_string_from_template(target_prompt),
            optimized_prompt
        )
        if prompt_type == "system_prompt":
            param.llm_call.update_system_prompt(optimized_prompt)
        else:
            param.llm_call.update_user_prompt(optimized_prompt)

    def _get_textual_gradient(self,
                              name: str,
                              param: TextualParameter,
                              tools: Optional[list] = None) -> str:
        system_prompt = param.llm_call.get_system_prompt()
        user_prompt = param.llm_call.get_user_prompt()
        messages = CREATE_PROMPT_TEXTUAL_GRADIENT_TEMPLATE.format(
            dict(system_prompt=TuneUtils.get_content_string_from_template(system_prompt),
                 user_prompt=TuneUtils.get_content_string_from_template(user_prompt),
                 bad_cases=self._get_bad_cases_string(),
                 tools_description=str(tools),
                 )
        ).to_messages()

        textual_gradient = asyncio.run(self._model.invoke(messages)).content
        return textual_gradient

    def _optimize_instruction(self,
                              instruction: PromptTemplate,
                              textual_gradient,
                              tools):
        """update instruction"""
        messages = PROMPT_INSTRUCTION_OPTIMIZE_TEMPLATE.format(
            dict(prompt_instruction=TuneUtils.get_content_string_from_template(instruction),
                 bad_cases=self._get_bad_cases_string(),
                 reflections_on_bad_cases=textual_gradient,
                 tools_description=str(tools) if tools else "None"
                 )
        ).to_messages()
        response = asyncio.run(self._model.invoke(messages)).content
        return self._extract_optimized_prompt_from_response(response, tag="PROMPT_OPTIMIZED")

    def _optimize_both_instruction(self,
                                   system_prompt: PromptTemplate,
                                   user_prompt: PromptTemplate,
                                   textual_gradient,
                                   tools):
        """update instruction"""
        messages = PROMPT_INSTRUCTION_OPTIMIZE_BOTH_TEMPLATE.format(
            dict(system_prompt=TuneUtils.get_content_string_from_template(system_prompt),
                 user_prompt=TuneUtils.get_content_string_from_template(user_prompt),
                 bad_cases=self._get_bad_cases_string(),
                 reflections_on_bad_cases=textual_gradient,
                 tools_description=str(tools) if tools else "None"
                 )
        ).to_messages()
        response = asyncio.run(self._model.invoke(messages)).content
        system_prompt = self._extract_optimized_prompt_from_response(response, tag="SYSTEM_PROMPT_OPTIMIZED")
        user_prompt = self._extract_optimized_prompt_from_response(response, tag="USER_PROMPT_OPTIMIZED")
        return system_prompt, user_prompt

    @staticmethod
    def _extract_optimized_prompt_from_response(response: str, tag: str) -> Optional[str]:
        """extract optimized prompt from response"""
        optimized_prompt_pattern = r"<{}>(.*?)</{}>".format(tag, tag)
        match = re.search(optimized_prompt_pattern, response, re.DOTALL)
        if not match:
            return None
        optimized_prompt = match.group(1)
        return optimized_prompt.replace("<prompt_base>", "").replace("</prompt_base>", "")

    def _get_bad_cases_string(self) -> str:
        error_example_string = "\n".join(
            CREATE_BAD_CASE_TEMPLATE.format(
                dict(question=str(eval_case.case.inputs),
                     label=str(eval_case.case.label),
                     answer=str(eval_case.answer),
                     reason=eval_case.reason)
            ).content
            for eval_case in self._bad_cases
        )
        return error_example_string

    def _get_bad_cases(self, evaluated_cases: List[EvaluatedCase]) -> List[EvaluatedCase]:
        bad_cases = [case for case in evaluated_cases if case.score == 0]
        self._bad_cases = bad_cases
        if len(self._bad_cases) > TuneConstant.DEFAULT_MAX_SAMPLED_EXAMPLE_NUM:
            self._bad_cases = random.sample(self._bad_cases, k=TuneConstant.DEFAULT_MAX_SAMPLED_EXAMPLE_NUM)
        return self._bad_cases

    def _validate_and_revise_optimized_prompt(self,
                                              original_prompt: str,
                                              optimized_prompt: str
                                              ) -> str:
        placeholders = self._find_placeholders_from_prompt(original_prompt)
        updated_placeholders = self._find_placeholders_from_prompt(optimized_prompt)
        missing_placeholders = self._find_missing_placeholders(placeholders, updated_placeholders)
        if missing_placeholders:
            optimized_prompt = self._add_missing_placeholders_to_prompt(
                original_prompt,
                optimized_prompt,
                missing_placeholders,
                placeholders
            )
        return optimized_prompt

    @staticmethod
    def _find_placeholders_from_prompt(prompt: PromptTemplate | str) -> List[str]:
        return PromptAssembler(prompt.content).input_keys \
            if isinstance(prompt, PromptTemplate) \
            else PromptAssembler(prompt).input_keys

    @staticmethod
    def _find_missing_placeholders(ori_placeholders, opt_placeholders) -> List[str]:
        return list((set(ori_placeholders) - set(opt_placeholders)))

    def _add_missing_placeholders_to_prompt(self,
                                            ori_prompt,
                                            opt_prompt,
                                            missing_placeholders,
                                            all_placeholders
                                            ) -> str:
        messages = PLACEHOLDER_RESTORE_TEMPLATE.format(
            dict(original_prompt=ori_prompt,
                 revised_prompt=opt_prompt,
                 all_placeholders=str(all_placeholders),
                 missing_placeholders=str(missing_placeholders)
                 )
        ).to_messages()
        restored_prompt = asyncio.run(self._model.invoke(messages)).content
        restored_placeholders = PromptAssembler(restored_prompt).input_keys
        missing_placeholders = self._find_missing_placeholders(all_placeholders, restored_placeholders)
        if missing_placeholders:
            restored_prompt = restored_prompt + "\n" + "\n".join(f"{{{{{ph}}}}}"for ph in missing_placeholders)
        return restored_prompt
