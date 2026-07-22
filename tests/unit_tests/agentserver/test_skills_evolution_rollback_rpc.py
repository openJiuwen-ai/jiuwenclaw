# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for skills.evolution.rollback / archives RPC helpers."""

# pylint: disable=protected-access

import asyncio
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter


def _make_adapter() -> JiuWenClawDeepAdapter:
    adapter = object.__new__(JiuWenClawDeepAdapter)
    adapter._config_cache = {"evolution": {"enabled": False, "auto_scan": False}}
    adapter._skill_evolution_rail = None
    adapter._model = None
    adapter._registered_skill_dirs = []
    return adapter


def _mock_disk_store(adapter: JiuWenClawDeepAdapter, monkeypatch, store: MagicMock) -> MagicMock:
    monkeypatch.setattr(adapter, "_get_disk_evolution_store", lambda: store)
    return store


@pytest.mark.unit
def test_handle_skills_evolution_archives_lists_body_versions(monkeypatch):
    adapter = _make_adapter()
    store = MagicMock()
    store.skill_exists.return_value = True
    store.list_archives.return_value = [
        "SKILL.v20260623T103013.md",
        "evolutions.v20260623T103013_01.json",
        "notes.txt",
    ]
    _mock_disk_store(adapter, monkeypatch, store)
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )

    result = asyncio.run(adapter.handle_skills_evolution_archives({"name": "docx-craft"}))
    assert result == {
        "name": "docx-craft",
        "versions": ["SKILL.v20260623T103013.md"],
    }
    assert adapter._skill_evolution_rail is None


@pytest.mark.unit
def test_handle_skills_evolution_archives_works_without_rail_or_model(monkeypatch, tmp_path: Path):
    skill_dir = tmp_path / "daily-weather"
    archive_dir = skill_dir / "archive"
    archive_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# daily-weather\n", encoding="utf-8")
    (archive_dir / "SKILL.v1.0.0.md").write_text("# archived\n", encoding="utf-8")
    (archive_dir / "evolutions.v1.0.0.json").write_text("{}", encoding="utf-8")

    adapter = _make_adapter()
    adapter._registered_skill_dirs = [str(tmp_path)]
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_build_skill_evolution_rail",
        lambda self, _config: (_ for _ in ()).throw(
            AssertionError("archives must not build EvolutionRail")
        ),
    )

    result = asyncio.run(adapter.handle_skills_evolution_archives({"name": "daily-weather"}))
    assert result["name"] == "daily-weather"
    assert "SKILL.v1.0.0.md" in result["versions"]
    assert adapter._skill_evolution_rail is None
    assert adapter._model is None


@pytest.mark.unit
def test_handle_skills_evolution_rollback_latest(monkeypatch):
    adapter = _make_adapter()
    store = MagicMock()
    store.skill_exists.return_value = True
    store.list_archives.return_value = ["SKILL.v20260623T103013.md", "SKILL.v20260622T121237.md"]
    store.normalize_body_archive_name = (
        lambda value: value if value.endswith(".md") else f"SKILL.{value}.md"
    )
    _mock_disk_store(adapter, monkeypatch, store)
    monkeypatch.setattr(
        adapter,
        "_rollback_skill_via_store",
        AsyncMock(return_value=(True, True)),
    )
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_filter_evolution_eligible_skill_names",
        staticmethod(lambda names: names),
    )

    result = asyncio.run(
        adapter.handle_skills_evolution_rollback({"name": "docx-craft", "version": "latest"})
    )
    assert result == {
        "success": True,
        "name": "docx-craft",
        "version": "SKILL.v20260623T103013.md",
        "rolled_back": True,
    }
    adapter._rollback_skill_via_store.assert_awaited_once_with(
        store,
        "docx-craft",
        "SKILL.v20260623T103013.md",
    )


@pytest.mark.unit
def test_do_evolve_rollback_restores_skill_without_rail(monkeypatch, tmp_path: Path):
    skill_dir = tmp_path / "daily-weather"
    archive_dir = skill_dir / "archive"
    archive_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("# current body\n", encoding="utf-8")
    (skill_dir / "evolutions.json").write_text('{"version":"2.0.0"}', encoding="utf-8")
    (archive_dir / "SKILL.v1.0.0.md").write_text("# archived body\n", encoding="utf-8")
    (archive_dir / "evolutions.v1.0.0.json").write_text(
        '{"version":"1.0.0","records":[]}',
        encoding="utf-8",
    )

    adapter = _make_adapter()
    adapter._registered_skill_dirs = [str(tmp_path)]
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_build_skill_evolution_rail",
        lambda self, _config: (_ for _ in ()).throw(
            AssertionError("rollback must not build EvolutionRail")
        ),
    )

    result = asyncio.run(adapter._do_evolve_rollback("daily-weather", "SKILL.v1.0.0.md"))
    assert result == {
        "ok": True,
        "rolled_back": True,
        "name": "daily-weather",
        "version": "SKILL.v1.0.0.md",
    }
    assert (skill_dir / "SKILL.md").read_text(encoding="utf-8") == "# archived body\n"
    assert '"version": "1.0.0"' in (skill_dir / "evolutions.json").read_text(encoding="utf-8") or (
        '"version":"1.0.0"' in (skill_dir / "evolutions.json").read_text(encoding="utf-8")
    )
    assert not (archive_dir / "SKILL.v1.0.0.md").exists()
    assert adapter._skill_evolution_rail is None
    assert adapter._model is None


