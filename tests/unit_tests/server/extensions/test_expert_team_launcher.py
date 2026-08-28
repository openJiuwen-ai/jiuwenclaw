# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for JiuwenExpertTeamLauncher (activate + stop rollback)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.team.expert_org.launcher import (
    JiuwenExpertTeamLauncher,
    _align_spec_storage,
    _read_agent_group_capabilities,
)


class _FakeSpec:
    def __init__(self, team_name: str, metadata: dict | None = None) -> None:
        self.team_name = team_name
        self.metadata = dict(metadata or {"capabilities": ["frontend"]})
        self.storage = None
        self.leader = SimpleNamespace(display_name="Expert Leader", desc="leader public")

    def model_copy(self, update: dict) -> _FakeSpec:
        merged_metadata = dict(self.metadata)
        if "metadata" in update:
            merged_metadata.update(update["metadata"])
        return _FakeSpec(
            update.get("team_name", self.team_name),
            metadata=merged_metadata,
        )


class _FakeDb:
    def __init__(self, connection_string: str = "/tmp/shared-team.db") -> None:
        self.config = SimpleNamespace(
            db_type="sqlite",
            connection_string=connection_string,
            db_timeout=5,
            db_enable_wal=True,
        )


class _FakeAgent:
    def __init__(self, team_name: str, db: object) -> None:
        self.member_name = f"leader-{team_name}"
        self.spec = SimpleNamespace(metadata={"capabilities": ["frontend", "ui"]})
        self.build_team_calls: list[dict] = []

        async def _build_team(**kwargs):
            self.build_team_calls.append(kwargs)

        self.team_backend = SimpleNamespace(
            team_name=team_name,
            leader_member_name=f"leader-{team_name}",
            member_name=f"leader-{team_name}",
            db=db,
            task_manager=SimpleNamespace(db=db),
            message_manager=SimpleNamespace(db=db),
            build_team=_build_team,
        )


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

    async def teams_for_session(self, session_id: str):
        return []


class _FakeRuntime:
    def __init__(self, shared_db: object) -> None:
        self.shared_db = shared_db
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
        agent = _FakeAgent(spec.team_name, self.shared_db)
        caps = getattr(spec, "metadata", {}).get("capabilities", ["frontend", "ui"])
        agent.spec = SimpleNamespace(metadata={"capabilities": list(caps)})
        self.entries[spec.team_name] = agent
        return _FakeActivation(agent)

    async def stop_team(self, *, team_name: str, session_id: str) -> bool:
        self.stop_calls.append((team_name, session_id))
        self.entries.pop(team_name, None)
        return True

    async def pause(self, *, team_name: str, session_id: str) -> bool:
        self.pause_calls.append((team_name, session_id))
        return True


def test_align_spec_storage_uses_donor_connection_string(monkeypatch: pytest.MonkeyPatch) -> None:
    captured: dict[str, object] = {}

    class _StorageSpec:
        def __init__(self, type: str, params: dict) -> None:
            captured["type"] = type
            captured["params"] = params

    import sys

    monkeypatch.setitem(
        sys.modules,
        "openjiuwen.agent_teams.schema.blueprint",
        SimpleNamespace(StorageSpec=_StorageSpec),
    )

    spec = _FakeSpec("expert-team")
    donor_db = _FakeDb("/data/org-team.db")
    _align_spec_storage(spec, donor_db)
    assert captured["type"] == "sqlite"
    assert captured["params"]["connection_string"] == "/data/org-team.db"
    assert spec.storage is not None


@pytest.mark.asyncio
async def test_launch_creates_resolvable_team(monkeypatch: pytest.MonkeyPatch) -> None:
    shared_db = _FakeDb()
    runtime = _FakeRuntime(shared_db)
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
    assert launched.capabilities == ("frontend",)
    assert launched.to_dict()["team_id"] == launched.team_id

    assert runtime.activate_calls == [(launched.team_id, "sess-1")]
    assert runtime.pause_calls == [(launched.team_id, "sess-1")]
    assert runtime.stop_calls == []
    pooled = await runtime.pool.get(launched.team_id)
    assert pooled is not None
    assert pooled.agent.team_backend.team_name == launched.team_id


