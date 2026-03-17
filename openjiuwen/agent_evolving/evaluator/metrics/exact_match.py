# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
ExactMatchMetric: 0/1 scoring based on string exact match or normalized match.
"""

from typing import Any

from openjiuwen.agent_evolving.evaluator.metrics.base import Metric


class ExactMatchMetric(Metric):
    """Exact match or normalized match: 1.0 if consistent, 0.0 otherwise.
    
    When normalize=True, applies _normalize first before comparison.
    """

    def __init__(self, normalize: bool = True):
        self._normalize_flag = normalize

    @property
    def name(self) -> str:
        return "exact_match"

    @property
    def higher_is_better(self) -> bool:
        return True

    def compute(
        self,
        prediction: Any,
        label: Any,
        **kwargs: Any,
    ) -> float:
        if self._normalize_flag:
            return 1.0 if ExactMatchMetric._normalize(prediction) == ExactMatchMetric._normalize(label) else 0.0
        return 1.0 if str(prediction) == str(label) else 0.0

    @staticmethod
    def _normalize(input_data: Any) -> str:
        """Convert to lowercase, strip whitespace, collapse multiple spaces to
        single space, used for normalized comparison.
        """
        result = str(input_data).strip().lower()
        return " ".join(result.split())
