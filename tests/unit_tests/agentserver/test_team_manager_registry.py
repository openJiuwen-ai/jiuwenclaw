# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for channel-scoped team manager registry behavior."""

from __future__ import annotations

import asyncio
from pathlib import Path

import pytest

from jiuwenclaw.agents.harness.team.team_manager import (
    TeamManager,
    TeamRailMountContext,
    MemberInfo,
    RuntimeInfo,
    TeamWorkspaceInfo,
    get_team_manager,
    reset_team_manager,
    sync_team_skills_across_managers,
)


class _TeamManagerHarness(TeamManager):
    def set_active_runtime_for_test(self, session_id: str, team_name: str) -> None:
        self.commit_runtime_ready(session_id, team_name)

    def set_pending_runtime_for_test(self, session_id: str, team_name: str) -> None:
        setattr(self, "_pending_session_id", session_id)
        setattr(self, "_pending_team_name", team_name)

    def cache_local_team_agent_for_test(self, session_id: str, team_agent) -> None:
        getattr(self, "_team_agents")[session_id] = team_agent

    def resolve_session_team_name_for_test(self, session_id: str) -> str | None:
        return self._resolve_session_team_name(session_id)


class _FakeRail:
    pass


class _FakeSkillEvolutionRail:
    def __init__(self, auto_scan: bool = True) -> None:
        self.auto_scan = auto_scan


class _FakeTeamSkillRail:
    pass


class _FakeTeamSkillCreateRail:
    pass


class _FakeAgent:
    def __init__(self) -> None:
        self.unregistered: list[object] = []
        self.added_rails: list[object] = []

    async def unregister_rail(self, rail: object):
        self.unregistered.append(rail)
        return self

    def add_rail(self, rail: object) -> None:
        self.added_rails.append(rail)


def setup_function() -> None:
    reset_team_manager()


def teardown_function() -> None:
    reset_team_manager()


def test_get_team_manager_is_scoped_by_channel() -> None:
    web_manager = get_team_manager("web")
    feishu_manager = get_team_manager("feishu")
    web_manager_again = get_team_manager("web")

    assert isinstance(web_manager, TeamManager)
    assert isinstance(feishu_manager, TeamManager)
    assert web_manager is web_manager_again
    assert web_manager is not feishu_manager


