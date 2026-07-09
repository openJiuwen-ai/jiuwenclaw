from __future__ import annotations

from jiuwenavatar.server.runtime.skill.skill_manager import SkillManager


def _write_skill(skill_dir, body: str = "body") -> None:
    skill_dir.mkdir(parents=True, exist_ok=True)
    (skill_dir / "SKILL.md").write_text(
        f"---\nname: {skill_dir.name}\ndescription: test\n---\n\n{body}\n",
        encoding="utf-8",
    )


def _prepare_skill_paths(monkeypatch, tmp_path, builtin_root):
    agent_root = tmp_path / "agent"
    skills_dir = agent_root / "skills"
    skills_dir.mkdir(parents=True, exist_ok=True)

    monkeypatch.setattr(
        "jiuwenavatar.server.runtime.skill.skill_manager.get_builtin_skills_dirs",
        lambda: [builtin_root],
    )
    monkeypatch.setattr(
        "jiuwenavatar.server.runtime.skill.skill_manager.resolve_builtin_skill_dir",
        lambda name: builtin_root / name if (builtin_root / name).is_dir() else None,
    )

    return agent_root, skills_dir


def test_sync_builtin_skills_updates_changed_package_copy(monkeypatch, tmp_path):
    builtin_root = tmp_path / "builtin"
    agent_root, skills_dir = _prepare_skill_paths(monkeypatch, tmp_path, builtin_root)
    builtin_skill = builtin_root / "demo-skill"
    user_skill = skills_dir / "demo-skill"

    _write_skill(builtin_skill, "new content")
    _write_skill(user_skill, "old content")
    (user_skill / "scripts").mkdir()
    (user_skill / "scripts" / "old_tool.py").write_text("legacy", encoding="utf-8")
    (user_skill / "data").mkdir()
    (user_skill / "data" / "community.db").write_bytes(b"runtime data")

    state_file = skills_dir / "skills_state.json"
    state_file.write_text(
        '{"installed_plugins":[{"name":"demo-skill","source":"builtin","marketplace":"builtin"}]}',
        encoding="utf-8",
    )

    manager = SkillManager(workspace_dir=str(agent_root))
    result = manager.sync_builtin_skills_from_package(force=True)

    assert result["updated"] == ["demo-skill"]
    assert "new content" in (user_skill / "SKILL.md").read_text(encoding="utf-8")
    assert not (user_skill / "scripts" / "old_tool.py").exists()
    assert (user_skill / "data" / "community.db").read_bytes() == b"runtime data"


def test_sync_builtin_skills_skips_unchanged_and_evolved(monkeypatch, tmp_path):
    builtin_root = tmp_path / "builtin"
    agent_root, skills_dir = _prepare_skill_paths(monkeypatch, tmp_path, builtin_root)
    builtin_skill = builtin_root / "same-skill"
    user_same = skills_dir / "same-skill"
    user_evolved = skills_dir / "evolved-skill"

    _write_skill(builtin_skill, "same")
    _write_skill(builtin_root / "evolved-skill", "new")
    _write_skill(user_same, "same")
    _write_skill(user_evolved, "old")
    (user_evolved / "evolutions.json").write_text("{}", encoding="utf-8")

    (skills_dir / "skills_state.json").write_text(
        '{"installed_plugins":['
        '{"name":"same-skill","source":"builtin","marketplace":"builtin"},'
        '{"name":"evolved-skill","source":"builtin","marketplace":"builtin"}'
        "]}",
        encoding="utf-8",
    )

    manager = SkillManager(workspace_dir=str(agent_root))
    result = manager.sync_builtin_skills_from_package()

    assert result["updated"] == []
    assert "same-skill" in result["skipped_same"]
    assert "evolved-skill" in result["skipped_evolved"]
