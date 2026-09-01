# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for legacy skills and Dolores path compatibility."""

import asyncio
from pathlib import Path
from unittest.mock import patch

from jiuwenswarm.common import utils
from jiuwenswarm.extensions.dolores.common import utils as dolores_utils
from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager


def _write_skill(skills_dir: Path, name: str, body: str) -> Path:
    skill_dir = skills_dir / name
    (skill_dir / "scripts").mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(body, encoding="utf-8")
    (skill_dir / "scripts" / "run.py").write_text("print('ok')\n", encoding="utf-8")
    return skill_dir


def test_legacy_skill_migration_is_non_destructive_and_idempotent(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "agent" / "workspace" / "skills"
    target_dir = (
        tmp_path
        / "service_default"
        / "agent_default"
        / "agent"
        / "workspace"
        / "skills"
    )
    copied_source = _write_skill(legacy_dir, "copied-skill", "legacy content")
    preserved_source = _write_skill(legacy_dir, "existing-skill", "legacy content")
    preserved_target = _write_skill(target_dir, "existing-skill", "tenant content")

    copied = utils.migrate_legacy_skills_to_default_tenant(tmp_path, target_dir)

    assert copied == ["copied-skill"]
    assert (target_dir / "copied-skill" / "SKILL.md").read_text(encoding="utf-8") == (
        "legacy content"
    )
    assert (target_dir / "copied-skill" / "scripts" / "run.py").is_file()
    assert preserved_target.joinpath("SKILL.md").read_text(encoding="utf-8") == (
        "tenant content"
    )
    assert copied_source.joinpath("SKILL.md").read_text(encoding="utf-8") == (
        "legacy content"
    )
    assert preserved_source.joinpath("SKILL.md").read_text(encoding="utf-8") == (
        "legacy content"
    )
    assert utils.migrate_legacy_skills_to_default_tenant(tmp_path, target_dir) == []


def test_path_resolution_runs_legacy_skill_migration(tmp_path: Path) -> None:
    (tmp_path / "config").mkdir(parents=True)
    legacy_dir = tmp_path / "agent" / "workspace" / "skills"
    _write_skill(legacy_dir, "legacy-skill", "legacy")
    original_cache = (
        utils._initialized,
        utils._config_dir,
        utils._workspace_dir,
        utils._root_dir,
    )

    try:
        with patch.object(utils, "get_user_workspace_dir", return_value=tmp_path):
            utils._resolve_paths(force=True)
        migrated = (
            tmp_path
            / "service_default"
            / "agent_default"
            / "agent"
            / "workspace"
            / "skills"
            / "legacy-skill"
            / "SKILL.md"
        )
        assert migrated.read_text(encoding="utf-8") == "legacy"
    finally:
        (
            utils._initialized,
            utils._config_dir,
            utils._workspace_dir,
            utils._root_dir,
        ) = original_cache


def test_frontend_skill_refresh_picks_up_new_legacy_skill(tmp_path: Path) -> None:
    legacy_dir = tmp_path / "agent" / "workspace" / "skills"
    tenant_workspace = (
        tmp_path
        / "service_default"
        / "agent_default"
        / "agent"
        / "workspace"
    )
    manager = SkillManager(workspace_dir=str(tenant_workspace))

    # Simulate a user dropping a new Skill into the historical directory after
    # the server-side manager has already been constructed.
    _write_skill(
        legacy_dir,
        "late-added-skill",
        "---\nname: late-added-skill\ndescription: compatibility test\n---\nBody",
    )
    with patch.object(utils, "get_user_workspace_dir", return_value=tmp_path):
        payload = asyncio.run(manager.handle_skills_list({}))

    assert (tenant_workspace / "skills" / "late-added-skill" / "SKILL.md").is_file()
    assert any(
        item.get("name") == "late-added-skill"
        for item in payload.get("skills", [])
    )


def test_dolores_agent_paths_delegate_to_stock_resolver(tmp_path: Path) -> None:
    agent_root = tmp_path / "service_s1" / "agent_a1" / "agent"
    workspace = agent_root / "workspace"
    skills = workspace / "skills"

    with (
        patch("jiuwenswarm.common.utils.get_agent_root_dir", return_value=agent_root),
        patch("jiuwenswarm.common.utils.get_agent_workspace_dir", return_value=workspace),
        patch("jiuwenswarm.common.utils.get_agent_skills_dir", return_value=skills),
        patch("jiuwenswarm.common.utils.get_workspace_dir", return_value=workspace),
    ):
        assert dolores_utils.get_agent_root_dir() == agent_root
        assert dolores_utils.get_workspace_dir() == workspace
        assert dolores_utils.get_agent_workspace_dir() == workspace
        assert dolores_utils.get_agent_skills_dir() == skills