@pytest.mark.unit
def test_rollback_skill_via_store_continues_when_evo_restore_fails(monkeypatch):
    """Body rollback must succeed even if paired evolution-log restore fails."""
    adapter = _make_adapter()
    body_archive = SimpleNamespace(name="SKILL.v1.0.0.md")
    evo_archive = SimpleNamespace(name="evolutions.v1.0.0.json")
    store = MagicMock()
    store.get_skill_archive_dir.return_value = Path("/tmp/skill/archive")
    store.normalize_body_archive_name.return_value = "SKILL.v1.0.0.md"
    store.get_skill_archive_file.return_value = body_archive
    store.resolve_paired_evolution_archive.return_value = evo_archive
    store.read_archive_text = AsyncMock(return_value="# archived body\n")
    store.archive_current_state = AsyncMock()
    store.write_skill_content = AsyncMock()
    store.restore_evolution_log_from_archive = AsyncMock(return_value=False)
    store.render_evolution_markdown = AsyncMock()
    store.delete_archive_version = AsyncMock(return_value=True)

    ok, evo_ok = asyncio.run(
        adapter._rollback_skill_via_store(store, "daily-weather", "SKILL.v1.0.0.md")
    )
    assert ok is True
    assert evo_ok is False
    store.write_skill_content.assert_awaited_once_with("daily-weather", "# archived body\n")
    store.restore_evolution_log_from_archive.assert_awaited_once()
    store.render_evolution_markdown.assert_awaited_once_with("daily-weather")
    store.delete_archive_version.assert_awaited_once_with(
        "daily-weather", "SKILL.v1.0.0.md"
    )


@pytest.mark.unit
def test_do_evolve_rollback_surfaces_evo_restore_warning(monkeypatch):
    adapter = _make_adapter()
    store = MagicMock()
    store.skill_exists.return_value = True
    store.list_archives.return_value = ["SKILL.v1.0.0.md"]
    store.normalize_body_archive_name = lambda value: value
    _mock_disk_store(adapter, monkeypatch, store)
    monkeypatch.setattr(
        adapter,
        "_rollback_skill_via_store",
        AsyncMock(return_value=(True, False)),
    )
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )

    result = asyncio.run(adapter._do_evolve_rollback("daily-weather", "SKILL.v1.0.0.md"))
    assert result["ok"] is True
    assert result["rolled_back"] is True
    assert "evolution log" in result["warning"]

    rpc = asyncio.run(
        adapter.handle_skills_evolution_rollback(
            {"name": "daily-weather", "version": "SKILL.v1.0.0.md"}
        )
    )
    assert rpc["rolled_back"] is True
    assert "evolution log" in rpc["warning"]


@pytest.mark.unit
def test_do_evolve_rollback_timeout_mentions_archive_dir(monkeypatch):
    adapter = _make_adapter()
    store = MagicMock()
    store.skill_exists.return_value = True
    store.list_archives.return_value = ["SKILL.v1.0.0.md"]
    store.normalize_body_archive_name = lambda value: value
    _mock_disk_store(adapter, monkeypatch, store)

    async def _slow(*_args, **_kwargs):
        raise asyncio.TimeoutError()

    monkeypatch.setattr(adapter, "_rollback_skill_via_store", _slow)
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )

    result = asyncio.run(adapter._do_evolve_rollback("daily-weather", "SKILL.v1.0.0.md"))
    assert result["ok"] is False
    assert "archive" in result["error"]
    assert "部分完成" in result["error"]


@pytest.mark.unit
def test_handle_skills_evolution_rollback_lists_when_version_omitted(monkeypatch):
    adapter = _make_adapter()
    store = MagicMock()
    store.skill_exists.return_value = True
    store.list_archives.return_value = ["SKILL.v20260623T103013.md"]
    _mock_disk_store(adapter, monkeypatch, store)
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )

    result = asyncio.run(adapter.handle_skills_evolution_rollback({"name": "docx-craft"}))
    assert result == {
        "success": True,
        "name": "docx-craft",
        "rolled_back": False,
        "versions": ["SKILL.v20260623T103013.md"],
    }


