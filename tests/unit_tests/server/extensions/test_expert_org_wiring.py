# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for expert org adapter installer and shared-db launch."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.team.expert_org.launcher import JiuwenExpertTeamLauncher
from jiuwenswarm.agents.harness.team.expert_org.wiring import install_expert_org_adapters


class _OrgRuntime:
    def __init__(self, team_runtime_manager=None) -> None:
        self.catalog = None
        self.launcher = None
        self._team_runtime_manager = team_runtime_manager

    def set_expert_group_catalog(self, catalog) -> None:
        self.catalog = catalog

    def set_expert_team_launcher(self, launcher) -> None:
        self.launcher = launcher


def test_install_expert_org_adapters_injects_catalog_and_launcher() -> None:
    team_runtime = object()
    org = _OrgRuntime(team_runtime_manager=team_runtime)
    install_expert_org_adapters(org)
    assert org.catalog is not None
    assert org.launcher is not None
    assert org.launcher._runtime_manager is team_runtime

    catalog_id = id(org.catalog)
    launcher_id = id(org.launcher)
    install_expert_org_adapters(org)
    assert id(org.catalog) == catalog_id
    assert id(org.launcher) == launcher_id


def test_install_expert_org_adapters_noop_without_setters() -> None:
    install_expert_org_adapters(SimpleNamespace())  # should not raise


@pytest.mark.asyncio
async def test_launcher_shares_db_from_owner(monkeypatch: pytest.MonkeyPatch) -> None:
    shared_db = object()
    owner_backend = SimpleNamespace(db=shared_db)
    expert_backend = SimpleNamespace(db=object())

    class _Pool:
        async def get(self, team_name: str):
            if team_name == "owner-team":
                return SimpleNamespace(
                    team_name="owner-team",
                    agent=SimpleNamespace(team_backend=owner_backend),
                )
            if team_name == "expert-team":
                return SimpleNamespace(
                    team_name="expert-team",
                    agent=SimpleNamespace(team_backend=expert_backend),
                )
            return None

        async def teams_for_session(self, session_id: str):
            return []

    class _Runtime:
        def __init__(self) -> None:
            self.pool = _Pool()
            self.paused = False

        async def activate(self, spec, session_id):
            agent = SimpleNamespace(
                team_backend=expert_backend,
                member_name="leader-expert",
                spec=SimpleNamespace(metadata={"capabilities": ["frontend"]}),
            )
            return SimpleNamespace(agent=agent)

        async def pause(self, *, team_name, session_id):
            self.paused = True
            return True

        async def stop_team(self, *, team_name, session_id):
            return True

    runtime = _Runtime()
    launcher = JiuwenExpertTeamLauncher(runtime_manager=runtime, sequence_start=1)
    monkeypatch.setattr(launcher, "_validate_agent_group", lambda _name: None)

    async def _fake_build(**kwargs):
        return SimpleNamespace(team_name=kwargs["team_id"])

    monkeypatch.setattr(launcher, "_build_enriched_spec", _fake_build)

    launched = await launcher.launch(
        organization_id="org-1",
        agent_group_name="sample-expert-group",
        session_id="sess-1",
        share_db_from_team_id="owner-team",
    )
    assert launched.team_id == "org-org-1-sample-expert-group-1"
    assert expert_backend.db is shared_db
    assert runtime.paused is True
