# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional
from concurrent.futures import ThreadPoolExecutor

import asyncio
from tqdm import tqdm

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.core.foundation.prompt import PromptTemplate
from openjiuwen.dev_tools.tune.base import Case, EvaluatedCase, TuneConstant
from openjiuwen.dev_tools.tune.utils import TuneUtils
from openjiuwen.dev_tools.tune.dataset.case_loader import CaseLoader


class BaseEvaluator(ABC):
    @abstractmethod
    def evaluate(self,
                 case: Case,
                 predict: Dict[str, Any]
                 ) -> EvaluatedCase:
        pass

    def batch_evaluate(self,
                       cases: List[Case] | CaseLoader,
                       predicts: List[Dict[str, Any]],
                       **kwargs
                       ) -> List[EvaluatedCase]:
        if len(cases) != len(predicts):
            raise build_error(
                StatusCode.TOOLCHAIN_EVALUATOR_EXECUTION_ERROR,
                error_msg=f"length of cases: {len(cases)} dose not equal with length of predicts: {len(predicts)} "
            )

        TuneUtils.validate_digital_parameter(kwargs.get("num_parallel", 1), "num_parallel",
                                             TuneConstant.MIN_PARALLEL_NUM, TuneConstant.MAX_PARALLEL_NUM)
        num_workers = min(kwargs.get("num_parallel", 1), len(cases))
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            evaluated_cases = executor.map(
                self.evaluate,
                cases, predicts)
            return list(tqdm(evaluated_cases, desc=f"evaluate", total=len(cases)))


LLM_METRIC_TEMPLATE = PromptTemplate(content="""
你是一个答案校验专家，负责校验给定的模型回答和标准答案之间的含义和结论一致性。请根据以下标准判断模型回答是否与标准答案的含义和结论一致。

- 如果模型回答和标准答案含义一致，返回`true`。
- 如果模型回答和标准答案含义不一致，返回`false`。
- 注意区分对话和工具调用，两者通常不能按语意判断为一致
- 结合用户问题和标准答案，简要分析模型回答和标准答案不一致的理由

以下是用户补充的自定义校验规则，如果与上述规则冲突，则优先遵从用户自定义规则，请严格遵守：
{{user_metrics}}

输出JSON格式：
```json
{{
“result”: true/false,
"reason": "校验理由"
}}
```

[问题]：{{question}}

以下是需要比对的模型回答和标准答案：
[标准答案]：{{expected_answer}}

[模型回答]：{{model_answer}}

请校验并返回结果：
"""
)


LLM_METRIC_RETRY_TEMPLATE = PromptTemplate(content="""
你是一个答案校验专家，负责修复不规范的评估结果。

## 原始待评估结果评估
[问题]：{{question}}
以下是需要比对的模型回答和标准答案：
[标准答案]：{{expected_answer}}
[模型回答]：{{model_answer}}

## 格式不规范的评估结果
但是当前收到了不规范的评估结果，导致无法正确解析成json格式：
<EVALUATED_RESULT>
{{nonstandard_evaluated_result}}
</EVALUATED_RESULT>

## 格式修复
请修正当前评估结果的格式，推理为什么上面的评估结果没有被json解析出来，修正并返回正确的评估格式，如下
输出JSON格式：
```json
{{
“result”: true/false,
"reason": "校验理由"
}}
```
## 要求
- 生成的json必须被```json```包裹
- 注意评估结果中是否存在不规范的引号使用，例如双引号与单引号生成错误、引号嵌套等问题

请校验并返回结果：
"""
)


class DefaultEvaluator(BaseEvaluator):
    def __init__(self,
                 model_config: ModelRequestConfig,
                 model_client_config: ModelClientConfig,
                 metric: str = "",
                 ):
        super().__init__()
        self._model = Model(model_client_config, model_config)
        self._metric_template = LLM_METRIC_TEMPLATE.format(
            dict(user_metrics=metric)
        )

    def evaluate(self,
                 case: Case,
                 predict: Dict[str, Any]
                 ) -> EvaluatedCase:
        messages = self._metric_template.format(
            dict(
                question=str(case.inputs),
                expected_answer=str(case.label),
                model_answer=str(predict),
            ),
        ).to_messages()
        evaluated_case = EvaluatedCase(case=case, answer=predict)
        try:
            response = asyncio.run(self._model.invoke(messages)).content
        except Exception:
            evaluated_case.reason = "Failed to evaluate case due to model error"
            return evaluated_case

        evaluated_result = self._extract_evaluate_result(response, case, predict)
        if not evaluated_result:
            evaluated_case.reason = "Failed to evaluate case due to parsing error"
            return evaluated_case
        result = evaluated_result.get("result", False)
        evaluated_case.reason = evaluated_result.get("reason", "")
        if result is True or (isinstance(result, str) and result.strip().lower() == "true"):
            evaluated_case.score = 1.0
            return evaluated_case
        return evaluated_case

    def _extract_evaluate_result(self, response: str, case: Case, predict: Dict) -> Optional[Dict[str, Any]]:
        evaluated_result = TuneUtils.parse_json_from_llm_response(response)
        if evaluated_result and "result" in evaluated_result and "reason" in evaluated_result:
            return evaluated_result
        messages = LLM_METRIC_RETRY_TEMPLATE.format(
            dict(
                question=str(case.inputs),
                expected_answer=str(case.label),
                model_answer=str(predict),
                nonstandard_evaluated_result=response
            ),
        ).to_messages()
        try:
            response = asyncio.run(self._model.invoke(messages)).content
        except Exception:
            return None
        return TuneUtils.parse_json_from_llm_response(response)
