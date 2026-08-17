import asyncio
from pathlib import Path
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    build_permission_rail,
    convert_interactions_to_ask_user_question,
    resolve_permission_workspace_dir,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_permission_context import (
    PERMISSION_TASK_WORKSPACE,
    TOOL_PERMISSION_SESSION_ID,
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


def _patch_agent_workspace(monkeypatch, agent_ws: Path) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_workspace_dir",
        lambda: agent_ws,
    )
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_agent_workspace_dir",
        lambda: agent_ws,
    )


def test_permission_workspace_uses_session_dir_when_cwd_is_artifact(tmp_path, monkeypatch):
    """CwdState 被 DeepAgent 写成 artifact 根时，回退到会话任务目录而不是放行父目录。"""
    from openjiuwen.core.sys_operation.cwd import _cwd_state, init_cwd

    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    _patch_agent_workspace(monkeypatch, agent_ws)
    monkeypatch.setattr(
        "jiuwenswarm.common.utils.get_default_project_session_workspace_dir",
        lambda session_id=None: project,
    )
    token = _cwd_state.set(None)
    perm_token = PERMISSION_TASK_WORKSPACE.set("")
    try:
        init_cwd(str(agent_ws), project_root=str(agent_ws), workspace=str(agent_ws))
        sid = TOOL_PERMISSION_SESSION_ID.set("web_xxx")
        try:
            resolved = resolve_permission_workspace_dir()
            assert resolved == project.resolve()
        finally:
            TOOL_PERMISSION_SESSION_ID.reset(sid)
    finally:
        PERMISSION_TASK_WORKSPACE.reset(perm_token)
        _cwd_state.reset(token)


def test_permission_host_uses_runtime_workspace_not_agent_root(tmp_path, monkeypatch):
    """file_guard workspace 应是当前任务目录，而不是 ~/.jiuwenswarm/agent/workspace。"""
    from openjiuwen.core.sys_operation.cwd import _cwd_state, init_cwd

    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    _patch_agent_workspace(monkeypatch, agent_ws)
    token = _cwd_state.set(None)
    perm_token = PERMISSION_TASK_WORKSPACE.set("")
    try:
        init_cwd(str(project), project_root=str(project), workspace=str(project))
        rail = build_permission_rail({"permissions": {"enabled": True, "mode": "auto"}})
        assert rail is not None
        resolved = Path(rail._host.resolve_workspace_dir()).resolve()
        assert resolved == project.resolve()
        assert resolved != agent_ws.resolve()
    finally:
        PERMISSION_TASK_WORKSPACE.reset(perm_token)
        _cwd_state.reset(token)


def test_permission_workspace_ignores_deepagent_artifact_root(tmp_path, monkeypatch):
    """DeepAgent 的 get_workspace() 是 artifact 根，file_guard 应改用任务 project_root。"""
    from openjiuwen.core.sys_operation.cwd import _cwd_state, init_cwd

    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    _patch_agent_workspace(monkeypatch, agent_ws)
    token = _cwd_state.set(None)
    perm_token = PERMISSION_TASK_WORKSPACE.set("")
    try:
        init_cwd(str(project), project_root=str(project), workspace=str(agent_ws))
        resolved = resolve_permission_workspace_dir()
        assert resolved is not None
        assert resolved == project.resolve()
        assert resolved != agent_ws.resolve()
    finally:
        PERMISSION_TASK_WORKSPACE.reset(perm_token)
        _cwd_state.reset(token)


def test_permission_workspace_ignores_leaked_seed_from_other_adapter(tmp_path, monkeypatch):
    """上一轮 adapter 留下的 PERMISSION_TASK_WORKSPACE 不得盖住本轮 CwdState。"""
    from openjiuwen.core.sys_operation.cwd import _cwd_state, init_cwd

    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    leaked = tmp_path / "fallback_project_workspace"
    project.mkdir(parents=True)
    leaked.mkdir(parents=True)
    _patch_agent_workspace(monkeypatch, agent_ws)
    token = _cwd_state.set(None)
    perm_token = PERMISSION_TASK_WORKSPACE.set(str(leaked))
    try:
        init_cwd(str(project), project_root=str(project), workspace=str(project))
        resolved = resolve_permission_workspace_dir()
        assert resolved == project.resolve()
        assert resolved != leaked.resolve()
    finally:
        PERMISSION_TASK_WORKSPACE.reset(perm_token)
        _cwd_state.reset(token)


def test_permission_workspace_does_not_fallback_to_agent_root(tmp_path, monkeypatch):
    """CwdState 缺失时不得把 agent 数据根当成 file_guard workspace。"""
    from openjiuwen.core.sys_operation.cwd import _cwd_state

    agent_ws = tmp_path / "workspace"
    agent_ws.mkdir()
    _patch_agent_workspace(monkeypatch, agent_ws)
    token = _cwd_state.set(None)
    perm_token = PERMISSION_TASK_WORKSPACE.set("")
    session_token = TOOL_PERMISSION_SESSION_ID.set("")
    try:
        resolved = resolve_permission_workspace_dir()
        assert resolved is None or resolved != agent_ws.resolve()
    finally:
        TOOL_PERMISSION_SESSION_ID.reset(session_token)
        PERMISSION_TASK_WORKSPACE.reset(perm_token)
        _cwd_state.reset(token)


@pytest.mark.asyncio
async def test_permission_sync_does_not_clobber_task_workspace_when_cwd_missing(
    tmp_path, monkeypatch
):
    """check 时 ContextVar 丢失，不得用 agent 根覆盖已经绑好的任务目录。"""
    from openjiuwen.core.sys_operation.cwd import _cwd_state, init_cwd
    from openjiuwen.harness.security.models import PermissionLevel

    agent_ws = tmp_path / "workspace"
    project = agent_ws / "projects" / "web_xxx"
    project.mkdir(parents=True)
    leak = agent_ws / "test1.txt"
    _patch_agent_workspace(monkeypatch, agent_ws)
    token = _cwd_state.set(None)
    try:
        init_cwd(str(project), project_root=str(project), workspace=str(project))
        rail = build_permission_rail({"permissions": {"enabled": True, "mode": "auto"}})
        assert rail is not None
        lost = _cwd_state.set(None)
        lost_perm = PERMISSION_TASK_WORKSPACE.set("")
        lost_sid = TOOL_PERMISSION_SESSION_ID.set("")
        try:
            sync = getattr(rail, "_sync_workspace_root_from_host", None)
            if callable(sync):
                sync()
            result = await rail._engine.check_permission(
                "write_file",
                {"file_path": str(leak), "content": "3234"},
            )
            assert result.permission == PermissionLevel.ASK
        finally:
            TOOL_PERMISSION_SESSION_ID.reset(lost_sid)
            PERMISSION_TASK_WORKSPACE.reset(lost_perm)
            _cwd_state.reset(lost)
    finally:
        _cwd_state.reset(token)