@pytest.mark.asyncio
async def test_update_evolution_config_updates_member_skill_evolution_auto_scan(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamManager()
    rail = _FakeSkillEvolutionRail(auto_scan=True)
    manager.register_team_member_skill_evolution_rail("sess-1", rail)

    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    await manager.update_evolution_config({"evolution": {"auto_scan": False}})

    assert rail.auto_scan is False

    await manager.update_evolution_config({"evolution": {"auto_scan": True}})

    assert rail.auto_scan is True


@pytest.mark.asyncio
async def test_update_evolution_config_disables_team_skill_rail_and_watcher(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamManager()
    rail = _FakeRail()
    agent = _FakeAgent()
    task = asyncio.create_task(asyncio.sleep(3600))

    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    manager.register_team_skill_rail("sess-1", rail)
    manager.register_team_live_rail("sess-1", agent, rail)
    manager.register_team_evolution_watcher("sess-1", task)

    await manager.update_evolution_config({"evolution": {"auto_scan": False}})

    assert manager.get_team_skill_rail("sess-1") is None
    assert manager.get_team_evolution_watcher("sess-1") is None
    assert agent.unregistered == [rail]
    assert task.cancelled()


@pytest.mark.asyncio
async def test_update_evolution_config_disables_team_skill_create_rail() -> None:
    manager = TeamManager()
    rail = _FakeRail()
    agent = _FakeAgent()

    manager.register_team_skill_create_rail("sess-1", rail)
    manager.register_team_live_rail("sess-1", agent, rail)

    await manager.update_evolution_config({"evolution": {"skill_create": False}})

    assert manager.get_team_skill_create_rail("sess-1") is None
    assert agent.unregistered == [rail]


@pytest.mark.asyncio
async def test_update_evolution_config_enabled_does_not_mount_missing_rails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamManager()
    rail = _FakeSkillEvolutionRail(auto_scan=False)
    manager.register_team_member_skill_evolution_rail("sess-1", rail)

    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    monkeypatch.delenv("SKILL_CREATE", raising=False)
    await manager.update_evolution_config(
        {"evolution": {"auto_scan": True, "skill_create": True}}
    )

    assert rail.auto_scan is True
    assert manager.get_team_skill_rail("sess-1") is None
    assert manager.get_team_skill_create_rail("sess-1") is None


@pytest.mark.asyncio
async def test_update_evolution_config_recreates_missing_team_rails_when_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamManager()
    agent = _FakeAgent()
    context = TeamRailMountContext(
        agent=agent,
        member_info=MemberInfo(role="leader"),
        runtime=RuntimeInfo(channel="web"),
        team_workspace=TeamWorkspaceInfo(
            root_dir="/tmp/team",
            skills_dir="/tmp/team/skills",
            trajectories_dir="/tmp/team/trajectories",
            team_id="demo-team",
            config={},
        ),
    )
    manager.register_team_rail_context("sess-1", context)

    monkeypatch.delenv("EVOLUTION_AUTO_SCAN", raising=False)
    monkeypatch.delenv("SKILL_CREATE", raising=False)
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.build_member_rails",
        lambda **kwargs: (
            [_FakeTeamSkillRail(), _FakeTeamSkillCreateRail()]
            if kwargs["team_workspace"].config.get("evolution", {}).get("auto_scan")
            and kwargs["team_workspace"].config.get("evolution", {}).get("skill_create")
            else []
        ),
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.TeamSkillRail",
        _FakeTeamSkillRail,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.TeamSkillCreateRail",
        _FakeTeamSkillCreateRail,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.get_config",
        lambda: {"evolution": {"auto_scan": True, "skill_create": True}},
    )

    await manager.update_evolution_config(
        {"evolution": {"auto_scan": True, "skill_create": True}}
    )

    assert isinstance(manager.get_team_skill_rail("sess-1"), _FakeTeamSkillRail)
    assert isinstance(manager.get_team_skill_create_rail("sess-1"), _FakeTeamSkillCreateRail)
    assert len(agent.added_rails) == 2


@pytest.mark.asyncio
async def test_destroy_team_cleans_registered_evolution_rails(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = TeamManager()
    rail = _FakeRail()
    agent = _FakeAgent()

    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.release_a2x_reservations_for_team",
        lambda team_agent: None,
    )
    manager.register_team_skill_rail("sess-1", rail)
    manager.register_team_member_skill_evolution_rail("sess-1", rail)
    manager.register_team_skill_create_rail("sess-1", rail)
    manager.register_team_live_rail("sess-1", agent, rail)
    manager.register_team_skill_sync_target("sess-1", Path("/tmp/src"), Path("/tmp/dst"))
    manager.commit_runtime_ready("sess-1", "demo-team")

    cleaned = await manager.destroy_team("sess-1")

    assert cleaned is False
    assert manager.get_team_skill_rail("sess-1") is None
    assert manager.get_team_skill_create_rail("sess-1") is None
    assert not manager.has_team_skill_sync_target("sess-1")


@pytest.mark.asyncio
async def test_team_manager_keeps_single_session_per_channel(monkeypatch: pytest.MonkeyPatch) -> None:
    destroyed_sessions: list[str] = []
    created_sessions: list[str] = []
    stopped_messagers: list[str] = []

    class _FakeTeamAgent:
        def __init__(self, session_id: str) -> None:
            self.session_id = session_id
            self._messager = self._FakeMessager(session_id)

        class _FakeMessager:
            def __init__(self, session_id: str) -> None:
                self.session_id = session_id

            async def stop(self) -> None:
                stopped_messagers.append(self.session_id)

        async def destroy_team(self, force: bool = False) -> bool:
            _ = force
            destroyed_sessions.append(self.session_id)
            return True

    class _FakeWorkspace:
        root_path = None

    def fake_load_team_spec(session_id: str):
        class _Spec:
            team_name = f"team-{session_id}"
            agent_customizer = None
            workspace = _FakeWorkspace()

            @staticmethod
            def build() -> _FakeTeamAgent:
                created_sessions.append(session_id)
                return _FakeTeamAgent(session_id)

        return _Spec()

    monkeypatch.setattr(TeamManager, "_load_team_spec", staticmethod(fake_load_team_spec))
    # Mock _copy_global_skills_to_team_shared_dir to avoid file operations
    monkeypatch.setattr(
        TeamManager,
        "_copy_global_skills_to_team_shared_dir",
        staticmethod(lambda spec: None),
    )

    web_manager = get_team_manager("web")
    feishu_manager = get_team_manager("feishu")

    await web_manager.get_or_create_team("web-s1", deep_agent=object(), channel_id="web")
    await feishu_manager.get_or_create_team("fs-s1", deep_agent=object(), channel_id="feishu")
    await web_manager.get_or_create_team("web-s2", deep_agent=object(), channel_id="web")

    assert created_sessions == ["web-s1", "fs-s1", "web-s2"]
    assert destroyed_sessions == ["web-s1"]
    assert stopped_messagers == ["web-s1"]
    assert web_manager.get_team_agent("web-s1") is None
    assert isinstance(web_manager.get_team_agent("web-s2"), _FakeTeamAgent)
    assert isinstance(feishu_manager.get_team_agent("fs-s1"), _FakeTeamAgent)


@pytest.mark.asyncio
async def test_create_team_does_not_run_global_runtime_cleanup(monkeypatch: pytest.MonkeyPatch) -> None:
    class _FakeWorkspace:
        root_path = None

    def fake_load_team_spec(_session_id: str):
        class _Spec:
            team_name = "demo-team"
            agent_customizer = None
            workspace = _FakeWorkspace()

            @staticmethod
            def build():
                return object()

        return _Spec()

    monkeypatch.setattr(TeamManager, "_load_team_spec", staticmethod(fake_load_team_spec))
    # Mock _copy_global_skills_to_team_shared_dir to avoid file operations
    monkeypatch.setattr(
        TeamManager,
        "_copy_global_skills_to_team_shared_dir",
        staticmethod(lambda spec: None),
    )
    manager = TeamManager()

    team_agent = await manager.create_team("sess-1", deep_agent=object(), channel_id="web")

    assert team_agent is not None
    assert manager.get_team_agent("sess-1") is team_agent


@pytest.mark.asyncio
async def test_create_team_appends_session_id_to_feishu_team_name(monkeypatch: pytest.MonkeyPatch) -> None:
    created_team_names: list[str] = []

    class _FakeWorkspace:
        root_path = None

    class _Spec:
        def __init__(self) -> None:
            self.team_name = "demo_team"
            self.agent_customizer = None
            self.workspace = _FakeWorkspace()

        def build(self):
            created_team_names.append(self.team_name)
            return object()

    monkeypatch.setattr(TeamManager, "_load_team_spec", staticmethod(lambda _session_id: _Spec()))
    monkeypatch.setattr(
        TeamManager,
        "_copy_global_skills_to_team_shared_dir",
        staticmethod(lambda spec: None),
    )
    manager = TeamManager()

    team_agent = await manager.create_team("oc_abc123", deep_agent=object(), channel_id="feishu")

    assert team_agent is not None
    assert created_team_names == ["demo_team_oc_abc123"]


@pytest.mark.asyncio
async def test_create_team_keeps_non_feishu_team_name(monkeypatch: pytest.MonkeyPatch) -> None:
    created_team_names: list[str] = []

    class _FakeWorkspace:
        root_path = None

    class _Spec:
        def __init__(self) -> None:
            self.team_name = "demo_team"
            self.agent_customizer = None
            self.workspace = _FakeWorkspace()

        def build(self):
            created_team_names.append(self.team_name)
            return object()

    monkeypatch.setattr(TeamManager, "_load_team_spec", staticmethod(lambda _session_id: _Spec()))
    monkeypatch.setattr(
        TeamManager,
        "_copy_global_skills_to_team_shared_dir",
        staticmethod(lambda spec: None),
    )
    manager = TeamManager()

    team_agent = await manager.create_team("oc_abc123", deep_agent=object(), channel_id="web")

    assert team_agent is not None
    assert created_team_names == ["demo_team"]


@pytest.mark.asyncio
async def test_prepare_session_switch_stops_other_active_and_pending_sessions(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()
    manager.set_active_runtime_for_test("sess-active", "team-active")
    manager.set_pending_runtime_for_test("sess-pending", "team-pending")

    stopped: list[tuple[str, str]] = []

    async def fake_stop(self, session_id: str, reason: str = "") -> bool:
        stopped.append((session_id, reason))
        return True

    monkeypatch.setattr(
        TeamManager,
        "stop_session_runtime",
        fake_stop,
    )

    await manager.prepare_session_switch("sess-target", reason="session switch: ")

    assert stopped == [
        ("sess-active", "session switch: "),
        ("sess-pending", "session switch: "),
    ]


@pytest.mark.asyncio
async def test_delete_session_runtime_releases_single_team_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()
    manager.set_active_runtime_for_test("sess-1", "demo-team")

    stopped: list[tuple[str, str]] = []
    released: list[str] = []

    async def fake_stop(self, session_id: str, reason: str = "") -> bool:
        stopped.append((session_id, reason))
        return True

    async def fake_release(session_id: str) -> None:
        released.append(session_id)

    monkeypatch.setattr(TeamManager, "stop_session_runtime", fake_stop)
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.Runner.release",
        fake_release,
    )

    deleted = await manager.delete_session_runtime("sess-1", reason="session.delete: ")

    assert deleted is True
    assert stopped == [("sess-1", "session.delete: ")]
    assert released == ["sess-1"]


@pytest.mark.asyncio
async def test_stop_session_runtime_stops_runner_owned_team_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()
    manager.set_active_runtime_for_test("sess-1", "demo-team")

    stop_calls: list[tuple[str, str]] = []

    async def fake_stop_agent_team(*, team_name: str, session_id: str) -> bool:
        stop_calls.append((team_name, session_id))
        return True

    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.Runner.stop_agent_team",
        fake_stop_agent_team,
    )

    stopped = await manager.stop_session_runtime("sess-1", reason="switch runtime: ")

    assert stopped is True
    assert stop_calls == [("demo-team", "sess-1")]
    assert manager.active_session_id is None
    assert manager.active_team_name is None


