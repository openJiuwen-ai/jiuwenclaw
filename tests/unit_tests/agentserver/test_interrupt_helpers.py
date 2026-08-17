import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    build_permission_rail,
    convert_interactions_to_ask_user_question,
)


def _evolution_interrupt(
    tool_name: str,
    operation: str,
    *,
    metadata: dict | None = None,
):
    if operation == "evolve":
        message = "是否批准 Skill 'demo-skill' 的 1 条演进经验？"
    else:
        message = "是否执行 Skill 'demo-skill' 的 1 项经验精简操作？"

    value = {
        "message": message,
        "tool_name": tool_name,
        "metadata": metadata
        or {
            "source": "evolution_interrupt",
            "interrupt_kind": "skill_evolution_approval",
        },
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


@pytest.mark.parametrize(
    ("tool_name", "operation", "approval_kind", "question"),
    [
        (
            "simplify_skill_experiences",
            "simplify",
            "simplify",
            "是否执行 Skill 'demo-skill' 的 1 项经验精简操作？",
        ),
        (
            "evolve_skill_experiences",
            "evolve",
            "evolve",
            "是否批准 Skill 'demo-skill' 的 1 条演进经验？",
        ),
    ],
)
def test_structured_evolution_approval_interrupt_is_classified(
    tool_name,
    operation,
    approval_kind,
    question,
):
    interaction = _evolution_interrupt(tool_name, operation)

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert result["source"] == "evolution_interrupt"
    assert result["approval_kind"] == approval_kind
    assert "approval_schema" not in result
    assert "evolution_meta" not in result
    assert "rail_kind" not in result
    assert "approval_detail" not in result["questions"][0]
    assert result["questions"][0]["question"] == question
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
    assert result["source"] == "evolution_interrupt"
    assert result["approval_kind"] == "simplify"
    assert result["questions"][0]["question"] == "Skill evolution approval required."


def test_legacy_skill_evolution_approval_metadata_is_classified():
    interaction = _evolution_interrupt(
        "evolve_skill_experiences",
        "evolve",
        metadata={"source": "skill_evolution_approval"},
    )

    result = convert_interactions_to_ask_user_question([interaction])

    assert result is not None
    assert result["source"] == "evolution_interrupt"
    assert result["approval_kind"] == "evolve"


def test_permission_interrupt_uses_ask_title_from_metadata():
    interaction = SimpleNamespace(
        id="call_perm",
        value={
            "message": "write C:\\Users\\hanzhibin\\test1.txt\n\n工具: `write_file`\n",
            "tool_name": "write_file",
            "tool_args": {"file_path": r"C:\Users\hanzhibin\test1.txt"},
            "metadata": {
                "ask_category": "path",
                "ask_title": "检测到受保护的文件路径访问",
                "ask_summary": r"write C:\Users\hanzhibin\test1.txt",
            },
            "ui_options": [
                {"label": "仅本次", "value": "allow_once"},
                {"label": "拒绝", "value": "reject"},
            ],
        },
    )
    result = convert_interactions_to_ask_user_question([interaction])
    assert result is not None
    assert result["source"] == "permission_interrupt"
    assert result["questions"][0]["header"] == "检测到受保护的文件路径访问"
    assert result["questions"][0]["question"].startswith("write ")


def _scene_hook_input(normalized_tool_name: str, user_input):
    from openjiuwen.harness.security.host import PermissionSceneHookInput

    return PermissionSceneHookInput(
        ctx=SimpleNamespace(session=None),
        tool_call=SimpleNamespace(id="call_1", name=normalized_tool_name, arguments={}),
        user_input=user_input,
        normalized_tool_name=normalized_tool_name,
        tool_args={},
        engine=None,
    )


def _permission_scene_hook():
    rail = build_permission_rail({"permissions": {"enabled": True}})
    assert rail is not None
    hook = rail._host.permission_scene_hook
    assert hook is not None
    return hook


def test_scene_hook_approves_ask_user_on_resume():
    """Regression for issue #1976.

    The permission rail intercepts every tool. On resume it would otherwise
    grab the ask_user answer as its own user_input and re-raise a permission
    interrupt, making the option card re-pop forever. The scene hook must
    approve ask_user so its answer reaches the model.
    """
    hook = _permission_scene_hook()
    resume_answer = {"answers": {"__free_text__": "数据处理"}, "original_request": "..."}

    outcome = asyncio.run(hook(_scene_hook_input("ask_user", resume_answer)))

    assert outcome == ("approve",)


def test_scene_hook_approves_ask_user_on_first_pass():
    hook = _permission_scene_hook()

    outcome = asyncio.run(hook(_scene_hook_input("ask_user", None)))

    assert outcome == ("approve",)


def test_scene_hook_leaves_other_tools_to_engine():
    """Non-interactive tools must still fall through to the tiered engine
    (returns ``None``) when no owner-scope context is set."""
    hook = _permission_scene_hook()

    outcome = asyncio.run(hook(_scene_hook_input("bash", None)))

    assert outcome is None


def test_filter_init_kwargs_drops_unknown_host_fields():
    from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
        _filter_init_kwargs,
    )

    class LegacyHost:
        def __init__(self, persist_allow_rule=None, host=None):
            self.persist_allow_rule = persist_allow_rule

    kwargs = _filter_init_kwargs(
        LegacyHost,
        persist_allow_rule=lambda _permissions: True,
        persist_session_allow_rule=lambda _permissions: True,
        host="unused",
    )
    assert "persist_allow_rule" in kwargs
    assert "persist_session_allow_rule" not in kwargs


def test_build_multi_questions_ignores_string_options():
    """Regression for #2331: options='a,b' must not become character options + Other."""
    from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
        _build_multi_questions,
    )

    questions = _build_multi_questions(
        [
            {
                "question": "Which option?",
                "header": "Choice",
                "options": "a,b",
            }
        ]
    )

    assert len(questions) == 1
    assert questions[0]["options"] == []


def test_build_multi_questions_appends_other_for_valid_options():
    from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
        _build_multi_questions,
    )

    questions = _build_multi_questions(
        [
            {
                "question": "Which option?",
                "header": "Choice",
                "options": [
                    {"label": "A", "description": "opt a"},
                    {"label": "B", "description": "opt b"},
                ],
            }
        ]
    )

    assert [opt["label"] for opt in questions[0]["options"]] == ["A", "B", "Other"]


def test_permission_host_uses_runtime_workspace_not_agent_root(tmp_path, monkeypatch):
    """file_guard workspace 应是当前任务目录，而不是 ~/.jiuwenswarm/agent/workspace。"""
    from openjiuwen.core.sys_operation.cwd import _cwd_state, init_cwd

    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_workspace_dir",
        lambda: agent_ws,
    )
    token = _cwd_state.set(None)
    try:
        init_cwd(str(project), project_root=str(project), workspace=str(project))
        rail = build_permission_rail({"permissions": {"enabled": True, "mode": "auto"}})
        assert rail is not None
        resolved = Path(rail._host.resolve_workspace_dir()).resolve()
        assert resolved == project.resolve()
        assert resolved != agent_ws.resolve()
    finally:
        _cwd_state.reset(token)