@pytest.mark.asyncio
async def test_launch_with_owner_uses_shared_db_for_task_and_message_managers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_db = _FakeDb("/tmp/org-owner.db")
    runtime = _FakeRuntime(shared_db)
    owner_agent = _FakeAgent("owner-team", shared_db)
    runtime.entries["owner-team"] = owner_agent

    launcher = JiuwenExpertTeamLauncher(runtime_manager=runtime, sequence_start=1)
    monkeypatch.setattr(launcher, "_validate_agent_group", lambda _name: None)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.expert_org.launcher._read_agent_group_instruction",
        lambda _name: "expert group instruction",
    )

    async def _fake_build(**kwargs):
        return _FakeSpec(kwargs["team_id"])

    monkeypatch.setattr(launcher, "_build_enriched_spec", _fake_build)

    launched = await launcher.launch(
        organization_id="org-1",
        agent_group_name="sample-expert-group",
        session_id="sess-1",
        display_name="Sample Experts",
        share_db_from_team_id="owner-team",
    )

    expert = runtime.entries[launched.team_id]
    assert expert.team_backend.db is shared_db
    assert expert.team_backend.task_manager.db is shared_db
    assert expert.team_backend.message_manager.db is shared_db
    assert expert.build_team_calls == [
        {
            "display_name": "Sample Experts",
            "desc": "expert group instruction",
            "leader_display_name": "Expert Leader",
            "leader_desc": "leader public",
        }
    ]
    assert runtime.pause_calls == [(launched.team_id, "sess-1")]


@pytest.mark.asyncio
async def test_launch_allocates_unique_team_ids(monkeypatch: pytest.MonkeyPatch) -> None:
    shared_db = _FakeDb()
    runtime = _FakeRuntime(shared_db)
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
    shared_db = _FakeDb()
    runtime = _FakeRuntime(shared_db)
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
    shared_db = _FakeDb()
    runtime = _FakeRuntime(shared_db)
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
    launcher = JiuwenExpertTeamLauncher(runtime_manager=_FakeRuntime(_FakeDb()))
    with pytest.raises(ValueError, match="agent_group_name"):
        await launcher.launch(
            organization_id="org-1",
            agent_group_name="  ",
            session_id="sess-1",
        )


def test_verify_shared_database_rejects_mismatched_task_manager_db() -> None:
    shared_db = _FakeDb()
    other_db = _FakeDb("/other.db")
    launcher = JiuwenExpertTeamLauncher()
    expert_agent = _FakeAgent("expert", shared_db)
    expert_agent.team_backend.task_manager = SimpleNamespace(db=other_db)
    donor_backend = SimpleNamespace(db=shared_db)

    with pytest.raises(ValueError, match="task_manager must use the shared TeamDatabase"):
        launcher._verify_shared_database(expert_agent, donor_backend)


def test_read_agent_group_capabilities_from_manifest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_dir = tmp_path / "finance-group"
    group_dir.mkdir()
    (group_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "finance-group",
                "package_type": "agent_group",
                "agents": ["leader"],
                "capabilities": ["finance", "risk"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.extension_package_manager.resolve_agent_group_dir",
        lambda _name: group_dir,
    )
    assert _read_agent_group_capabilities("finance-group") == ("finance", "risk")


def test_read_agent_group_capabilities_without_manifest_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    group_dir = tmp_path / "sample-expert-group"
    group_dir.mkdir()
    (group_dir / "manifest.json").write_text(
        json.dumps(
            {
                "name": "sample-expert-group",
                "package_type": "agent_group",
                "agents": ["leader"],
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.extension_package_manager.resolve_agent_group_dir",
        lambda _name: group_dir,
    )
    assert _read_agent_group_capabilities("sample-expert-group") == ()


@pytest.mark.asyncio
async def test_launch_reflects_metadata_capabilities_from_build(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    shared_db = _FakeDb()
    runtime = _FakeRuntime(shared_db)
    launcher = JiuwenExpertTeamLauncher(runtime_manager=runtime, sequence_start=1)
    monkeypatch.setattr(launcher, "_validate_agent_group", lambda _name: None)

    async def _fake_build(**kwargs):
        return _FakeSpec(
            kwargs["team_id"],
            metadata={"capabilities": ["finance", "risk"]},
        )

    monkeypatch.setattr(launcher, "_build_enriched_spec", _fake_build)

    launched = await launcher.launch(
        organization_id="org-1",
        agent_group_name="finance-group",
        session_id="sess-1",
    )
    assert launched.capabilities == ("finance", "risk")
