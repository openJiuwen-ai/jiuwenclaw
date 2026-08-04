# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

import pytest

from jiuwenswarm.agents.harness.common import memory_rpc
from jiuwenswarm.common.coding_memory_paths import resolve_project_coding_memory_dir


@pytest.mark.asyncio
async def test_coding_memory_dir_is_project_scoped_under_workspace(tmp_path):
    workspace = tmp_path / "agent_workspace"
    project_dir = tmp_path / "project"
    coding_memory_dir = Path(
        resolve_project_coding_memory_dir(
            agent_workspace_dir=workspace,
            project_dir=project_dir,
        )
    )
    coding_memory_dir.mkdir(parents=True)

    result = await memory_rpc.handle_memory_open(
        str(workspace),
        {"project_dir": str(project_dir)},
    )

    assert Path(result["coding_memory_dir"]) == coding_memory_dir
