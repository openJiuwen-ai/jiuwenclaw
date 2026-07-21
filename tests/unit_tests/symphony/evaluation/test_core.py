from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

import pytest

import jiuwenswarm.symphony.evaluation as evaluation


def fingerprint(
    kind: str = "skill", identifier: str = "weather"
) -> evaluation.Fingerprint:
    return evaluation.Fingerprint(
        type=kind,
        id=identifier,
        name=identifier,
        description="Answer weather questions accurately.",
        version="1.0.0",
        inputs=[evaluation.ParameterSpec(name="query", type="text")],
        outputs=[evaluation.ArtifactSpec(name="answer", type="text")],
        static_data={"documentation": "Return concise current conditions."},
    )


def case(kind: str = "skill", **kwargs: Any) -> evaluation.EvaluationCase:
    values: dict[str, Any] = {
        "fingerprint": fingerprint(kind),
        "message": [
            {"role": "user", "content": "Weather in Shenzhen?"},
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": "call_weather",
                        "type": "function",
                        "function": {
                            "name": "weather",
                            "arguments": '{"city":"Shenzhen"}',
                        },
                    }
                ],
            },
            {"role": "tool", "tool_call_id": "call_weather", "content": "30"},
            {"role": "user", "content": "Use Celsius."},
            {"role": "assistant", "content": "It is 30 °C."},
        ],
        "success": True,
        "latency": evaluation.Latency(ttft=1_000, e2e=10_000),
        "event_time": datetime(2026, 7, 18, tzinfo=timezone.utc),
    }
    values.update(kwargs)
    return evaluation.EvaluationCase(**values)


def test_agent_and_skill_case_extract_openai_message_values() -> None:
    skill_case = case()
    agent_case = case("agent")

    assert skill_case.input_contents == ["Weather in Shenzhen?", "Use Celsius."]
    assert skill_case.final_output == "It is 30 °C."
    assert agent_case.fingerprint.type == "agent"
    assert evaluation.EvaluationCase(fingerprint=fingerprint()).final_output is None


def test_latency_validates_invalid_values() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        evaluation.Latency(e2e=-1)


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_latency_rejects_non_finite_values(invalid: float) -> None:
    with pytest.raises(ValueError, match="finite"):
        evaluation.Latency(ttft=invalid)
    with pytest.raises(ValueError, match="finite"):
        evaluation.Latency(e2e=invalid)


def test_success_rate_single_and_grouped_results_exclude_na() -> None:
    evaluator = evaluation.SuccessRateEvaluator()
    results = evaluator.evaluate_batch(
        [case(success=True), case(success=False), case(success=None)]
    )

    assert [(result.score, result.level) for result in results] == [
        (1.0, "excellent"),
        (0.0, "fail"),
        (None, "not_applicable"),
    ]
    aggregate = evaluator.new_accumulator().extend(results).aggregate()[0]
    assert aggregate.score == 0.5
    assert aggregate.level == "fail"
    assert aggregate.reason == ""
    assert aggregate.metrics["result_count"] == 2


@pytest.mark.parametrize(
    ("value", "level", "score"),
    [
        (5_000, "excellent", 1.0),
        (5_001, "good", 0.8),
        (10_001, "pass", 0.6),
        (15_001, "fail", 0.0),
    ],
)
def test_realtime_latency_thresholds(value: float, level: str, score: float) -> None:
    evaluator = evaluation.LatencyEvaluator("realtime_interaction")
    result = evaluator.evaluate(
        case(latency=evaluation.Latency(ttft=value))
    )

    assert (result.level, result.score) == (level, score)


def test_short_and_long_latency_and_missing_data() -> None:
    short = evaluation.LatencyEvaluator("short_task")
    long = evaluation.LatencyEvaluator("long")

    assert (
        short.evaluate(case(latency=evaluation.Latency(e2e=30_000))).level
        == "excellent"
    )
    assert (
        short.evaluate(case(latency=evaluation.Latency(e2e=90_001))).level
        == "fail"
    )
    assert (
        long.evaluate(case(latency=evaluation.Latency(e2e=999_999))).level
        == "excellent"
    )
    assert short.evaluate(case(latency=None)).level == "not_applicable"


