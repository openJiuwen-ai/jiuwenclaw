"""LLM-based execution accuracy evaluator."""

from jiuwenswarm.symphony.evaluation.evaluators.base import LLMScoreEvaluator


class AccuracyEvaluator(LLMScoreEvaluator):
    metric = "accuracy"
    thresholds = (0.90, 0.95, 0.99)
    aggregation_thresholds = thresholds
    instruction = (
        "Evaluate whether the final output accurately satisfies all inputs and "
        "the Agent or Skill static contract."
    )
