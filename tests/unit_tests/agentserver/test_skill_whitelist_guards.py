"""Skill 白名单：租户 ID / 路径逃生通道回归；预制 sync 直写 DB."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jiuwenswarm.server.runtime.skill.skill_whitelist import is_skill_whitelist_tenant
from jiuwenswarm.common.utils import (
    get_agent_skills_dir,
    get_multi_tenant_skill_dirs,
    get_tenant_agent_jiuwenclaw_workspace_dir,
    get_tenant_agent_skills_dirs,
)


@pytest.fixture
def agent_runtime_env(monkeypatch: pytest.MonkeyPatch):
    monkeypatch.setenv("JIUWENSWARM_EDITION", "enterprise")
    yield
    monkeypatch.delenv("JIUWENSWARM_EDITION", raising=False)


def test_is_skill_whitelist_tenant_rejects_empty_ids(agent_runtime_env) -> None:
    assert is_skill_whitelist_tenant("", "") is False
    assert is_skill_whitelist_tenant("  ", "bot") is False
    assert is_skill_whitelist_tenant("bot", None) is False


def test_is_skill_whitelist_tenant_legacy_tenants(agent_runtime_env) -> None:
    assert is_skill_whitelist_tenant("default", "default") is False
    assert is_skill_whitelist_tenant("acp", "global_acp") is False
    assert is_skill_whitelist_tenant("real-agent", "real-svc") is True


def test_is_skill_whitelist_tenant_requires_agent_runtime(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("JIUWENSWARM_EDITION", raising=False)
    assert is_skill_whitelist_tenant("real-agent", "real-svc") is False


def test_tenant_workspace_requires_ids() -> None:
    with pytest.raises(TypeError, match="tenant scope is required"):
        get_tenant_agent_jiuwenclaw_workspace_dir()
    with pytest.raises(TypeError, match="tenant scope requires both"):
        get_tenant_agent_skills_dirs(service_id="default", agent_id=None)


def test_multi_tenant_skill_dirs_single_tenant_fallback() -> None:
    dirs = get_multi_tenant_skill_dirs()
    assert len(dirs) == 1
    assert dirs[0] == get_agent_skills_dir()


def test_parse_agent_skill_whitelist_id_version_source() -> None:
    from jiuwenswarm.server.runtime.skill.skill_whitelist import parse_agent_skill_whitelist

    config = parse_agent_skill_whitelist(
        "bot-1",
        "my-svc",
        [
            {
                "skill_id": "692e40917156f746d25f84fb",
                "skill_version": "3.0.1",
                "skill_source": "https://openjiuwen-market.obs.example/skills/pkg.zip",
            }
        ],
    )
    assert len(config.skills) == 1
    assert config.skills[0].id == "692e40917156f746d25f84fb"
    assert config.skills[0].version == "3.0.1"
    assert config.skills[0].source.endswith("pkg.zip")
    assert len(config.items_with_source) == 1


def test_parse_agent_skill_whitelist_skips_invalid_items() -> None:
    from jiuwenswarm.server.runtime.skill.skill_whitelist import parse_agent_skill_whitelist

    assert parse_agent_skill_whitelist("bot-1", "my-svc", []).skills == []
    assert (
        parse_agent_skill_whitelist(
            "bot-1",
            "my-svc",
            [{"skill_id": "only-id", "skill_version": "1.0.0"}],
        ).items_with_source
        == []
    )


class _FakeSkillDb:
    """内存假账本，供 sync 单测替代 Gateway DB."""

    def __init__(self) -> None:
        self.rows: dict[str, dict[str, Any]] = {}

    async def list_installed_skills(self, **_kwargs: Any) -> list[dict[str, Any]]:
        return list(self.rows.values())

    async def list_enabled_skill_names(self, **_kwargs: Any) -> list[str]:
        return list(self.rows.keys())

    async def get_installed_skill(self, *, skill_name: str, **_kwargs: Any) -> dict[str, Any] | None:
        return self.rows.get(skill_name)

    async def upsert_installed_skill(self, **kwargs: Any) -> dict[str, Any]:
        name = str(kwargs["skill_name"])
        row = {
            "skill_name": name,
            "source_type": kwargs.get("source_type"),
            "skill_source": kwargs.get("skill_source"),
            "skill_version": kwargs.get("skill_version"),
            "skill_id": kwargs.get("skill_id"),
        }
        self.rows[name] = row
        return row

    async def delete_installed_skill(self, *, skill_name: str, **_kwargs: Any) -> bool:
        return self.rows.pop(skill_name, None) is not None


def _patch_skill_db(monkeypatch: pytest.MonkeyPatch, db: _FakeSkillDb) -> None:
    mod = "jiuwenswarm.server.runtime.skill.skill_whitelist"
    monkeypatch.setattr(f"{mod}.list_installed_skills", db.list_installed_skills)
    monkeypatch.setattr(f"{mod}.upsert_installed_skill", db.upsert_installed_skill)
    monkeypatch.setattr(f"{mod}.delete_installed_skill", db.delete_installed_skill)


def test_multi_id_same_source_version_skips_second_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jiuwenswarm.server.runtime.skill.skill_whitelist import (
        AgentSkillWhitelistConfig,
        SkillWhitelistItem,
        SkillWhitelistSynchronizer,
    )

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)

    source = "https://example.com/pkg.zip"
    version = "1.0.0"
    db = _FakeSkillDb()
    _patch_skill_db(monkeypatch, db)

    install_called = {"count": 0}

    def _fake_install(_url: str, _force: bool, _mirror: None) -> dict:
        install_called["count"] += 1
        skill_dir = skills_dir / "shared-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: shared-skill\n---\n", encoding="utf-8")
        return {"ok": True, "skill_name": "shared-skill"}

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {"install_skill_sync": staticmethod(_fake_install)},
        )(),
    )

    sync = SkillWhitelistSynchronizer(workspace, "svc", "bot")
    config = AgentSkillWhitelistConfig(
        agent_id="bot",
        service_id="svc",
        skills=[
            SkillWhitelistItem(id="id-a", version=version, source=source),
            SkillWhitelistItem(id="id-b", version=version, source=source),
        ],
    )
    result = asyncio.run(sync.sync(config))

    assert install_called["count"] == 1
    assert result.enabled_skill_dirs == ["shared-skill"]
    assert result.ok is True
    assert "shared-skill" in db.rows
    assert db.rows["shared-skill"]["skill_id"] == "id-b"


def test_db_skips_redownload_on_second_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jiuwenswarm.server.runtime.skill.skill_whitelist import (
        AgentSkillWhitelistConfig,
        SkillWhitelistItem,
        SkillWhitelistSynchronizer,
    )

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    source = "https://example.com/pkg.zip"
    version = "1.0.0"
    config = AgentSkillWhitelistConfig(
        agent_id="bot",
        service_id="svc",
        skills=[SkillWhitelistItem(id="id-a", version=version, source=source)],
    )
    db = _FakeSkillDb()
    _patch_skill_db(monkeypatch, db)

    install_called = {"count": 0}

    def _fake_install(_url: str, _force: bool, _mirror: None) -> dict:
        install_called["count"] += 1
        skill_dir = skills_dir / "cached-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: cached-skill\n---\n", encoding="utf-8")
        return {"ok": True, "skill_name": "cached-skill"}

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {"install_skill_sync": staticmethod(_fake_install)},
        )(),
    )

    sync = SkillWhitelistSynchronizer(workspace, "svc", "bot")
    asyncio.run(sync.sync(config))
    assert install_called["count"] == 1

    asyncio.run(sync.sync(config))
    assert install_called["count"] == 1


def test_db_same_version_with_disk_missing_redownloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库有同版本但盘缺失时仍会重下补盘（不做库有盘无的成功兜底）。"""
    from jiuwenswarm.agents.harness.common.installed_skill import SOURCE_PREBUILT
    from jiuwenswarm.server.runtime.skill.skill_whitelist import (
        AgentSkillWhitelistConfig,
        SkillWhitelistItem,
        SkillWhitelistSynchronizer,
    )

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    source = "https://example.com/pkg.zip"
    version = "1.0.0"
    config = AgentSkillWhitelistConfig(
        agent_id="bot",
        service_id="svc",
        skills=[SkillWhitelistItem(id="id-a", version=version, source=source)],
    )
    db = _FakeSkillDb()
    db.rows["cached-skill"] = {
        "skill_name": "cached-skill",
        "source_type": SOURCE_PREBUILT,
        "skill_source": source,
        "skill_version": version,
        "skill_id": "id-a",
    }
    _patch_skill_db(monkeypatch, db)

    install_called = {"count": 0}

    def _fake_install(_url: str, _force: bool, _mirror: None) -> dict:
        install_called["count"] += 1
        skill_dir = skills_dir / "cached-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: cached-skill\n---\n", encoding="utf-8")
        return {"ok": True, "skill_name": "cached-skill"}

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {"install_skill_sync": staticmethod(_fake_install)},
        )(),
    )

    result = asyncio.run(SkillWhitelistSynchronizer(workspace, "svc", "bot").sync(config))
    assert install_called["count"] == 1
    assert result.ok is True
    assert "cached-skill" in db.rows


