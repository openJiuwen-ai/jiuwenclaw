"""SkillManager behavior with optional builtin-skill packages installed or absent."""

from __future__ import annotations

import pytest

from jiuwenswarm.common import utils as common_utils
from jiuwenswarm.server.runtime.skill import skill_manager as skill_manager_module
from jiuwenswarm.server.runtime.skill.skill_manager import (
    SkillManager,
    _find_builtin_skill_path,
)

from tests.unit_tests.common.test_optional_skill_packages import (
    ASCEND_SKILLS,
    make_fake_skills_package,
)


@pytest.fixture
def isolated_user_skills_dir(tmp_path, monkeypatch):
    user_dir = tmp_path / "user-skills"
    monkeypatch.setattr(
        skill_manager_module, "get_agent_skills_dir", lambda: user_dir
    )
    return user_dir


def test_scan_builtin_skills_lists_optional_package_skill(
    tmp_path, monkeypatch, isolated_user_skills_dir
):
    make_fake_skills_package(tmp_path, monkeypatch)
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))

    results = manager._scan_builtin_skills()
    by_name = {r["name"]: r for r in results}

    assert "dummy-skill" in by_name
    entry = by_name["dummy-skill"]
    assert entry["source"] == "builtin"
    assert entry["is_builtin"] is True
    assert entry["is_builtin_source"] is True
    assert entry["installed"] is False


def test_get_builtin_skills_dir_stays_patchable_on_module(tmp_path, monkeypatch):
    # Other suites isolate builtin skills by patching this exact dotted name
    fake_primary = tmp_path / "primary-builtin"
    fake_primary.mkdir()
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.skill.skill_manager.get_builtin_skills_dir",
        lambda: fake_primary,
    )

    assert skill_manager_module._builtin_skills_dirs()[0] == fake_primary
    assert _find_builtin_skill_path("skill-creator") is None


def test_find_builtin_skill_path_covers_optional_dirs(tmp_path, monkeypatch):
    fake_skills_dir = make_fake_skills_package(tmp_path, monkeypatch)

    found = _find_builtin_skill_path("dummy-skill")
    assert found is not None
    assert found.resolve() == (fake_skills_dir / "dummy-skill").resolve()
    assert _find_builtin_skill_path("skill-creator") is not None
    assert _find_builtin_skill_path("no-such-skill-anywhere") is None


@pytest.mark.asyncio
async def test_install_builtin_from_optional_package(
    tmp_path, monkeypatch, isolated_user_skills_dir
):
    make_fake_skills_package(tmp_path, monkeypatch)
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))

    result = await manager.handle_skills_install_builtin({"name": "dummy-skill"})

    assert result["success"] is True
    installed = manager._skills_dir / "dummy-skill"
    assert (installed / "SKILL.md").is_file()

    local = {r["name"]: r for r in manager._scan_local_skills()}
    assert local["dummy-skill"]["is_builtin_source"] is True


def test_ascend_skills_absent_without_optional_package(
    tmp_path, monkeypatch, isolated_user_skills_dir
):
    monkeypatch.setattr(common_utils, "_OPTIONAL_SKILL_PACKAGES", ())
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))

    names = {r["name"] for r in manager._scan_builtin_skills()}
    for ascend_name in ASCEND_SKILLS:
        assert ascend_name not in names
