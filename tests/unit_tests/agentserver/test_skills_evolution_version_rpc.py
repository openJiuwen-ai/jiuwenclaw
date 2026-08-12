# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for skills.evolution.archives / rollback / rebuild host adapters."""

from __future__ import annotations

import json
from pathlib import Path
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


def test_queue_auto_rebuild_respects_auto_save(monkeypatch):
    adapter = JiuWenSwarmDeepAdapter()
    adapter._config_cache = {"react": {"evolution": {"auto_save": False}}}  # pylint: disable=protected-access
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.get_evolution_auto_save_enabled",
        lambda _cfg=None: False,
    )
    adapter._queue_auto_rebuild_skill("demo-skill")  # pylint: disable=protected-access
    assert adapter._pending_auto_rebuild_skills == []  # pylint: disable=protected-access

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.get_evolution_auto_save_enabled",
        lambda _cfg=None: True,
    )
    adapter._queue_auto_rebuild_skill("demo-skill")  # pylint: disable=protected-access
    assert adapter._pending_auto_rebuild_skills == ["demo-skill"]  # pylint: disable=protected-access


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
