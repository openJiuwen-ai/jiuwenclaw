# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for skills.evolution.archives / rollback / rebuild host adapters."""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.server.runtime.agent_adapter import evolution_version as evolution_version_ctl
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.server.runtime.agent_manager import _DISK_ONLY_EVOLUTION_METHODS


def _write_skill(tmp_path: Path, name: str) -> Path:
    skills_dir = tmp_path / "skills"
    skill_dir = skills_dir / name
    skill_dir.mkdir(parents=True)
    skill_dir.joinpath("SKILL.md").write_text(
        f"---\nname: {name}\nversion: 1.0.0\n---\n# {name}\n",
        encoding="utf-8",
    )
    skill_dir.joinpath("evolutions.json").write_text(
        json.dumps(
            {
                "skill_id": name,
                "version": "v1.0.0",
                "updated_at": "live",
                "entries": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    return skills_dir


def _write_pair(tmp_path: Path, name: str, version: str, body: str) -> None:
    archive = tmp_path / "skills" / name / "archive"
    archive.mkdir(parents=True, exist_ok=True)
    archive.joinpath(f"SKILL.{version}.md").write_text(body, encoding="utf-8")
    archive.joinpath(f"evolutions.{version}.json").write_text(
        json.dumps(
            {
                "skill_id": name,
                "version": version,
                "updated_at": "archived",
                "entries": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )


def test_disk_only_methods_exclude_rebuild():
    assert "skills.evolution.archives" in _DISK_ONLY_EVOLUTION_METHODS
    assert "skills.evolution.rollback" in _DISK_ONLY_EVOLUTION_METHODS
    assert "skills.evolution.rebuild" not in _DISK_ONLY_EVOLUTION_METHODS


def test_safe_path_name_rejects_traversal():
    with pytest.raises(ValueError):
        evolution_version_ctl.safe_path_name("../evil", "skill")


def test_skill_md_fingerprint_changes_with_content(tmp_path: Path):
    path = tmp_path / "SKILL.md"
    path.write_text("a", encoding="utf-8")
    first = evolution_version_ctl.skill_md_fingerprint(str(path))
    path.write_text("b", encoding="utf-8")
    second = evolution_version_ctl.skill_md_fingerprint(str(path))
    assert first is not None and second is not None
    assert first != second


@pytest.mark.anyio
async def test_handle_skills_evolution_archives_lists_versions(tmp_path, monkeypatch):
    skills_dir = _write_skill(tmp_path, "demo-skill")
    _write_pair(tmp_path, "demo-skill", "v1.0.0", "# old\n")
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_skill_dirs", lambda: [str(skills_dir)])

    result = await adapter.handle_skills_evolution_archives({"name": "demo-skill"})
    assert result["name"] == "demo-skill"
    assert "SKILL.v1.0.0.md" in result["versions"]


@pytest.mark.anyio
async def test_handle_skills_evolution_rollback_latest(tmp_path, monkeypatch):
    skills_dir = _write_skill(tmp_path, "demo-skill")
    skill_md = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    skill_md.write_text("# current\n", encoding="utf-8")
    _write_pair(tmp_path, "demo-skill", "v1.0.0", "# archived-body\n")

    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_skill_dirs", lambda: [str(skills_dir)])

    result = await adapter.handle_skills_evolution_rollback(
        {"name": "demo-skill", "version": "latest"}
    )
    assert result["success"] is True
    assert result["rolled_back"] is True
    assert skill_md.read_text(encoding="utf-8") == "# archived-body\n"
    live = json.loads(
        (tmp_path / "skills" / "demo-skill" / "evolutions.json").read_text(encoding="utf-8")
    )
    assert live["entries"] == []


@pytest.mark.anyio
async def test_handle_skills_evolution_rollback_lists_when_version_omitted(tmp_path, monkeypatch):
    skills_dir = _write_skill(tmp_path, "demo-skill")
    _write_pair(tmp_path, "demo-skill", "v1.2.0", "# v120\n")
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_skill_dirs", lambda: [str(skills_dir)])

    result = await adapter.handle_skills_evolution_rollback({"name": "demo-skill"})
    assert result["success"] is True
    assert result["rolled_back"] is False
    assert result["versions"]


@pytest.mark.anyio
async def test_generate_evolution_merge_version_fingerprint_gate(tmp_path, monkeypatch):
    skills_dir = _write_skill(tmp_path, "demo-skill")
    skill_md = tmp_path / "skills" / "demo-skill" / "SKILL.md"
    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = object()  # pylint: disable=protected-access
    adapter._model = object()  # pylint: disable=protected-access
    adapter._config_cache = {"evolution": {"auto_save": True}}  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_resolve_skill_dirs", lambda: [str(skills_dir)])
    monkeypatch.setattr(adapter, "_bind_request_env_overlay", lambda: (None, None))
    monkeypatch.setattr(adapter, "_reset_request_env_bindings", lambda *_a, **_k: None)
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "cn")
    monkeypatch.setattr(adapter, "_resolve_model_name", lambda: "test-model")
    monkeypatch.setattr(adapter, "ensure_instance", AsyncMock())
    monkeypatch.setattr(
        adapter,
        "_execute_merge_version_rewrite",
        AsyncMock(return_value=True),
    )

    # Rewrite succeeds but file unchanged → must fail before complete_rebuild
    with pytest.raises(ValueError, match="未更新"):
        await adapter.generate_evolution_merge_version({"name": "demo-skill"})

    assert skill_md.read_text(encoding="utf-8").startswith("---")


def test_queue_auto_rebuild_respects_skill_evolution_action(monkeypatch):
    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_skill_dirs", lambda: [])
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.resolve_skill_evolution_action",
        lambda skill_name, **_kwargs: "suggest",
    )
    adapter._queue_auto_rebuild_skill("demo-skill")  # pylint: disable=protected-access
    assert adapter._pending_auto_rebuild_skills == []  # pylint: disable=protected-access

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.resolve_skill_evolution_action",
        lambda skill_name, **_kwargs: "auto",
    )
    adapter._queue_auto_rebuild_skill("demo-skill")  # pylint: disable=protected-access
    assert adapter._pending_auto_rebuild_skills == ["demo-skill"]  # pylint: disable=protected-access

    adapter._pending_auto_rebuild_skills.clear()  # pylint: disable=protected-access
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.resolve_skill_evolution_action",
        lambda skill_name, **_kwargs: "off",
    )
    adapter._queue_auto_rebuild_skill("demo-skill")  # pylint: disable=protected-access
    assert adapter._pending_auto_rebuild_skills == []  # pylint: disable=protected-access


def _adapter_for_auto_rebuild(monkeypatch, *, entries: list[Any]) -> JiuWenSwarmDeepAdapter:
    adapter = JiuWenSwarmDeepAdapter()
    adapter._pending_auto_rebuild_skills = ["demo-skill"]  # pylint: disable=protected-access
    store = SimpleNamespace(
        load_full_evolution_log=AsyncMock(return_value=SimpleNamespace(entries=entries)),
    )
    monkeypatch.setattr(adapter, "_get_disk_evolution_store", lambda: store)
    monkeypatch.setattr(adapter, "_should_auto_merge_evolved_skill", lambda _name: True)
    return adapter


@pytest.mark.anyio
async def test_run_auto_rebuild_skips_when_no_live_records(monkeypatch):
    adapter = _adapter_for_auto_rebuild(monkeypatch, entries=[])
    merge_calls: list[str] = []

    async def _fake_merge(*, skill_name: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        merge_calls.append(str(skill_name))
        return {"success": True}

    monkeypatch.setattr(adapter, "generate_evolution_merge_version", _fake_merge)

    await adapter._run_auto_rebuild_skills_detached(request_id="rid")  # pylint: disable=protected-access

    assert merge_calls == []
    assert adapter._pending_auto_rebuild_skills == []  # pylint: disable=protected-access


@pytest.mark.anyio
async def test_run_auto_rebuild_proceeds_when_live_records_exist(monkeypatch):
    adapter = _adapter_for_auto_rebuild(monkeypatch, entries=[SimpleNamespace(id="e1")])
    merge_calls: list[str] = []

    async def _fake_merge(*, skill_name: str | None = None, **_kwargs: Any) -> dict[str, Any]:
        merge_calls.append(str(skill_name))
        return {"success": True}

    monkeypatch.setattr(adapter, "generate_evolution_merge_version", _fake_merge)

    await adapter._run_auto_rebuild_skills_detached(request_id="rid")  # pylint: disable=protected-access

    assert merge_calls == ["demo-skill"]
    assert adapter._pending_auto_rebuild_skills == []  # pylint: disable=protected-access


@pytest.mark.anyio
async def test_do_evolve_rollback_clears_live_evolutions(tmp_path):
    skills_dir = _write_skill(tmp_path, "demo-skill")
    skill_dir = tmp_path / "skills" / "demo-skill"
    skill_dir.joinpath("evolutions.json").write_text(
        json.dumps(
            {
                "skill_id": "demo-skill",
                "version": "v1.0.0",
                "updated_at": "dirty",
                "entries": [{"id": "e1"}],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    _write_pair(tmp_path, "demo-skill", "v1.0.0", "# restored\n")
    store = evolution_version_ctl.get_disk_evolution_store([str(skills_dir)])
    result = await evolution_version_ctl.do_evolve_rollback(store, "demo-skill", "latest")
    assert result["ok"] is True
    assert result["rolled_back"] is True
    live = json.loads(skill_dir.joinpath("evolutions.json").read_text(encoding="utf-8"))
    assert live["entries"] == []


def test_allowed_skill_roots_for_path_includes_control_plane_root(tmp_path: Path, monkeypatch):
    workspace_skills = tmp_path / "workspace" / "skills"
    workspace_skills.mkdir(parents=True)
    project_skills = tmp_path / "relay-claw" / ".office-claw" / "skills"
    skill_md = project_skills / "tianqi" / "SKILL.md"
    skill_md.parent.mkdir(parents=True)
    skill_md.write_text("# tianqi\n", encoding="utf-8")

    monkeypatch.setattr(
        evolution_version_ctl,
        "resolve_agent_registered_skill_dirs",
        lambda: [workspace_skills],
    )
    roots = evolution_version_ctl.allowed_skill_roots_for_path(
        [str(workspace_skills)],
        str(skill_md),
    )
    assert str(workspace_skills.resolve()) in roots
    assert str(project_skills.resolve()) in roots


@pytest.mark.anyio
async def test_handle_skills_evolution_rollback_accepts_office_claw_skill_path(
    tmp_path, monkeypatch,
):
    """Control-plane .office-claw/skills path must pass even if adapter is workspace-only."""
    workspace_skills = tmp_path / "workspace" / "skills"
    workspace_skills.mkdir(parents=True)

    project_root = tmp_path / "relay-claw"
    skills_dir = project_root / ".office-claw" / "skills"
    skill_dir = skills_dir / "tianqi"
    archive = skill_dir / "archive"
    archive.mkdir(parents=True)
    skill_md = skill_dir / "SKILL.md"
    skill_md.write_text("# current\n", encoding="utf-8")
    skill_dir.joinpath("evolutions.json").write_text(
        json.dumps(
            {
                "skill_id": "tianqi",
                "version": "v2.0.0",
                "updated_at": "live",
                "entries": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    archive.joinpath("SKILL.v1.0.0.md").write_text("# archived-body\n", encoding="utf-8")
    archive.joinpath("evolutions.v1.0.0.json").write_text(
        json.dumps(
            {
                "skill_id": "tianqi",
                "version": "v1.0.0",
                "updated_at": "archived",
                "entries": [],
            },
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )

    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_skill_dirs", lambda: [str(workspace_skills)])
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.resolve_agent_registered_skill_dirs",
        lambda: [workspace_skills],
    )
    monkeypatch.setattr(
        evolution_version_ctl,
        "resolve_agent_registered_skill_dirs",
        lambda: [workspace_skills],
    )

    result = await adapter.handle_skills_evolution_rollback(
        {
            "name": "tianqi",
            "version": "latest",
            "skill_path": str(skill_md),
        }
    )
    assert result["success"] is True
    assert result["rolled_back"] is True
    assert skill_md.read_text(encoding="utf-8") == "# archived-body\n"


@pytest.mark.anyio
async def test_handle_skills_evolution_rollback_rejects_path_outside_allowed(
    tmp_path, monkeypatch,
):
    workspace_skills = tmp_path / "workspace" / "skills"
    workspace_skills.mkdir(parents=True)
    other = tmp_path / "other" / "skills" / "demo-skill"
    other.mkdir(parents=True)
    skill_md = other / "SKILL.md"
    skill_md.write_text("# demo\n", encoding="utf-8")

    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_skill_dirs", lambda: [str(workspace_skills)])
    monkeypatch.setattr(
        evolution_version_ctl,
        "resolve_agent_registered_skill_dirs",
        lambda: [workspace_skills],
    )
    # Do not include skill_path root via helper path: validate with stale roots only
    # by forcing allowed_skill_roots_for_path to omit the control-plane root.
    monkeypatch.setattr(
        evolution_version_ctl,
        "allowed_skill_roots_for_path",
        lambda _adapter_dirs, _skill_path=None: [str(workspace_skills.resolve())],
    )

    with pytest.raises(ValueError, match="outside registered skill roots"):
        await adapter.handle_skills_evolution_rollback(
            {
                "name": "demo-skill",
                "version": "latest",
                "skill_path": str(skill_md),
            }
        )


@pytest.mark.anyio
async def test_handle_skills_evolution_rollback_rejects_mismatched_skill_dir_name(
    tmp_path, monkeypatch,
):
    skills_dir = tmp_path / "skills"
    wrong = skills_dir / "wrong-name"
    wrong.mkdir(parents=True)
    skill_md = wrong / "SKILL.md"
    skill_md.write_text("# dummy\n", encoding="utf-8")

    adapter = JiuWenSwarmDeepAdapter()
    monkeypatch.setattr(adapter, "_resolve_skill_dirs", lambda: [str(skills_dir)])

    with pytest.raises(ValueError, match="directory name must match skill name"):
        await adapter.handle_skills_evolution_rollback(
            {
                "name": "demo-skill",
                "version": "latest",
                "skill_path": str(skill_md),
            }
        )


@pytest.mark.anyio
async def test_ws_disk_only_evolution_uses_agent_manager_not_stateless_agent(monkeypatch):
    """archives/rollback must not skip AgentManager disk-only skill-root binding."""
    from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse
    from jiuwenswarm.common.schema.message import ReqMethod
    from jiuwenswarm.server.agent_ws_server import AgentWebSocketServer

    server = object.__new__(AgentWebSocketServer)
    manager_calls: list[AgentRequest] = []
    stateless_calls: list[AgentRequest] = []

    class _Manager:
        async def process_message(self, request):
            manager_calls.append(request)
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"name": "tianqi", "versions": ["SKILL.v1.0.0.md"]},
            )

    class _Stateless:
        async def process_message(self, request):
            stateless_calls.append(request)
            raise AssertionError("stateless agent must not handle disk-only evolution")

    server._agent_manager = _Manager()  # pylint: disable=protected-access
    monkeypatch.setattr(
        AgentWebSocketServer,
        "_uses_tenant_pool",
        staticmethod(lambda _request: False),
    )
    monkeypatch.setattr(
        AgentWebSocketServer,
        "_get_stateless_agent",
        AsyncMock(return_value=_Stateless()),
    )

    sent: list[Any] = []

    async def _send_wire(_ws, wire):
        sent.append(wire)
        return True

    monkeypatch.setattr(
        "jiuwenswarm.server.agent_ws_server.send_wire_payload",
        _send_wire,
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.agent_ws_server.encode_agent_response_for_wire",
        lambda resp, response_id=None: {"ok": resp.ok, "payload": resp.payload},
    )
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.ensure_persistent_checkpointer",
        AsyncMock(),
    )

    request = AgentRequest(
        request_id="req-disk",
        channel_id="web",
        req_method=ReqMethod.SKILLS_EVOLUTION_ROLLBACK,
        params={"name": "tianqi", "version": "latest", "skill_path": "X:/proj/.office-claw/skills/tianqi/SKILL.md"},
        is_stream=False,
    )
    await server._handle_unary_impl(None, request, asyncio.Lock())  # pylint: disable=protected-access

    assert len(manager_calls) == 1
    assert not stateless_calls
    assert sent