def test_latency_evaluator_reads_classified_static_scenario() -> None:
    original = fingerprint()
    classified = evaluation.LatencyScenarioClassifier(
        FakeLLM('{"scenario": "realtime_interaction", "reason": "interactive Q&A"}')
    ).apply(original)
    evaluator = evaluation.LatencyEvaluator(scenario=None)
    result = evaluator.evaluate(
        case(
            fingerprint=classified,
            latency=evaluation.Latency(ttft=5_001, e2e=1),
        )
    )

    assert "evaluation" not in original.static_data
    assert result.metrics["scenario"] == "realtime_interaction"
    assert result.level == "good"


def test_latency_scenario_classifier_rejects_invalid_response() -> None:
    classifier = evaluation.LatencyScenarioClassifier(
        FakeLLM('{"scenario": "instant", "reason": "unsupported"}')
    )

    with pytest.raises(ValueError, match="unsupported latency scenario"):
        classifier.classify(fingerprint())


def test_latency_aggregation_reports_both_distributions_and_grades_p95() -> None:
    evaluator = evaluation.LatencyEvaluator("short_task")
    results = [
        evaluator.evaluate(
            case(
                latency=evaluation.Latency(ttft=first_response, e2e=total)
            )
        )
        for first_response, total in [(100, 10_000), (300, 20_000)]
    ]

    aggregate = evaluator.new_accumulator().extend(results).aggregate()[0]

    assert aggregate.metrics["ttft"] == {
        "avg": 200.0,
        "p95": 290.0,
        "p99": 298.0,
    }
    assert aggregate.metrics["e2e"] == {
        "avg": 15_000.0,
        "p95": 19_500.0,
        "p99": 19_900.0,
    }
    assert aggregate.level == "excellent"
    assert aggregate.reason == ""


class FakeLLM:
    def __init__(self, response: str | Exception) -> None:
        self.response = response
        self.messages: Any = None

    def chat(self, messages: Any, **kwargs: Any) -> list[str]:
        self.messages = messages
        if isinstance(self.response, Exception):
            raise self.response
        return [self.response]

    def generate(self, prompts: Any, **kwargs: Any) -> list[str]:
        raise AssertionError("Scoring evaluators must use chat.")


@pytest.mark.parametrize(
    ("evaluator_type", "scores"),
    [
        (
            evaluation.AccuracyEvaluator,
            [(0.89, "fail"), (0.90, "pass"), (0.95, "good"), (0.99, "excellent")],
        ),
        (
            evaluation.CompletenessEvaluator,
            [(0.59, "fail"), (0.60, "pass"), (0.80, "good"), (0.90, "excellent")],
        ),
    ],
)
def test_llm_metric_thresholds(
    evaluator_type: type, scores: list[tuple[float, str]]
) -> None:
    for score, level in scores:
        llm = FakeLLM(f'{{"score": {score}, "reason": "ok", "evidence": ["x"]}}')
        result = evaluator_type(llm).evaluate(case())
        assert result.level == level
        assert not hasattr(result, "evidence")
        assert "static_data" in llm.messages[0]["content"]
        assert "Use Celsius" in llm.messages[0]["content"]
        assert "call_weather" in llm.messages[0]["content"]
        assert "optional evidence" not in llm.messages[0]["content"]


def test_completeness_prompt_evaluates_goal_coverage_and_execution_closure() -> None:
    llm = FakeLLM('{"score": 0.8, "reason": "mostly complete"}')

    result = evaluation.CompletenessEvaluator(llm).evaluate(case())

    prompt = llm.messages[0]["content"]
    assert result.metric == "completeness"
    assert "user goals" in prompt
    assert "multi-step" in prompt
    assert "capability commitment" in prompt
    assert "final result" in prompt


@pytest.mark.parametrize(
    "llm",
    [
        None,
        FakeLLM(RuntimeError("offline")),
        FakeLLM("not json"),
        FakeLLM('{"score": 2, "reason": "bad"}'),
        FakeLLM('{"score": 0.9}'),
    ],
)
def test_llm_metric_failures_are_not_applicable(llm: FakeLLM | None) -> None:
    assert evaluation.AccuracyEvaluator(llm).evaluate(case()).level == "not_applicable"


def test_llm_metric_missing_input_or_output_is_not_applicable() -> None:
    evaluator = evaluation.AccuracyEvaluator(FakeLLM('{"score": 1, "reason": "ok"}'))
    no_input = case(message=[{"role": "assistant", "content": "x"}])
    no_output = case(message=[{"role": "user", "content": "x"}])

    assert evaluator.evaluate(no_input).level == "not_applicable"
    assert evaluator.evaluate(no_output).level == "not_applicable"


