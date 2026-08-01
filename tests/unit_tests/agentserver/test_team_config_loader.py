# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for team config loading."""

from pathlib import Path

from jiuwenclaw.agentserver.team.config_loader import (
    resolve_team_sqlite_db_path,
)


def test_resolve_team_sqlite_db_path_defaults_to_agent_teams_home(monkeypatch, tmp_path):
    """Missing connection_string should fall back to openjiuwen agent-teams team.db."""
    config = {
        "team": {
            "storage": {
                "type": "sqlite",
                "params": {},
            }
        }
    }

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_config",
        lambda: config,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.config_loader.get_agent_teams_home",
        lambda: tmp_path / ".agent_teams",
    )

    db_path = resolve_team_sqlite_db_path()

    assert db_path == Path(tmp_path / ".agent_teams" / "team.db")
