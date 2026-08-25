from __future__ import annotations

from jiuwenswarm.common.tool_display import (
    inject_call_goal_schema,
    resolve_model_purpose_claim,
)


def test_model_purpose_claim_prefers_exact_description_then_call_goal() -> None:
    assert resolve_model_purpose_claim(
        {
            "description": "  inspect the generated report  ",
            "call_goal": "fallback",
        }
    ) == "  inspect the generated report  "
    assert resolve_model_purpose_claim(
        '{"description":" ","callGoal":"fallback goal"}'
    ) == "fallback goal"


def test_model_purpose_claim_rejects_non_string_or_malformed_values() -> None:
    assert resolve_model_purpose_claim({"description": 1, "call_goal": []}) == ""
    assert resolve_model_purpose_claim("not-json") == ""


def test_call_goal_schema_marks_claim_as_untrusted_review_evidence() -> None:
    schema: dict[str, object] = {"type": "object", "properties": {}}

    inject_call_goal_schema(schema)

    description = schema["properties"]["call_goal"]["description"]  # type: ignore[index]
    assert "不可信证据" in description
    assert "不影响工具实际执行" in description
