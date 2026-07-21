"""LLM-based execution completeness evaluator."""

from jiuwenswarm.symphony.evaluation.evaluators.base import LLMScoreEvaluator


class CompletenessEvaluator(LLMScoreEvaluator):
    metric = "completeness"
    thresholds = (0.60, 0.80, 0.90)
    aggregation_thresholds = thresholds
    instruction = (
        "Evaluate whether the execution completely fulfills all user goals, "
        "covers every multi-step requirement and capability commitment, and "
        "delivers a closed-loop final result."
    )
