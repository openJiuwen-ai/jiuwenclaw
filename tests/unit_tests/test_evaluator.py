# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""Tests for TraceOutcomeEvaluator and TaskNameInferrer."""

import pytest
from jiuwenswarm.evolve.ahe.evaluator import TraceOutcomeEvaluator, TaskNameInferrer
from jiuwenswarm.evolve.models import TraceOutcome


class TestTaskNameInferrer:
    def test_from_skill_name(self):
        trace = {"id": "abc123def456", "skill_name": "bash-tool"}
        result = TaskNameInferrer.infer(trace)
        assert result == "skill_bash-tool_abc123de"

    def test_from_user_message(self):
        trace = {
            "id": "abc123def456",
            "messages": [{"role": "user", "content": "帮我写一个 Python 脚本"}],
        }
        result = TaskNameInferrer.infer(trace)
        assert "task_" in result
        assert "abc123de" in result

    def test_fallback_to_trace_id(self):
        trace = {"id": "abc123def456"}
        result = TaskNameInferrer.infer(trace)
        assert result == "abc123def456"

    def test_empty_messages(self):
        trace = {"id": "xyz789", "messages": []}
        result = TaskNameInferrer.infer(trace)
        assert result == "xyz789"


class TestTraceOutcomeEvaluatorFast:
    """Non-LLM heuristic evaluation."""

    def test_span_error_detection(self):
        evaluator = TraceOutcomeEvaluator()
        result = evaluator.evaluate_fast(
            trace_dict={"id": "abc123", "status_code": "ERROR", "status_description": "timeout"},
        )
        assert result.outcome == "fail"
        assert result.judgment_method == "span_error"

    def test_empty_output_detection(self):
        evaluator = TraceOutcomeEvaluator()
        result = evaluator.evaluate_fast(trace_dict={"id": "abc123", "output": ""})
        assert result.outcome == "uncertain"
        assert result.judgment_method == "heuristic"

    def test_normal_trace_returns_uncertain(self):
        evaluator = TraceOutcomeEvaluator()
        result = evaluator.evaluate_fast(
            trace_dict={"id": "abc123", "status_code": "OK", "output": {"content": "some text"}},
        )
        assert result.outcome == "uncertain"
        assert result.judgment_method == "heuristic"

    def test_error_description_detection(self):
        evaluator = TraceOutcomeEvaluator()
        result = evaluator.evaluate_fast(
            trace_dict={"id": "abc123", "status_code": "OK", "status_description": "Error: connection refused"},
        )
        assert result.outcome == "fail"


class TestTraceOutcomeEvaluatorParseResponse:
    """Test _parse_llm_response static method."""

    def test_valid_json(self):
        content = '{"outcome": "pass", "score": 0.9, "confidence": 0.85, "reason": "task completed", "key_evidence": "user got correct answer", "missing_requirements": [], "needs_external_verification": false}'
        result = TraceOutcomeEvaluator._parse_llm_response(content)
        assert result.outcome == "pass"
        assert result.score == 0.9

    def test_json_embedded_in_text(self):
        content = 'Based on my analysis:\n{"outcome": "fail", "score": 0.2, "confidence": 0.9, "reason": "task not completed"}\nEnd of analysis.'
        result = TraceOutcomeEvaluator._parse_llm_response(content)
        assert result.outcome == "fail"

    def test_invalid_json_fallback(self):
        content = "I think this trace shows the task was uncertain."
        result = TraceOutcomeEvaluator._parse_llm_response(content)
        assert result.outcome == "uncertain"
        assert result.score == 0.5
