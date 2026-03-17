# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Evaluation interfaces: BaseEvaluator, DefaultEvaluator, MetricEvaluator.

- BaseEvaluator: Abstract base with evaluate / batch_evaluate
- DefaultEvaluator: LLM-as-judge for consistency checking
- MetricEvaluator: Score aggregation from multiple Metrics
"""

import asyncio
from abc import ABC, abstractmethod
from typing import Dict, Any, List, Optional, Union
from concurrent.futures import ThreadPoolExecutor

from tqdm import tqdm

from openjiuwen.core.common.exception.codes import StatusCode
from openjiuwen.core.common.exception.errors import build_error
from openjiuwen.core.common.logging import logger
from openjiuwen.core.foundation.llm import ModelRequestConfig, ModelClientConfig, Model
from openjiuwen.agent_evolving.dataset import Case, EvaluatedCase, CaseLoader
from openjiuwen.agent_evolving.constant import TuneConstant
from openjiuwen.agent_evolving.utils import TuneUtils
from openjiuwen.agent_evolving.evaluator.metrics.base import Metric
from openjiuwen.agent_evolving.evaluator.templates import LLM_METRIC_TEMPLATE, LLM_METRIC_RETRY_TEMPLATE


class BaseEvaluator(ABC):
    """Abstract evaluator for converting (case, prediction) to EvaluatedCase.

    Implement evaluate() for single case, use batch_evaluate() for parallel execution.
    """

    @abstractmethod
    def evaluate(self, case: Case, predict: Dict[str, Any]) -> EvaluatedCase:
        """Evaluate single case with model prediction.

        Args:
            case: Original Case with inputs and label
            predict: Model prediction to evaluate

        Returns:
            EvaluatedCase with score and reasoning
        """
        pass

    def batch_evaluate(
        self,
        cases: Union[List[Case], CaseLoader],
        predicts: List[Dict[str, Any]],
        num_parallel: int = 1,
    ) -> List[EvaluatedCase]:
        """Evaluate multiple cases in parallel.

        Args:
            cases: List of Cases or CaseLoader
            predicts: List of model predictions
            num_parallel: Number of parallel workers

        Returns:
            List of EvaluatedCases

        Raises:
            TOOLCHAIN_EVALUATOR_EXECUTION_ERROR: if lengths mismatch
        """
        if len(cases) != len(predicts):
            raise build_error(
                StatusCode.TOOLCHAIN_EVALUATOR_EXECUTION_ERROR,
                error_msg=f"length of cases: {len(cases)} dose not equal with length of predicts: {len(predicts)} ",
            )

        TuneUtils.validate_digital_parameter(
            num_parallel, "num_parallel", TuneConstant.min_parallel_num, TuneConstant.max_parallel_num
        )
        num_workers = min(num_parallel, len(cases))
        with ThreadPoolExecutor(max_workers=num_workers) as executor:
            evaluated_cases = executor.map(self.evaluate, cases, predicts)
            return list(tqdm(evaluated_cases, desc="evaluate", total=len(cases)))


class DefaultEvaluator(BaseEvaluator):
    """Uses LLM as judge to evaluate model output consistency.

    Determines pass/fail and reasoning based on question/expected answer/model answer.
    Maps to 0/1 score.
    """

    def __init__(
        self,
        model_config: ModelRequestConfig,
        model_client_config: ModelClientConfig,
        metric: str = "",
    ):
        """Initialize with model configuration.

        Args:
            model_config: LLM request configuration
            model_client_config: Model client configuration
            metric: Optional custom evaluation metric template
        """
        super().__init__()
        self._model = Model(model_client_config, model_config)
        self._metric_template = LLM_METRIC_TEMPLATE.format({"user_metrics": metric})

    def evaluate(self, case: Case, predict: Dict[str, Any]) -> EvaluatedCase:
        """Evaluate case using LLM-as-judge.

        Args:
            case: Case with inputs and label
            predict: Model prediction

        Returns:
            EvaluatedCase with score (0 or 1) and reasoning
        """
        messages = self._metric_template.format({
            "question": str(case.inputs),
            "expected_answer": str(case.label),
            "model_answer": str(predict),
        }).to_messages()
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
        evaluated_case.score = 1.0 if self._is_pass_result(evaluated_result.get("result")) else 0.0
        evaluated_case.reason = evaluated_result.get("reason", "")
        return evaluated_case

    def _is_pass_result(self, result: Any) -> bool:
        """Check if evaluation result is passing.

        Args:
            result: Result from LLM (bool or "true"/"false" string)

        Returns:
            True if pass, False otherwise
        """
        if result is True:
            return True
        if isinstance(result, str):
            return result.strip().lower() == "true"
        return False

    def _extract_evaluate_result(self, response: str, case: Case, predict: Dict) -> Optional[Dict[str, Any]]:
        """Parse evaluation result from LLM response.

        Args:
            response: LLM response text
            case: Original Case
            predict: Model prediction

        Returns:
            Dict with 'result' and 'reason', or None on failure
        """
        evaluated_result = TuneUtils.parse_json_from_llm_response(response)
        if evaluated_result and "result" in evaluated_result and "reason" in evaluated_result:
            return evaluated_result
        messages = LLM_METRIC_RETRY_TEMPLATE.format({
            "question": str(case.inputs),
            "expected_answer": str(case.label),
            "model_answer": str(predict),
            "nonstandard_evaluated_result": response,
        }).to_messages()
        try:
            response = asyncio.run(self._model.invoke(messages)).content
        except Exception:
            return None
        return TuneUtils.parse_json_from_llm_response(response)


def _agg_score(results: List[float], aggregate: str = "mean") -> float:
    """Aggregate multiple scores.

    Args:
        results: List of scores
        aggregate: Aggregation method ("mean" or "first")

    Returns:
        Aggregated score
    """
    if not results:
        return 0.0
    if aggregate == "mean":
        return sum(results) / len(results)
    if aggregate == "first":
        return results[0]
    return sum(results) / len(results)


class MetricEvaluator(BaseEvaluator):
    """Evaluates using one or more Metrics with aggregation.

    Supports per-metric breakdown and configurable aggregation (mean, first).
    """

    def __init__(
        self,
        metrics: Union["Metric", List["Metric"]],
        aggregate: str = "mean",
    ):
        """Initialize with metrics and aggregation.

        Args:
            metrics: Single Metric or list of Metrics
            aggregate: Aggregation method ("mean" or "first")
        """
        if isinstance(metrics, list):
            self._metrics = metrics
        else:
            self._metrics = [metrics]
        self._aggregate = aggregate

    def evaluate(self, case: Case, predict: Dict[str, Any]) -> EvaluatedCase:
        """Evaluate using all metrics.

        Args:
            case: Case with inputs and label
            predict: Model prediction

        Returns:
            EvaluatedCase with aggregated score and per-metric breakdown
        """
        evaluated = EvaluatedCase(case=case, answer=predict)
        per_metric: Dict[str, float] = {}
        scores: List[float] = []
        for metric in self._metrics:
            out = metric.compute(predict, case.label, question=case.inputs, case=case)
            if isinstance(out, dict):
                for k, v in out.items():
                    vf = self._safe_convert(v)
                    per_metric[k] = vf
                    scores.append(vf)
            else:
                score = self._safe_convert(out)
                per_metric[metric.name] = score
                scores.append(score)
        evaluated.score = _agg_score(scores, self._aggregate)
        evaluated.per_metric = per_metric if per_metric else None
        return evaluated

    def _safe_convert(self, num):
        try:
            num = float(num) if not isinstance(num, (int, float)) else num
        except (TypeError, ValueError):
            num = 0.0
            logger.warning(f"Could not convert metric value {num} to float")
        return num
