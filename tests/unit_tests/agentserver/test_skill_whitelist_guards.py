"""Skill 白名单：租户 ID / 路径逃生通道回归；预制 sync 直写 DB."""

import asyncio
from pathlib import Path
from typing import Any

import pytest

from jiuwenclaw.agentserver.skill_whitelist import is_skill_whitelist_tenant
from jiuwenclaw.utils import (
    get_agent_skills_dir,
    get_multi_tenant_skill_dirs,
    get_tenant_agent_jiuwenclaw_workspace_dir,
    get_tenant_agent_skills_dirs,
)


def test_is_skill_whitelist_tenant_rejects_empty_ids() -> None:
    assert is_skill_whitelist_tenant("", "") is False
    assert is_skill_whitelist_tenant("  ", "bot") is False
    assert is_skill_whitelist_tenant("bot", None) is False


def test_is_skill_whitelist_tenant_legacy_tenants() -> None:
    assert is_skill_whitelist_tenant("default", "default") is False
    assert is_skill_whitelist_tenant("acp", "global_acp") is False
    assert is_skill_whitelist_tenant("real-agent", "real-svc") is True


def test_tenant_workspace_requires_workspace_key() -> None:
    with pytest.raises(ValueError, match="workspace_key required"):
        get_tenant_agent_jiuwenclaw_workspace_dir()
    with pytest.raises(ValueError, match="workspace_key required"):
        get_tenant_agent_skills_dirs(workspace_key="")


def test_multi_tenant_skill_dirs_single_tenant_fallback() -> None:
    dirs = get_multi_tenant_skill_dirs()
    assert len(dirs) == 1
    assert dirs[0] == get_agent_skills_dir()


def test_parse_agent_skill_whitelist_id_version_source() -> None:
    from jiuwenclaw.agentserver.skill_whitelist import parse_agent_skill_whitelist

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
    from jiuwenclaw.agentserver.skill_whitelist import parse_agent_skill_whitelist

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
    mod = "jiuwenclaw.agentserver.skill_whitelist"
    monkeypatch.setattr(f"{mod}.list_installed_skills", db.list_installed_skills)
    monkeypatch.setattr(f"{mod}.upsert_installed_skill", db.upsert_installed_skill)
    monkeypatch.setattr(f"{mod}.delete_installed_skill", db.delete_installed_skill)


def test_multi_id_same_source_version_skips_second_download(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from jiuwenclaw.agentserver.skill_whitelist import (
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
        "jiuwenclaw.agentserver.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {"install_skill_sync": staticmethod(_fake_install)},
        )(),
    )

    sync = SkillWhitelistSynchronizer(workspace, service_id="svc", agent_id="bot")
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
    from jiuwenclaw.agentserver.skill_whitelist import (
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
        "jiuwenclaw.agentserver.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {"install_skill_sync": staticmethod(_fake_install)},
        )(),
    )

    sync = SkillWhitelistSynchronizer(workspace, service_id="svc", agent_id="bot")
    asyncio.run(sync.sync(config))
    assert install_called["count"] == 1

    asyncio.run(sync.sync(config))
    assert install_called["count"] == 1