@pytest.mark.asyncio
async def test_pause_session_runtime_pauses_runner_owned_team_runtime(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()
    manager.set_active_runtime_for_test("sess-1", "demo-team")

    pause_calls: list[tuple[str, str]] = []

    async def fake_pause_agent_team(*, team_name: str, session_id: str) -> bool:
        pause_calls.append((team_name, session_id))
        return True

    async def fake_stop_agent_team(*, team_name: str, session_id: str) -> bool:
        raise AssertionError("pause should not stop the Runner-owned team runtime")

    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.Runner.pause_agent_team",
        fake_pause_agent_team,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.Runner.stop_agent_team",
        fake_stop_agent_team,
    )

    paused = await manager.pause_session_runtime("sess-1", reason="interrupt(intent=pause): ")

    assert paused is True
    assert pause_calls == [("demo-team", "sess-1")]
    assert manager.active_session_id is None
    assert manager.active_team_name is None


@pytest.mark.asyncio
async def test_interact_uses_runner_only_for_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()
    manager.set_active_runtime_for_test("sess-1", "demo-team")

    class _LocalTeamAgent:
        async def interact(self, _user_input: str) -> None:
            raise AssertionError("single-machine interact should not use local TeamAgent")

    interact_calls: list[tuple[str, str, str]] = []

    async def fake_interact_agent_team(user_input: str, *, team_name: str, session_id: str) -> bool:
        interact_calls.append((user_input, team_name, session_id))
        return True

    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.Runner.interact_agent_team",
        fake_interact_agent_team,
    )

    success = await manager.interact("sess-1", "hello team")

    assert success is True
    assert interact_calls == [("hello team", "demo-team", "sess-1")]