@pytest.mark.unit
def test_handle_skills_evolution_rollback_rejects_unsafe_name():
    adapter = _make_adapter()
    with pytest.raises(ValueError, match="invalid skill name"):
        asyncio.run(adapter.handle_skills_evolution_rollback({"name": "../evil", "version": "latest"}))


@pytest.mark.unit
def test_handle_skills_evolution_rollback_no_archives(monkeypatch):
    adapter = _make_adapter()
    store = MagicMock()
    store.skill_exists.return_value = True
    store.list_archives.return_value = ["notes.txt"]
    _mock_disk_store(adapter, monkeypatch, store)
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_guard_bootstrap_skill",
        staticmethod(lambda _name: None),
    )
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_filter_evolution_eligible_skill_names",
        staticmethod(lambda names: names),
    )

    with pytest.raises(ValueError, match="没有归档版本"):
        asyncio.run(adapter.handle_skills_evolution_rollback({"name": "docx-craft", "version": "latest"}))


@pytest.mark.unit
def test_get_disk_evolution_store_does_not_build_rail(monkeypatch, tmp_path: Path):
    adapter = _make_adapter()
    adapter._registered_skill_dirs = [str(tmp_path)]
    monkeypatch.setattr(
        JiuWenClawDeepAdapter,
        "_build_skill_evolution_rail",
        lambda self, _config: (_ for _ in ()).throw(
            AssertionError("disk store must not build EvolutionRail")
        ),
    )
    store = adapter._get_disk_evolution_store()
    assert store is not None
    assert adapter._skill_evolution_rail is None


@pytest.mark.unit
def test_facade_forwards_evolution_rail_rpc(monkeypatch):
    from jiuwenclaw.agentserver.interface import JiuWenClaw
    from jiuwenclaw.schema.agent import AgentRequest
    from jiuwenclaw.schema.message import ReqMethod

    claw = object.__new__(JiuWenClaw)
    adapter = SimpleNamespace(
        handle_skills_evolution_rollback=AsyncMock(
            return_value={
                "success": True,
                "name": "docx-craft",
                "version": "SKILL.v20260623T103013.md",
                "rolled_back": True,
            }
        )
    )
    claw._ensure_adapter = AsyncMock(return_value=adapter)

    request = AgentRequest(
        request_id="req-1",
        channel_id="web",
        req_method=ReqMethod.SKILLS_EVOLUTION_ROLLBACK,
        params={"name": "docx-craft", "version": "latest"},
    )
    response = asyncio.run(claw._handle_skills_evolution_rail_request(request))
    assert response.ok is True
    assert response.payload["rolled_back"] is True
    adapter.handle_skills_evolution_rollback.assert_awaited_once()


@pytest.mark.unit
def test_facade_forwards_evolution_rebuild_rpc(monkeypatch):
    from jiuwenclaw.agentserver.interface import JiuWenClaw
    from jiuwenclaw.schema.agent import AgentRequest
    from jiuwenclaw.schema.message import ReqMethod

    claw = object.__new__(JiuWenClaw)
    adapter = SimpleNamespace(
        handle_skills_evolution_rebuild=AsyncMock(
            return_value={
                "success": True,
                "name": "docx-craft",
                "new_version": "1.2.0",
                "cleared": True,
            }
        )
    )
    claw._ensure_adapter = AsyncMock(return_value=adapter)

    request = AgentRequest(
        request_id="req-rebuild",
        channel_id="web",
        req_method=ReqMethod.SKILLS_EVOLUTION_REBUILD,
        params={"name": "docx-craft", "record_ids": ["ev_1"]},
    )
    response = asyncio.run(claw._handle_skills_evolution_rail_request(request))
    assert response.ok is True
    assert response.payload["new_version"] == "1.2.0"
    adapter.handle_skills_evolution_rebuild.assert_awaited_once()