def test_accuracy_aggregation_uses_accuracy_thresholds() -> None:
    evaluator = evaluation.AccuracyEvaluator(
        FakeLLM('{"score": 0.92, "reason": "mostly accurate"}')
    )
    results = evaluator.evaluate_batch([case(), case()])

    aggregate = evaluator.new_accumulator().extend(results).aggregate()[0]

    assert aggregate.score == pytest.approx(0.92)
    assert aggregate.level == "pass"


@dataclass
class Rule:
    code: str
    severity: evaluation.ComplianceSeverity
    passed: bool = False
    raises: bool = False

    def evaluate(
        self, fingerprint: evaluation.Fingerprint
    ) -> evaluation.AssertionResult:
        if self.raises:
            raise RuntimeError("broken rule")
        return evaluation.AssertionResult(
            code="ignored",
            passed=self.passed,
            severity="hard",
            reason=fingerprint.name,
            evidence=[{"rule": self.code}],
        )


@pytest.mark.parametrize(
    ("rules", "expected"),
    [
        ([Rule("ok", "hard", True)], (1.0, "excellent")),
        ([Rule("light", "light")], (0.8, "good")),
        ([Rule("major", "major")], (0.6, "pass")),
        ([Rule("hard", "hard")], (0.0, "fail")),
        ([Rule("raises", "light", raises=True)], (0.0, "fail")),
    ],
)
def test_compliance_severity(rules: list[Rule], expected: tuple[float, str]) -> None:
    result = evaluation.ComplianceEvaluator(rules).evaluate(case())
    assert (result.score, result.level) == expected


def test_compliance_without_rules_is_not_applicable() -> None:
    result = evaluation.ComplianceEvaluator().evaluate(case())
    assert result.level == "not_applicable"


def test_compliance_rejects_unknown_severity() -> None:
    rule = Rule("unknown", "warning")
    with pytest.raises(ValueError, match="Unsupported compliance severity"):
        evaluation.ComplianceEvaluator([rule])


def test_compliance_isolates_invalid_rule_result_as_hard_failure() -> None:
    class InvalidRule:
        code = "invalid-result"
        severity = "light"

        def evaluate(self, fingerprint: evaluation.Fingerprint) -> object:
            return object()

    result = evaluation.ComplianceEvaluator([InvalidRule()]).evaluate(case())

    assert (result.score, result.level) == (0.0, "fail")


def test_assertion_result_rejects_unknown_severity() -> None:
    with pytest.raises(ValueError, match="severity"):
        evaluation.AssertionResult(
            code="bad",
            passed=False,
            severity="warning",
        )


def test_result_models_recursively_convert_dates_to_json_safe_values() -> None:
    timestamp = datetime(2026, 7, 18, 8, 30, tzinfo=timezone.utc)
    metric = evaluation.MetricResult(
        metric="accuracy",
        type="skill",
        id="weather",
        version="1.0.0",
        event_time=timestamp,
        score=1.0,
        level="excellent",
        reason="ok",
        metrics={"nested": [{"measured_at": timestamp}]},
    )
    window = evaluation.Window(start=timestamp, end=timestamp, label="sample")
    quality = evaluation.QualityResult(
        type="skill",
        id="weather",
        version="1.0.0",
        window=window,
        result_count=1,
        metrics={"accuracy": metric},
        confidence="low",
    )

    payload = quality.to_dict()
    assert payload["metrics"]["accuracy"]["event_time"] == timestamp.isoformat()
    assert payload["metrics"]["accuracy"]["metrics"]["nested"][0][
        "measured_at"
    ] == timestamp.isoformat()
    json.dumps(payload, allow_nan=False)


def test_public_result_models_do_not_expose_assertions_or_evidence() -> None:
    metric = evaluation.MetricResult(
        metric="accuracy",
        type="agent",
        id="weather",
        version="1",
        event_time=None,
        score=1.0,
        level="excellent",
        reason="ok",
    )
    quality = evaluation.QualityResult(
        type="agent",
        id="weather",
        version="1",
        window=None,
        result_count=1,
        metrics={"accuracy": metric},
        confidence="low",
    )

    assert not hasattr(metric, "assertions")
    assert not hasattr(metric, "evidence")
    assert not hasattr(quality, "evidence")
    assert "assertions" not in metric.to_dict()
    assert "evidence" not in metric.to_dict()
    assert metric.to_dict()["reason"] == "ok"
    assert "evidence" not in quality.to_dict()
    assert "reasons" not in quality.to_dict()
    assert "reason" not in quality.to_dict()["metrics"]["accuracy"]