@pytest.mark.asyncio
async def test_interact_returns_false_for_non_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()
    manager.set_active_runtime_for_test("sess-active", "demo-team")

    interact_calls: list[tuple[str, str, str]] = []

    async def fake_interact_agent_team(user_input: str, *, team_name: str, session_id: str) -> bool:
        interact_calls.append((user_input, team_name, session_id))
        return True

    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.Runner.interact_agent_team",
        fake_interact_agent_team,
    )

    success = await manager.interact("sess-other", "hello team")

    assert success is False
    assert interact_calls == []


@pytest.mark.asyncio
async def test_stop_session_runtime_ignores_local_team_cache_in_single_machine_mode(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()
    manager.set_active_runtime_for_test("sess-1", "demo-team")

    class _LocalTeamAgent:
        async def destroy_team(self, force: bool = False) -> bool:
            _ = force
            raise AssertionError("single-machine stop should not destroy local TeamAgent cache")

    stop_calls: list[tuple[str, str]] = []

    async def fake_stop_agent_team(*, team_name: str, session_id: str) -> bool:
        stop_calls.append((team_name, session_id))
        return True

    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.Runner.stop_agent_team",
        fake_stop_agent_team,
    )

    manager.cache_local_team_agent_for_test("sess-1", _LocalTeamAgent())

    stopped = await manager.stop_session_runtime("sess-1", reason="switch runtime: ")

    assert stopped is True
    assert stop_calls == [("demo-team", "sess-1")]


