# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

import pytest

from jiuwenswarm.agents.harness.common import memory_rpc


@pytest.mark.asyncio
async def test_coding_memory_dir_is_project_scoped_under_workspace(tmp_path):
    workspace = tmp_path / "agent_workspace"
    project_dir = tmp_path / "project"
    coding_memory_dir = workspace / "coding_memory" / "project"
    coding_memory_dir.mkdir(parents=True)

    result = await memory_rpc.handle_memory_open(
        str(workspace),
        "agent.plan",
        {"project_dir": str(project_dir)},
    )

    assert Path(result["coding_memory_dir"]) == coding_memory_dir


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["team", "team.plan", "code.team"])
async def test_generic_memory_rpc_rejects_team_modes(tmp_path, mode):
    workspace = tmp_path / "agent_workspace"
    params = {"path": "JIUWENSWARM.md", "project_dir": str(tmp_path / "project")}

    with pytest.raises(memory_rpc.MemoryModeNotSupportedError):
        await memory_rpc.handle_memory_list(str(workspace), mode, {})
    with pytest.raises(memory_rpc.MemoryModeNotSupportedError):
        await memory_rpc.handle_memory_edit(str(workspace), mode, params)
    with pytest.raises(memory_rpc.MemoryModeNotSupportedError):
        await memory_rpc.handle_memory_status(str(workspace), mode, {})
    with pytest.raises(memory_rpc.MemoryModeNotSupportedError):
        await memory_rpc.handle_memory_toggle(
            str(workspace), mode, {"key": "memory_enabled"}
        )
    with pytest.raises(memory_rpc.MemoryModeNotSupportedError):
        await memory_rpc.handle_memory_open(str(workspace), mode, params)