def test_disk_skill_missing_from_db_reconciled_on_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """盘有库无：sync 时补 SOURCE_USER 账本并进入启用集。"""
    from jiuwenswarm.agents.harness.common.installed_skill import SOURCE_USER
    from jiuwenswarm.server.runtime.skill.skill_whitelist import (
        AgentSkillWhitelistConfig,
        SkillWhitelistSynchronizer,
    )

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    orphan = skills_dir / "orphan-skill"
    orphan.mkdir(parents=True)
    (orphan / "SKILL.md").write_text("---\nname: orphan-skill\n---\n", encoding="utf-8")

    reserved = skills_dir / "_marketplace"
    reserved.mkdir(parents=True)
    (reserved / "SKILL.md").write_text("---\nname: marketplace\n---\n", encoding="utf-8")

    db = _FakeSkillDb()
    _patch_skill_db(monkeypatch, db)

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {
                "install_skill_sync": staticmethod(
                    lambda *_a, **_k: {"ok": False, "detail": "unused"}
                )
            },
        )(),
    )

    result = asyncio.run(
        SkillWhitelistSynchronizer(
            workspace,
            service_id="svc",
            agent_id="bot",
        ).sync(AgentSkillWhitelistConfig(agent_id="bot", service_id="svc"))
    )

    assert "orphan-skill" in db.rows
    assert db.rows["orphan-skill"]["source_type"] == SOURCE_USER
    assert "_marketplace" not in db.rows
    assert result.enabled_skill_dirs == ["orphan-skill"]
    assert "reconciled_disk:orphan-skill" in result.succeeded