@pytest.mark.asyncio
async def test_stop_session_runtime_uses_metadata_team_name_for_non_active_session(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()
    manager.register_stream_task("sess-1", asyncio.create_task(asyncio.sleep(0)))

    stop_calls: list[tuple[str, str]] = []

    async def fake_stop_agent_team(*, team_name: str, session_id: str) -> bool:
        stop_calls.append((team_name, session_id))
        return True

    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.Runner.stop_agent_team",
        fake_stop_agent_team,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.get_session_metadata",
        lambda _session_id: {"team_name": "meta-team"},
    )

    stopped = await manager.stop_session_runtime("sess-1", reason="switch runtime: ")

    assert stopped is True
    assert stop_calls == [("meta-team", "sess-1")]


@pytest.mark.asyncio
async def test_delete_session_runtime_uses_metadata_team_name(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()

    stop_calls: list[tuple[str, str]] = []
    released: list[str] = []

    async def fake_stop(self, session_id: str, reason: str = "") -> bool:
        stop_calls.append((session_id, reason))
        return True

    async def fake_release(session_id: str) -> None:
        released.append(session_id)

    monkeypatch.setattr(TeamManager, "stop_session_runtime", fake_stop)
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.Runner.release",
        fake_release,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.get_session_metadata",
        lambda _session_id: {"team_name": "meta-team"},
    )

    deleted = await manager.delete_session_runtime("sess-1", reason="session.delete: ")

    assert deleted is True
    assert stop_calls == [("sess-1", "session.delete: ")]
    assert released == ["sess-1"]


def test_sync_team_skills_across_managers_uses_public_lookup(monkeypatch: pytest.MonkeyPatch) -> None:
    manager = get_team_manager("web")
    source = Path("/tmp/team-source")
    target = Path("/tmp/team-target")
    manager.register_team_skill_sync_target("sess-1", source, target)

    called = {"count": 0}

    def fake_sync(session_id: str) -> None:
        called["count"] += 1
        assert session_id == "sess-1"

    monkeypatch.setattr(manager, "sync_team_skills", fake_sync)

    assert sync_team_skills_across_managers("sess-1") is True
    assert called["count"] == 1


def test_resolve_session_team_name_returns_none_when_metadata_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    manager = _TeamManagerHarness()
    monkeypatch.setattr(
        "jiuwenclaw.agents.harness.team.team_manager.get_session_metadata",
        lambda _session_id: {},
    )

    team_name = manager.resolve_session_team_name_for_test("sess-missing")

    assert team_name is None