@pytest.mark.parametrize("invalid", [float("nan"), float("inf"), float("-inf")])
def test_result_models_reject_non_finite_nested_numbers(invalid: float) -> None:
    result = evaluation.MetricResult(
        metric="accuracy",
        type="skill",
        id="weather",
        version="1.0.0",
        event_time=None,
        score=1.0,
        level="excellent",
        reason="ok",
        metrics={"distribution": [invalid]},
    )
    with pytest.raises(ValueError, match="finite"):
        result.to_dict()


def test_result_models_reject_unknown_nested_objects() -> None:
    assertion = evaluation.AssertionResult(
        code="unknown",
        passed=True,
        evidence=[{"value": object()}],
    )
    with pytest.raises(TypeError, match="not JSON serializable"):
        assertion.to_dict()


def test_compliance_aggregation_keeps_one_static_result() -> None:
    evaluator = evaluation.ComplianceEvaluator([Rule("static", "light")])
    result = evaluator.evaluate(case())

    aggregate = evaluator.new_accumulator().extend([result, result]).aggregate()[0]

    assert aggregate.metrics["result_count"] == 1
    assert aggregate.score == result.score
    assert aggregate.reason == ""
    assert not hasattr(aggregate, "assertions")
    assert not hasattr(aggregate, "evidence")


def standard_suite() -> evaluation.EvaluationSuite:
    score_llm = FakeLLM('{"score": 1, "reason": "ok"}')
    return evaluation.EvaluationSuite(
        [
            evaluation.SuccessRateEvaluator(),
            evaluation.LatencyEvaluator(),
            evaluation.AccuracyEvaluator(score_llm),
            evaluation.CompletenessEvaluator(score_llm),
            evaluation.ComplianceEvaluator([Rule("ok", "hard", True)]),
        ]
    )


def test_suite_batch_and_quality_result_have_no_quality_score() -> None:
    suite = standard_suite()
    batches = suite.evaluate_batch([case(), case(kind="agent")])
    accumulator = suite.new_accumulator(evaluation.Window(label="daily"))
    quality = accumulator.extend(batches).aggregate()

    assert len(batches) == 2
    assert [result.type for result in quality] == ["agent", "skill"]
    assert quality[0].confidence == "low"
    assert set(quality[0].metrics) == {
        "success_rate",
        "latency",
        "accuracy",
        "completeness",
        "compliance",
    }
    assert "quality_score" not in quality[0].to_dict()
    assert quality[0].to_dict()["window"]["label"] == "daily"


def test_all_na_quality_result_has_zero_count_and_no_confidence() -> None:
    suite = evaluation.EvaluationSuite()
    result = suite.evaluate(case(success=None, latency=None, message=[]))
    quality = suite.new_accumulator().add(result).aggregate()[0]

    assert quality.result_count == 0
    assert quality.confidence == "none"
    assert quality.metrics["success_rate"].score is None


def test_suite_requires_atomic_five_metric_results_with_one_identity() -> None:
    with pytest.raises(ValueError, match="exactly the five"):
        evaluation.EvaluationSuite([evaluation.SuccessRateEvaluator()])

    suite = standard_suite()
    skill_results = suite.evaluate(case())
    accumulator = suite.new_accumulator()
    with pytest.raises(ValueError, match="all five metrics"):
        accumulator.add({"success_rate": skill_results["success_rate"]})

    agent_results = suite.evaluate(case(kind="agent"))
    mixed = dict(skill_results)
    mixed["accuracy"] = agent_results["accuracy"]
    with pytest.raises(ValueError, match="one fingerprint identity"):
        accumulator.add(mixed)

    mismatched_metric = dict(skill_results)
    mismatched_metric["accuracy"] = skill_results["completeness"]
    with pytest.raises(ValueError, match="mapping key"):
        accumulator.add(mismatched_metric)

    assert accumulator.aggregate() == []
