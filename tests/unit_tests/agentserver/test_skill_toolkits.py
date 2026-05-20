from __future__ import annotations

import asyncio

from jiuwenswarm.server.runtime.skill.skill_manager import SkillManager
from jiuwenswarm.agents.harness.common.tools.skill_toolkits import SkillToolkit


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
