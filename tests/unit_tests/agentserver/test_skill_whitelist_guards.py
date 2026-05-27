"""Skill 白名单：租户 ID / 路径逃生通道回归."""

import asyncio
from pathlib import Path

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


def test_tenant_workspace_requires_both_ids() -> None:
    with pytest.raises(ValueError, match="tenant id required"):
        get_tenant_agent_jiuwenclaw_workspace_dir(None, None)
    with pytest.raises(ValueError, match="tenant id required"):
        get_tenant_agent_skills_dirs("only-service", "")


def test_multi_tenant_skill_dirs_single_tenant_fallback() -> None:
    dirs = get_multi_tenant_skill_dirs(None, None)
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

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.skill_whitelist.get_tenant_agent_jiuwenclaw_workspace_dir",
        lambda _s, _a: workspace,
    )

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

    sync = SkillWhitelistSynchronizer("svc", "bot")
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
    saved = sync.load_manifest_entries()
    assert {e.db_skill_id for e in saved} == {"id-a", "id-b"}
    assert all(e.installed_dir == "shared-skill" for e in saved)


def test_manifest_skips_redownload_on_second_sync(
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

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.skill_whitelist.get_tenant_agent_jiuwenclaw_workspace_dir",
        lambda _s, _a: workspace,
    )

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

    asyncio.run(SkillWhitelistSynchronizer("svc", "bot").sync(config))
    assert install_called["count"] == 1

    asyncio.run(SkillWhitelistSynchronizer("svc", "bot").sync(config))
    assert install_called["count"] == 1


def test_multi_tenant_skill_dirs_requires_both_when_any_id_set() -> None:
    with pytest.raises(ValueError, match="tenant id required"):
        get_multi_tenant_skill_dirs("svc-only", None)
