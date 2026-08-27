# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for JiuwenExpertTeamLauncher (activate + stop rollback)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.team.expert_org.launcher import JiuwenExpertTeamLauncher


class _FakeSpec:
    def __init__(self, team_name: str) -> None:
        self.team_name = team_name
        self.metadata = {"capabilities": ["frontend"]}


class _FakeAgent:
    def __init__(self, team_name: str) -> None:
        self.member_name = f"leader-{team_name}"
        self.team_backend = SimpleNamespace(
            team_name=team_name,
            leader_member_name=f"leader-{team_name}",
            member_name=f"leader-{team_name}",
        )
        self.spec = SimpleNamespace(metadata={"capabilities": ["frontend", "ui"]})


class _FakeActivation:
    def __init__(self, agent: _FakeAgent) -> None:
        self.agent = agent


class _FakePool:
    def __init__(self, entries: dict[str, _FakeAgent]) -> None:
        self._entries = entries

    async def get(self, team_name: str):
        agent = self._entries.get(team_name)
        if agent is None:
            return None
        return SimpleNamespace(agent=agent, team_name=team_name)


class _FakeRuntime:
    def __init__(self) -> None:
        self.entries: dict[str, _FakeAgent] = {}
        self.activate_calls: list[tuple[str, str]] = []
        self.stop_calls: list[tuple[str, str]] = []
        self.pause_calls: list[tuple[str, str]] = []
        self.fail_activate = False

    @property
    def pool(self) -> _FakePool:
        return _FakePool(self.entries)

    async def activate(self, spec, session_id):
        self.activate_calls.append((spec.team_name, session_id))
        if self.fail_activate:
            raise RuntimeError("activate failed")
        agent = _FakeAgent(spec.team_name)
        self.entries[spec.team_name] = agent
        return _FakeActivation(agent)

    async def stop_team(self, *, team_name: str, session_id: str) -> bool:
        self.stop_calls.append((team_name, session_id))
        self.entries.pop(team_name, None)
        return True

    async def pause(self, *, team_name: str, session_id: str) -> bool:
        self.pause_calls.append((team_name, session_id))
        return True


@pytest.mark.asyncio
async def test_launch_creates_resolvable_team(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _FakeRuntime()
    launcher = JiuwenExpertTeamLauncher(runtime_manager=runtime, sequence_start=1)
    monkeypatch.setattr(launcher, "_validate_agent_group", lambda _name: None)

    async def _fake_build(**kwargs):
        return _FakeSpec(kwargs["team_id"])

    monkeypatch.setattr(launcher, "_build_enriched_spec", _fake_build)

    launched = await launcher.launch(
        organization_id="org-1",
        agent_group_name="sample-expert-group",
        session_id="sess-1",
        display_name="Sample",
    )

    assert launched.team_id == "org-org-1-sample-expert-group-1"
    assert launched.agent_group_name == "sample-expert-group"
    assert launched.leader_id == "leader-org-org-1-sample-expert-group-1"
    assert launched.capabilities == ("frontend", "ui")
    assert launched.to_dict()["team_id"] == launched.team_id

    assert runtime.activate_calls == [(launched.team_id, "sess-1")]
    assert runtime.pause_calls == [(launched.team_id, "sess-1")]
    assert runtime.stop_calls == []
    pooled = await runtime.pool.get(launched.team_id)
    assert pooled is not None
    assert pooled.agent.team_backend.team_name == launched.team_id


@pytest.mark.asyncio
async def test_launch_allocates_unique_team_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _FakeRuntime()
    launcher = JiuwenExpertTeamLauncher(runtime_manager=runtime, sequence_start=7)
    monkeypatch.setattr(launcher, "_validate_agent_group", lambda _name: None)

    async def _fake_build(**kwargs):
        return _FakeSpec(kwargs["team_id"])

    monkeypatch.setattr(launcher, "_build_enriched_spec", _fake_build)

    first = await launcher.launch(
        organization_id="org",
        agent_group_name="frontend-group",
        session_id="s",
    )
    second = await launcher.launch(
        organization_id="org",
        agent_group_name="frontend-group",
        session_id="s",
    )
    assert first.team_id == "org-org-frontend-group-7"
    assert second.team_id == "org-org-frontend-group-8"
    assert first.team_id != second.team_id


@pytest.mark.asyncio
async def test_launch_rolls_back_when_activate_fails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    runtime = _FakeRuntime()
    runtime.fail_activate = True
    launcher = JiuwenExpertTeamLauncher(runtime_manager=runtime)
    monkeypatch.setattr(launcher, "_validate_agent_group", lambda _name: None)

    async def _fake_build(**kwargs):
        return _FakeSpec(kwargs["team_id"])

    monkeypatch.setattr(launcher, "_build_enriched_spec", _fake_build)

    with pytest.raises(RuntimeError, match="activate failed"):
        await launcher.launch(
            organization_id="org-1",
            agent_group_name="sample-expert-group",
            session_id="sess-1",
        )

    assert runtime.stop_calls == [("org-org-1-sample-expert-group-1", "sess-1")]
    assert await runtime.pool.get("org-org-1-sample-expert-group-1") is None


@pytest.mark.asyncio
async def test_stop_removes_team(monkeypatch: pytest.MonkeyPatch) -> None:
    runtime = _FakeRuntime()
    launcher = JiuwenExpertTeamLauncher(runtime_manager=runtime)
    monkeypatch.setattr(launcher, "_validate_agent_group", lambda _name: None)

    async def _fake_build(**kwargs):
        return _FakeSpec(kwargs["team_id"])

    monkeypatch.setattr(launcher, "_build_enriched_spec", _fake_build)

    launched = await launcher.launch(
        organization_id="org-1",
        agent_group_name="g",
        session_id="sess-1",
    )
    await launcher.stop(team_id=launched.team_id, session_id="sess-1")
    assert await runtime.pool.get(launched.team_id) is None


@pytest.mark.asyncio
async def test_launch_requires_agent_group_name() -> None:
    launcher = JiuwenExpertTeamLauncher(runtime_manager=_FakeRuntime())
    with pytest.raises(ValueError, match="agent_group_name"):
        await launcher.launch(
            organization_id="org-1",
            agent_group_name="  ",
            session_id="sess-1",
        )
