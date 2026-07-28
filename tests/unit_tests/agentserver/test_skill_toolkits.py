from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import MagicMock, patch

from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager
from jiuwenswarm.agents.harness.common.tools.skill_toolkits import SkillToolkit
from jiuwenswarm.agents.harness.common.recommendation.situation_report import (
    _format_skills_for_llm,
)


def test_uninstall_skill_removes_local_skill_without_plugin_record(tmp_path):
    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    toolkit = SkillToolkit(manager)

    source = tmp_path / "source-skill"
    source.mkdir()
    (source / "SKILL.md").write_text(
        "---\nname: local-only-skill\ndescription: local only\n---\nbody\n",
        encoding="utf-8",
    )

    imported = asyncio.run(manager.handle_skills_import_local({"path": str(source)}))
    assert imported["success"] is True
    assert manager.get_installed_plugins() == []
    assert (tmp_path / "workspace" / "skills" / "local-only-skill").is_dir()

    result = asyncio.run(toolkit.uninstall_skill("local-only-skill"))

    assert result["success"] is True
    assert result["removed"] is True
    assert not (tmp_path / "workspace" / "skills" / "local-only-skill").exists()
    assert manager.get_local_skills() == []


def test_search_builtin_skills_matches_name_and_description(tmp_path):
    builtin_dir = tmp_path / "builtin_skills"
    builtin_dir.mkdir()
    user_skills_dir = tmp_path / "user_skills"
    user_skills_dir.mkdir()

    skill_a = builtin_dir / "deep-research"
    skill_a.mkdir()
    (skill_a / "SKILL.md").write_text(
        "---\nname: openJiuwen-DeepSearch\ndescription: deep search and research report\n---\nbody\n",
        encoding="utf-8",
    )

    skill_b = builtin_dir / "ppt-helper"
    skill_b.mkdir()
    (skill_b / "SKILL.md").write_text(
        "---\nname: ppt-helper\ndescription: generate PPT slides\n---\nbody\n",
        encoding="utf-8",
    )

    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    toolkit = SkillToolkit(manager)

    with patch(
        "jiuwenswarm.common.utils.get_builtin_skills_dir",
        return_value=builtin_dir,
    ), patch(
        "jiuwenswarm.common.utils.get_agent_skills_dir",
        return_value=user_skills_dir,
    ):
        results = toolkit._search_builtin_skills("deep", set(), 10)

    assert len(results) == 1
    assert results[0]["name"] == "openJiuwen-DeepSearch"
    assert results[0]["source"] == "builtin"
    assert results[0]["identifier"] == "openJiuwen-DeepSearch"
    assert results[0]["is_builtin"] is True
    assert results[0]["is_builtin_source"] is True


def test_search_builtin_skills_skips_already_installed(tmp_path):
    builtin_dir = tmp_path / "builtin_skills"
    builtin_dir.mkdir()
    user_skills_dir = tmp_path / "user_skills"
    user_skills_dir.mkdir()

    skill_a = builtin_dir / "deep-research"
    skill_a.mkdir()
    (skill_a / "SKILL.md").write_text(
        "---\nname: openJiuwen-DeepSearch\ndescription: deep search\n---\nbody\n",
        encoding="utf-8",
    )

    installed_copy = user_skills_dir / "deep-research"
    installed_copy.mkdir()

    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    toolkit = SkillToolkit(manager)

    with patch(
        "jiuwenswarm.common.utils.get_builtin_skills_dir",
        return_value=builtin_dir,
    ), patch(
        "jiuwenswarm.common.utils.get_agent_skills_dir",
        return_value=user_skills_dir,
    ):
        results = toolkit._search_builtin_skills("deep", set(), 10)

    assert len(results) == 0


def test_search_skill_with_builtin_source(tmp_path):
    builtin_dir = tmp_path / "builtin_skills"
    builtin_dir.mkdir()
    user_skills_dir = tmp_path / "user_skills"
    user_skills_dir.mkdir()

    skill_a = builtin_dir / "deep-research"
    skill_a.mkdir()
    (skill_a / "SKILL.md").write_text(
        "---\nname: openJiuwen-DeepSearch\ndescription: deep search and report\n---\nbody\n",
        encoding="utf-8",
    )

    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    toolkit = SkillToolkit(manager)

    with patch(
        "jiuwenswarm.common.utils.get_builtin_skills_dir",
        return_value=builtin_dir,
    ), patch(
        "jiuwenswarm.common.utils.get_agent_skills_dir",
        return_value=user_skills_dir,
    ):
        result = asyncio.run(toolkit.search_skill("deep", source="builtin"))

    assert result["success"] is True
    assert result["source"] == "builtin"
    assert len(result["items"]) == 1
    assert result["items"][0]["name"] == "openJiuwen-DeepSearch"


def test_install_skill_builtin_source_routes_to_handle_skills_install_builtin(tmp_path):
    builtin_dir = tmp_path / "builtin_skills"
    builtin_dir.mkdir()
    user_skills_dir = tmp_path / "workspace" / "skills"
    user_skills_dir.mkdir(parents=True)

    skill_a = builtin_dir / "my-builtin-skill"
    skill_a.mkdir()
    (skill_a / "SKILL.md").write_text(
        "---\nname: my-builtin-skill\ndescription: a builtin skill\n---\nbody\n",
        encoding="utf-8",
    )

    manager = SkillManager(workspace_dir=str(tmp_path / "workspace"))
    toolkit = SkillToolkit(manager)

    with patch(
        "jiuwenswarm.server.runtime.skill.skill_manager.get_builtin_skills_dir",
        return_value=builtin_dir,
    ):
        result = asyncio.run(
            toolkit.install_skill("my-builtin-skill", source="builtin")
        )

    assert result["success"] is True
    assert result["source"] == "builtin"
    assert (user_skills_dir / "my-builtin-skill").is_dir()


def test_format_skills_for_llm_distinguishes_install_status():
    skills = [
        {"name": "installed-skill", "description": "already there", "installed": True, "source": "local"},
        {"name": "builtin-skill", "description": "builtin not installed", "installed": False, "source": "builtin"},
        {"name": "marketplace-skill", "description": "from marketplace", "installed": False, "source": "clawhub"},
    ]
    rendered = _format_skills_for_llm(skills)

    assert "- installed-skill | already there [已安装]" in rendered
    assert "- builtin-skill | builtin not installed [未安装·内置技能]" in rendered
    assert "- marketplace-skill | from marketplace [未安装]" in rendered