@pytest.mark.unit
def test_agent_manager_disk_only_evolution_skips_create_instance(monkeypatch):
    from jiuwenclaw.agentserver.agent_manager import AgentManager
    from jiuwenclaw.schema.agent import AgentRequest
    from jiuwenclaw.schema.message import ReqMethod

    manager = object.__new__(AgentManager)
    manager.user_workspace_dir = None
    manager.agent_id = "test-agent"
    manager.service_id = "test-service"
    manager._latest_env_overrides = {
        "JIUWENCLAW_SHARED_SKILLS_DIRS": "D:\\skills",
    }
    manager.get_agent = AsyncMock(side_effect=AssertionError("get_agent must not run"))
    manager.get_agent_nowait = MagicMock(return_value=None)

    ephemeral = SimpleNamespace(
        process_message=AsyncMock(
            return_value=SimpleNamespace(
                ok=True,
                payload={"name": "daily-weather", "versions": ["SKILL.v1.0.0.md"]},
            )
        )
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.interface.JiuWenClaw",
        lambda **_kwargs: ephemeral,
    )

    request = AgentRequest(
        request_id="req-disk",
        channel_id="web",
        req_method=ReqMethod.SKILLS_EVOLUTION_ARCHIVES,
        params={"name": "daily-weather"},
    )
    response = asyncio.run(manager.process_message(request))
    assert response.ok is True
    assert response.payload["versions"] == ["SKILL.v1.0.0.md"]
    ephemeral.process_message.assert_awaited_once_with(request)
    manager.get_agent.assert_not_called()


@pytest.mark.unit
def test_facade_evolution_rail_rpc_handler_missing():
    from jiuwenclaw.agentserver.interface import JiuWenClaw
    from jiuwenclaw.schema.agent import AgentRequest
    from jiuwenclaw.schema.message import ReqMethod

    claw = object.__new__(JiuWenClaw)
    claw._ensure_adapter = AsyncMock(return_value=SimpleNamespace())

    request = AgentRequest(
        request_id="req-missing",
        channel_id="web",
        req_method=ReqMethod.SKILLS_EVOLUTION_ROLLBACK,
        params={"name": "docx-craft", "version": "latest"},
    )
    response = asyncio.run(claw._handle_skills_evolution_rail_request(request))
    assert response.ok is False
    assert "不支持" in response.payload["error"]


@pytest.mark.unit
def test_facade_evolution_rail_rpc_handler_raises():
    from jiuwenclaw.agentserver.interface import JiuWenClaw
    from jiuwenclaw.schema.agent import AgentRequest
    from jiuwenclaw.schema.message import ReqMethod

    claw = object.__new__(JiuWenClaw)
    adapter = SimpleNamespace(
        handle_skills_evolution_rollback=AsyncMock(side_effect=RuntimeError("boom")),
    )
    claw._ensure_adapter = AsyncMock(return_value=adapter)

    request = AgentRequest(
        request_id="req-err",
        channel_id="web",
        req_method=ReqMethod.SKILLS_EVOLUTION_ROLLBACK,
        params={"name": "docx-craft", "version": "latest"},
    )
    response = asyncio.run(claw._handle_skills_evolution_rail_request(request))
    assert response.ok is False
    assert response.payload["error"] == "boom"


@pytest.mark.unit
def test_agent_manager_disk_only_evolution_reuses_existing_agent(monkeypatch):
    from jiuwenclaw.agentserver.agent_manager import AgentManager
    from jiuwenclaw.schema.agent import AgentRequest
    from jiuwenclaw.schema.message import ReqMethod

    manager = object.__new__(AgentManager)
    manager.user_workspace_dir = None
    manager.agent_id = "test-agent"
    manager.service_id = "test-service"
    manager._latest_env_overrides = {}
    manager.get_agent = AsyncMock(side_effect=AssertionError("get_agent must not run"))

    existing = SimpleNamespace(
        process_message=AsyncMock(
            return_value=SimpleNamespace(
                ok=True,
                payload={"name": "daily-weather", "versions": ["SKILL.v1.0.0.md"]},
            )
        )
    )
    manager.get_agent_nowait = MagicMock(return_value=existing)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.interface.JiuWenClaw",
        lambda **_kwargs: (_ for _ in ()).throw(
            AssertionError("ephemeral JiuWenClaw must not be created")
        ),
    )

    request = AgentRequest(
        request_id="req-reuse",
        channel_id="web",
        req_method=ReqMethod.SKILLS_EVOLUTION_ARCHIVES,
        params={"name": "daily-weather"},
    )
    response = asyncio.run(manager.process_message(request))
    assert response.ok is True
    assert response.payload["versions"] == ["SKILL.v1.0.0.md"]
    existing.process_message.assert_awaited_once_with(request)
    manager.get_agent.assert_not_called()


@pytest.mark.unit
def test_rebuild_not_in_disk_only_evolution_methods():
    """skills.evolution.rebuild needs agent merge rewrite; must not be disk-only ephemeral."""
    from jiuwenclaw.agentserver.agent_manager import _DISK_ONLY_EVOLUTION_METHODS

    assert "skills.evolution.archives" in _DISK_ONLY_EVOLUTION_METHODS
    assert "skills.evolution.rollback" in _DISK_ONLY_EVOLUTION_METHODS
    assert "skills.evolution.rebuild" not in _DISK_ONLY_EVOLUTION_METHODS
