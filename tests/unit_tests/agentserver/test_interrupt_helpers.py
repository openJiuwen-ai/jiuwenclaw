from types import SimpleNamespace

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    convert_interactions_to_ask_user_question,
)


def _evolution_interrupt(
    tool_name: str,
    operation: str,
):
    if operation == "evolve":
        message = "是否批准 Skill 'demo-skill' 的 1 条演进经验？"
    else:
        message = "是否执行 Skill 'demo-skill' 的 1 项经验精简操作？"

    value = {
        "message": message,
        "tool_name": tool_name,
        "ui_options": [
            {"label": "本次允许", "value": "allow_once", "description": "允许本次技能演进变更执行"},
            {"label": "总是允许", "value": "allow_always", "description": "自动允许后续匹配的技能演进变更"},
            {"label": "拒绝", "value": "reject", "description": "跳过本次技能演进变更"},
        ],
    }
    return SimpleNamespace(
        id="call_123",
        value=value,
    )


def test_structured_simplify_approval_interrupt_is_classified():
    interaction = _evolution_interrupt("simplify_skill_experiences", "simplify")

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert result["source"] == "skill_evolution_approval"
    assert result["approval_schema"] == "openjiuwen.skill_evolution_approval.v1"
    assert result["evolution_meta"]["approval_kind"] == "simplify"
    assert result["evolution_meta"]["approval_transport"] == "interrupt"
    assert "approval_detail" not in result["questions"][0]
    assert result["questions"][0]["question"] == "是否执行 Skill 'demo-skill' 的 1 项经验精简操作？"
    assert [option["value"] for option in result["questions"][0]["options"]] == [
        "allow_once",
        "allow_always",
        "reject",
    ]


def test_structured_evolve_approval_interrupt_is_classified():
    interaction = _evolution_interrupt("evolve_skill_experiences", "evolve")

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert result["source"] == "skill_evolution_approval"
    assert result["approval_schema"] == "openjiuwen.skill_evolution_approval.v1"
    assert result["evolution_meta"]["approval_kind"] == "evolve"
    assert result["evolution_meta"]["approval_transport"] == "interrupt"
    assert "approval_detail" not in result["questions"][0]
    assert result["questions"][0]["question"] == "是否批准 Skill 'demo-skill' 的 1 条演进经验？"
    assert [option["value"] for option in result["questions"][0]["options"]] == [
        "allow_once",
        "allow_always",
        "reject",
    ]


def test_skill_evolution_tool_name_without_detail_is_classified():
    interaction = SimpleNamespace(
        id="call_123",
        value={
            "message": "Skill evolution approval required.",
            "tool_name": "simplify_skill_experiences",
        },
    )

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert result["source"] == "skill_evolution_approval"
    assert result["approval_schema"] == "openjiuwen.skill_evolution_approval.v1"
    assert result["evolution_meta"]["approval_kind"] == "simplify"
    assert result["questions"][0]["question"] == "Skill evolution approval required."
