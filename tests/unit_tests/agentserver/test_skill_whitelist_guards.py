"""Skill 白名单：租户 ID / 路径逃生通道回归；预制 sync 以本地 manifest + 磁盘为准."""

import asyncio
import json
from pathlib import Path

import pytest

from jiuwenswarm.server.runtime.skill.skill_whitelist import (
    MANIFEST_FILENAME,
    is_skill_whitelist_tenant,
)
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


def _read_manifest(skills_dir: Path) -> dict:
    path = skills_dir / MANIFEST_FILENAME
    if not path.is_file():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def _patch_skill_manager(monkeypatch: pytest.MonkeyPatch, install_fn) -> None:
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_whitelist.SkillManager",
        lambda **kwargs: type(
            "FakeSkillManager",
            (),
            {"install_skill_sync": staticmethod(install_fn)},
        )(),
    )


def test_is_skill_whitelist_tenant_rejects_empty_ids(agent_runtime_env) -> None:
    assert is_skill_whitelist_tenant("", "") is False
    assert is_skill_whitelist_tenant("  ", "bot") is False
    assert is_skill_whitelist_tenant("bot", None) is False


def test_is_skill_whitelist_tenant_legacy_tenants(agent_runtime_env) -> None:
    # default 租户仍走白名单；仅 ACP 全局租户排除
    assert is_skill_whitelist_tenant("default", "default") is True
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
    install_called = {"count": 0}

    def _fake_install(_url: str, _force: bool, _mirror: None) -> dict:
        install_called["count"] += 1
        skill_dir = skills_dir / "shared-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: shared-skill\n---\n", encoding="utf-8")
        return {"ok": True, "skill_name": "shared-skill"}

    _patch_skill_manager(monkeypatch, _fake_install)

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
    manifest = _read_manifest(skills_dir)
    assert manifest["id-a"]["skill_name"] == "shared-skill"
    assert manifest["id-b"]["skill_name"] == "shared-skill"
    assert manifest["id-b"]["version"] == version
    assert manifest["id-b"]["source"] == source


def test_manifest_skips_redownload_on_second_sync(
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
    install_called = {"count": 0}

    def _fake_install(_url: str, _force: bool, _mirror: None) -> dict:
        install_called["count"] += 1
        skill_dir = skills_dir / "cached-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: cached-skill\n---\n", encoding="utf-8")
        return {"ok": True, "skill_name": "cached-skill"}

    _patch_skill_manager(monkeypatch, _fake_install)

    sync = SkillWhitelistSynchronizer(workspace, "svc", "bot")
    asyncio.run(sync.sync(config))
    assert install_called["count"] == 1
    assert _read_manifest(skills_dir)["id-a"]["skill_name"] == "cached-skill"

    asyncio.run(sync.sync(config))
    assert install_called["count"] == 1


def test_manifest_same_version_with_disk_missing_redownloads(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """manifest 有同版本但盘缺失时仍会重下补盘。"""
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
    (skills_dir / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "id-a": {
                    "skill_name": "cached-skill",
                    "version": version,
                    "source": source,
                }
            },
            ensure_ascii=False,
            indent=2,
        )
        + "\n",
        encoding="utf-8",
    )
    config = AgentSkillWhitelistConfig(
        agent_id="bot",
        service_id="svc",
        skills=[SkillWhitelistItem(id="id-a", version=version, source=source)],
    )
    install_called = {"count": 0}

    def _fake_install(_url: str, _force: bool, _mirror: None) -> dict:
        install_called["count"] += 1
        skill_dir = skills_dir / "cached-skill"
        skill_dir.mkdir(parents=True, exist_ok=True)
        (skill_dir / "SKILL.md").write_text("---\nname: cached-skill\n---\n", encoding="utf-8")
        return {"ok": True, "skill_name": "cached-skill"}

    _patch_skill_manager(monkeypatch, _fake_install)

    result = asyncio.run(SkillWhitelistSynchronizer(workspace, "svc", "bot").sync(config))
    assert install_called["count"] == 1
    assert result.ok is True
    assert "cached-skill" in result.enabled_skill_dirs
    assert _read_manifest(skills_dir)["id-a"]["skill_name"] == "cached-skill"


def test_disk_ready_skill_included_in_enabled_on_sync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """盘上已有就绪目录（用户自装等）进入启用集；保留名不纳入。"""
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

    _patch_skill_manager(
        monkeypatch,
        lambda *_a, **_k: {"ok": False, "detail": "unused"},
    )

    result = asyncio.run(
        SkillWhitelistSynchronizer(
            workspace,
            service_id="svc",
            agent_id="bot",
        ).sync(AgentSkillWhitelistConfig(agent_id="bot", service_id="svc"))
    )

    assert result.enabled_skill_dirs == ["orphan-skill"]
    assert "_marketplace" not in result.enabled_skill_dirs
    assert _read_manifest(skills_dir) == {}


def test_reconcile_disk_lists_ready_skills_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """热刷新：只扫盘上就绪目录，不写 installed_skill / 不改 manifest。"""
    from jiuwenswarm.server.runtime.skill.skill_whitelist import SkillWhitelistSynchronizer

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    skill_dir = skills_dir / "prebuilt-skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: prebuilt-skill\n---\n", encoding="utf-8")

    (skills_dir / MANIFEST_FILENAME).write_text(
        json.dumps(
            {
                "pre-1": {
                    "skill_name": "prebuilt-skill",
                    "version": "1.0.0",
                    "source": "https://example.com/pre.zip",
                }
            },
            ensure_ascii=False,
        )
        + "\n",
        encoding="utf-8",
    )
    before = _read_manifest(skills_dir)

    result = asyncio.run(
        SkillWhitelistSynchronizer(
            workspace,
            service_id="svc",
            agent_id="bot",
        ).reconcile_disk_into_ledger()
    )

    assert result.ok is True
    assert result.enabled_skill_dirs == ["prebuilt-skill"]
    assert result.succeeded == []
    assert _read_manifest(skills_dir) == before


def test_reconcile_disk_into_ledger_only(tmp_path: Path) -> None:
    """热刷新路径：只返回盘上就绪技能，不依赖预制模板。"""
    from jiuwenswarm.server.runtime.skill.skill_whitelist import SkillWhitelistSynchronizer

    workspace = tmp_path / "tenant_ws"
    skills_dir = workspace / "skills"
    skill_dir = skills_dir / "disk-only"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text("---\nname: disk-only\n---\n", encoding="utf-8")

    result = asyncio.run(
        SkillWhitelistSynchronizer(
            workspace,
            service_id="svc",
            agent_id="bot",
        ).reconcile_disk_into_ledger()
    )

    assert result.ok is True
    assert result.enabled_skill_dirs == ["disk-only"]
    assert result.succeeded == []
    assert not (skills_dir / MANIFEST_FILENAME).is_file()


def test_multi_tenant_skill_dirs_requires_ids_for_tenant_path() -> None:
    with pytest.raises(TypeError, match="tenant scope is required"):
        get_tenant_agent_skills_dirs()
    # 无 tenant ids → 单租户回退
    dirs = get_multi_tenant_skill_dirs()
    assert len(dirs) == 1
    assert dirs[0] == get_agent_skills_dir()
