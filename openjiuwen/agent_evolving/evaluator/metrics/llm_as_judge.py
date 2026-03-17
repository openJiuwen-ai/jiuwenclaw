# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
LLMAsJudgeMetric: Uses Model as judge to determine semantic consistency
between question/expected answer/model answer, returns 0/1.
"""

import asyncio
from typing import Any, Optional

from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.agent_evolving.evaluator.metrics.base import Metric
from openjiuwen.agent_evolving.evaluator.templates import LLM_METRIC_TEMPLATE
from openjiuwen.agent_evolving.utils import TuneUtils


class LLMAsJudgeMetric(Metric):
    """Uses Model as judge to perform semantic consistency check on
    (prediction, label); returns 1.0 if consistent, 0.0 otherwise;
    optional question context.
    """

    def __init__(
        self,
        model_config: ModelRequestConfig,
        model_client_config: ModelClientConfig,
        user_metrics: str = "",
    ):
        self._model = Model(model_client_config, model_config)
        self._template = LLM_METRIC_TEMPLATE.format({"user_metrics": user_metrics or ""})

    @property
    def name(self) -> str:
        return "llm_as_judge"

    @property
    def higher_is_better(self) -> bool:
        return True

    def compute(
        self,
        prediction: Any,
        label: Any,
        question: Optional[Any] = None,
        **kwargs: Any,
    ) -> float:
        messages = self._template.format({
            "question": str(question or ""),
            "expected_answer": str(label),
            "model_answer": str(prediction),
        }).to_messages()
        try:
            response = asyncio.run(self._model.invoke(messages)).content
        except Exception:
            return 0.0
        return self._parse_result(response)

    def _parse_result(self, response: str) -> float:
        """Parse evaluation result, returns 1.0 (pass) or 0.0 (fail)."""
        data = TuneUtils.parse_json_from_llm_response(response)
        result = data.get("result") if data else None
        if result is True:
            return 1.0
        if isinstance(result, str):
            return 1.0 if result.strip().lower() == "true" else 0.0
        return 0.0
