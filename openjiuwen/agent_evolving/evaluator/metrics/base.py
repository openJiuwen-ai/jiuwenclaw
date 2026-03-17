# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
Metric abstraction for scoring.

Metric: Computes score for single (prediction, label).
MetricResult: Union[float, Dict[str, float]] for single or multi-metric scores.
"""

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Union

MetricResult = Union[float, Dict[str, float]]


class Metric(ABC):
    """Base class for evaluation metrics.

    Subclasses implement compute() for scoring logic.
    """

    @property
    @abstractmethod
    def name(self) -> str:
        """Metric identifier.

        Returns:
            Unique metric name
        """
        ...

    @property
    def higher_is_better(self) -> bool:
        """Whether higher score indicates better performance.

        Returns:
            True if higher is better (default: True)
        """
        return True

    @abstractmethod
    def compute(
        self,
        prediction: Any,
        label: Any,
        **kwargs: Any,
    ) -> MetricResult:
        """Compute score for single sample.

        Args:
            prediction: Model prediction
            label: Expected label
            **kwargs: Additional context (e.g., question, case)

        Returns:
            Score (float) or Dict[metric_name, score]
        """
        ...

    def compute_batch(
        self,
        predictions: List[Any],
        labels: List[Any],
        **kwargs: Any,
    ) -> List[MetricResult]:
        """Compute scores for batch of samples.

        Args:
            predictions: List of predictions
            labels: List of labels
            **kwargs: Additional context

        Returns:
            List of scores
        """
        return [self.compute(predictio, label, **kwargs) for predictio, label in zip(predictions, labels)]