def test_db_same_version_with_disk_missing_redownloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库有同版本但盘缺失时仍会重下补盘（不做库有盘无的成功兜底）。"""
    from jiuwenclaw.agentserver.installed_skill import SOURCE_PREBUILT
    from jiuwenclaw.agentserver.skill_whitelist import (
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
        "jiuwenclaw.agentserver.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {"install_skill_sync": staticmethod(_fake_install)},
        )(),
    )

    result = asyncio.run(
        SkillWhitelistSynchronizer(workspace, service_id="svc", agent_id="bot").sync(config)
    )
    assert install_called["count"] == 1
    assert result.ok is True
    assert "cached-skill" in db.rows


def test_prebuilt_download_failure_purges_ghost_db_without_disk(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """库有盘无且下载失败时清掉预制幽灵行，不进入启用集。"""
    from jiuwenclaw.agentserver.installed_skill import SOURCE_PREBUILT
    from jiuwenclaw.agentserver.skill_whitelist import (
        AgentSkillWhitelistConfig,
        SkillWhitelistItem,
        SkillWhitelistSynchronizer,
    )

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    skills_dir.mkdir(parents=True)
    source = "https://example.com/expired.zip"
    version = "1.0.0"
    ghost_name = "investment-due-diligence-team"
    config = AgentSkillWhitelistConfig(
        agent_id="bot",
        service_id="svc",
        skills=[SkillWhitelistItem(id="touzi", version=version, source=source)],
    )
    db = _FakeSkillDb()
    db.rows[ghost_name] = {
        "skill_name": ghost_name,
        "source_type": SOURCE_PREBUILT,
        "skill_source": source,
        "skill_version": version,
        "skill_id": "touzi",
    }
    _patch_skill_db(monkeypatch, db)

    def _fail_install(_url: str, _force: bool, _mirror: None) -> dict:
        return {"ok": False, "detail": "403 Client Error: Forbidden"}

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {"install_skill_sync": staticmethod(_fail_install)},
        )(),
    )

    result = asyncio.run(
        SkillWhitelistSynchronizer(workspace, service_id="svc", agent_id="bot").sync(config)
    )

    assert ghost_name not in db.rows
    assert ghost_name not in result.enabled_skill_dirs
    assert result.ok is False


def test_prebuilt_download_failure_keeps_db_when_disk_still_ready(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """版本 bump 下载失败但旧目录仍在时保留预制行。"""
    from jiuwenclaw.agentserver.installed_skill import SOURCE_PREBUILT
    from jiuwenclaw.agentserver.skill_whitelist import (
        AgentSkillWhitelistConfig,
        SkillWhitelistItem,
        SkillWhitelistSynchronizer,
    )

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    skill_dir = skills_dir / "cached-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: cached-skill\n---\n", encoding="utf-8")

    source = "https://example.com/new.zip"
    config = AgentSkillWhitelistConfig(
        agent_id="bot",
        service_id="svc",
        skills=[SkillWhitelistItem(id="id-a", version="2.0.0", source=source)],
    )
    db = _FakeSkillDb()
    db.rows["cached-skill"] = {
        "skill_name": "cached-skill",
        "source_type": SOURCE_PREBUILT,
        "skill_source": source,
        "skill_version": "1.0.0",
        "skill_id": "id-a",
    }
    _patch_skill_db(monkeypatch, db)

    def _fail_install(_url: str, _force: bool, _mirror: None) -> dict:
        return {"ok": False, "detail": "network timeout"}

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {"install_skill_sync": staticmethod(_fail_install)},
        )(),
    )

    result = asyncio.run(
        SkillWhitelistSynchronizer(workspace, service_id="svc", agent_id="bot").sync(config)
    )

    assert "cached-skill" in db.rows
    assert "cached-skill" in result.enabled_skill_dirs
    assert result.ok is False


def test_user_skill_db_without_disk_removed_on_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """用户自装库有盘无：sync 时清 DB 幽灵行，仅保留磁盘就绪 skill。"""
    from jiuwenclaw.agentserver.installed_skill import SOURCE_USER
    from jiuwenclaw.agentserver.skill_whitelist import (
        AgentSkillWhitelistConfig,
        SkillWhitelistSynchronizer,
    )

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    live_dir = skills_dir / "live-skill"
    live_dir.mkdir(parents=True)
    (live_dir / "SKILL.md").write_text("---\nname: live-skill\n---\n", encoding="utf-8")

    db = _FakeSkillDb()
    db.rows["ghost-skill"] = {
        "skill_name": "ghost-skill",
        "source_type": SOURCE_USER,
        "skill_source": "web:https://example.com/ghost.zip",
        "skill_version": "0.1",
        "skill_id": None,
    }
    db.rows["live-skill"] = {
        "skill_name": "live-skill",
        "source_type": SOURCE_USER,
        "skill_source": "web:https://example.com/live.zip",
        "skill_version": "0.1",
        "skill_id": None,
    }
    _patch_skill_db(monkeypatch, db)

    result = asyncio.run(
        SkillWhitelistSynchronizer(
            workspace,
            service_id="svc",
            agent_id="bot",
        ).sync(AgentSkillWhitelistConfig(agent_id="bot", service_id="svc"))
    )

    assert "ghost-skill" not in db.rows
    assert "live-skill" in db.rows
    assert result.enabled_skill_dirs == ["live-skill"]
    assert "removed_user:ghost-skill" in result.succeeded


def test_multi_tenant_skill_dirs_requires_workspace_key_for_tenant_path() -> None:
    with pytest.raises(ValueError, match="workspace_key required"):
        get_tenant_agent_skills_dirs(workspace_key=None)
    # 无 workspace_key → 单租户回退
    dirs = get_multi_tenant_skill_dirs()
    assert len(dirs) == 1
    assert dirs[0] == get_agent_skills_dir()
