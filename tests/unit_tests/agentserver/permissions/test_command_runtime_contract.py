"""Owning tests for the command working-directory contract."""

from __future__ import annotations

from pathlib import Path
from types import MappingProxyType, SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.rails.permissions._auto_permission import (
    invocation_context,
)
from jiuwenswarm.agents.harness.common.rails.permissions._auto_permission.models import (
    ToolInvocation,
)
from jiuwenswarm.agents.harness.common.rails.permissions.tool_decision_facts import (
    build_tool_decision_facts,
)
from jiuwenswarm.agents.harness.common.tools.command_runtime import (
    CommandRuntimePaths,
    current_command_runtime_paths,
    resolve_command_workdir,
)


def _runtime_paths(tmp_path: Path) -> CommandRuntimePaths:
    workspace = tmp_path / "workspace"
    cwd = workspace / "project"
    cwd.mkdir(parents=True)
    return CommandRuntimePaths(
        current_cwd=cwd,
        project_root=workspace,
        workspace_root=workspace,
        agent_workspace_root=workspace,
    )


@pytest.mark.parametrize(
    ("workdir", "suffix"),
    [
        (None, "project"),
        ("", "project"),
        (".", "project"),
        ("outputs", "project/outputs"),
    ],
)
def test_resolves_workdir_from_runtime_cwd(
    tmp_path: Path,
    workdir: object,
    suffix: str,
) -> None:
    paths = _runtime_paths(tmp_path)

    resolved = resolve_command_workdir(workdir, runtime_paths=paths)

    assert resolved == paths.workspace_root / suffix


def test_resolves_absolute_workdir_without_rebasing(tmp_path: Path) -> None:
    paths = _runtime_paths(tmp_path)
    target = paths.workspace_root / "other"

    assert resolve_command_workdir(str(target), runtime_paths=paths) == target


def test_required_runtime_cwd_does_not_guess_workspace(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from openjiuwen.core.sys_operation import cwd as cwd_module

    monkeypatch.setattr(
        cwd_module,
        "get_cwd",
        lambda: (_ for _ in ()).throw(RuntimeError("missing")),
    )

    with pytest.raises(ValueError, match="runtime cwd is unavailable"):
        current_command_runtime_paths(require_runtime_cwd=True)


@pytest.mark.parametrize("workdir", ["../../outside", 42, ["."]])
def test_rejects_invalid_or_external_workdir(
    tmp_path: Path,
    workdir: object,
) -> None:
    paths = _runtime_paths(tmp_path)

    with pytest.raises(ValueError):
        resolve_command_workdir(workdir, runtime_paths=paths)


def test_freezes_effective_workdir_into_host_execution_args(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    tool_call = SimpleNamespace(
        name="mcp_exec_command",
        arguments={"command": "pwd", "workdir": "outputs"},
    )
    inputs = SimpleNamespace(
        tool_call=tool_call,
        tool_name="mcp_exec_command",
        tool_args=tool_call.arguments,
    )
    invocation = ToolInvocation(
        ctx=SimpleNamespace(inputs=inputs),
        tool_call=tool_call,
        tool_name="mcp_exec_command",
        tool_args={
            "command": "pwd",
            "workdir": "outputs",
            "call_goal": "display only",
        },
    )
    kwargs = {"tool_args": invocation.tool_args}
    monkeypatch.setattr(
        invocation_context,
        "current_command_runtime_paths",
        lambda **_kwargs: paths,
    )

    frozen, error = invocation_context._normalize_command_invocation_for_execution(
        invocation,
        kwargs,
    )

    expected = {
        "command": "pwd",
        "workdir": str(paths.current_cwd / "outputs"),
    }
    assert error == ""
    assert frozen.tool_args == expected
    assert tool_call.arguments == expected
    assert inputs.tool_args == expected
    assert kwargs["tool_args"] == expected
    facts = build_tool_decision_facts(
        frozen.tool_name,
        frozen.tool_args,
        workspace_root=paths.workspace_root,
        original_args_were_valid_object=True,
    )
    assert dict(facts.untrusted_args) == expected
    assert facts.command == "pwd"


def test_rejects_non_authoritative_cwd_field(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    invocation = ToolInvocation(
        ctx=SimpleNamespace(inputs=None),
        tool_call=SimpleNamespace(arguments={}),
        tool_name="mcp_exec_command",
        tool_args={"command": "pwd", "cwd": str(paths.current_cwd)},
    )
    monkeypatch.setattr(
        invocation_context,
        "current_command_runtime_paths",
        lambda **_kwargs: paths,
    )

    frozen, error = invocation_context._normalize_command_invocation_for_execution(
        invocation,
        {},
    )

    assert frozen is invocation
    assert error == "command_workdir_contract_invalid"


def test_writeback_failure_does_not_publish_frozen_args(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    paths = _runtime_paths(tmp_path)
    invocation = ToolInvocation(
        ctx=SimpleNamespace(inputs=None),
        tool_call=MappingProxyType({"arguments": {"command": "pwd"}}),
        tool_name="mcp_exec_command",
        tool_args={"command": "pwd"},
    )
    monkeypatch.setattr(
        invocation_context,
        "current_command_runtime_paths",
        lambda **_kwargs: paths,
    )

    frozen, error = invocation_context._normalize_command_invocation_for_execution(
        invocation,
        {},
    )

    assert frozen is invocation
    assert error == "command_workdir_writeback_failed"
