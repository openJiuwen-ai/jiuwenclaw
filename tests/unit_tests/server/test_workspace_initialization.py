# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

from jiuwenswarm.server.workspace_initialization import should_prepare_workspace


def test_workspace_initialization_runs_when_config_exists_but_user_file_is_missing(
    tmp_path: Path,
) -> None:
    config_file = tmp_path / "config" / "config.yaml"
    config_file.parent.mkdir()
    config_file.touch()
    new_workspace = tmp_path / "agent" / "workspace"
    new_workspace.mkdir(parents=True)
    old_workspace = tmp_path / "agent" / "jiuwenclaw_workspace"

    assert should_prepare_workspace(config_file, new_workspace, old_workspace)