def test_reconcile_disk_does_not_overwrite_prebuilt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """账本已是 prefbuilt 的技能，盘→库对账不得改成 user。"""
    from jiuwenswarm.agents.harness.common.installed_skill import SOURCE_PREBUILT
    from jiuwenswarm.server.runtime.skill.skill_whitelist import SkillWhitelistSynchronizer

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    skill_dir = skills_dir / "prebuilt-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: prebuilt-skill\n---\n", encoding="utf-8")

    db = _FakeSkillDb()
    db.rows["prebuilt-skill"] = {
        "skill_name": "prebuilt-skill",
        "source_type": SOURCE_PREBUILT,
        "skill_source": "https://example.com/pre.zip",
        "skill_version": "1.0.0",
        "skill_id": "pre-1",
    }
    _patch_skill_db(monkeypatch, db)

    result = asyncio.run(
        SkillWhitelistSynchronizer(
            workspace,
            service_id="svc",
            agent_id="bot",
        ).reconcile_disk_into_ledger()
    )

    assert result.ok is True
    assert db.rows["prebuilt-skill"]["source_type"] == SOURCE_PREBUILT
    assert result.enabled_skill_dirs == ["prebuilt-skill"]
    assert not any(s.startswith("reconciled_disk:") for s in result.succeeded)


def test_reconcile_disk_into_ledger_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """热刷新路径：只跑盘→库对账，不依赖预制模板。"""
    from jiuwenswarm.agents.harness.common.installed_skill import SOURCE_USER
    from jiuwenswarm.server.runtime.skill.skill_whitelist import SkillWhitelistSynchronizer

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    skill_dir = skills_dir / "disk-only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: disk-only\n---\n", encoding="utf-8")

    db = _FakeSkillDb()
    _patch_skill_db(monkeypatch, db)

    result = asyncio.run(
        SkillWhitelistSynchronizer(
            workspace,
            service_id="svc",
            agent_id="bot",
        ).reconcile_disk_into_ledger()
    )

    assert result.ok is True
    assert db.rows["disk-only"]["source_type"] == SOURCE_USER
    assert result.enabled_skill_dirs == ["disk-only"]
    assert "reconciled_disk:disk-only" in result.succeeded


def test_multi_tenant_skill_dirs_requires_ids_for_tenant_path() -> None:
    with pytest.raises(TypeError, match="tenant scope is required"):
        get_tenant_agent_skills_dirs()
    # 无 tenant ids → 单租户回退
    dirs = get_multi_tenant_skill_dirs()
    assert len(dirs) == 1
    assert dirs[0] == get_agent_skills_dir()
