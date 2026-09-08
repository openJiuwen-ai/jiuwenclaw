from __future__ import annotations

from pathlib import Path

import pytest
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
