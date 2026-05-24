# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from pathlib import Path

from jiuwenclaw.agentserver.skilldev_agent.utils.direct_import import (
    find_skill_root,
    validate_direct_import_skill,
)


def test_validate_direct_import_skill_ok(tmp_path: Path) -> None:
    skill_root = tmp_path / "my-skill"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        "---\n"
        "name: my-skill\n"
        "description: 一个用于测试的 skill\n"
        "---\n"
        "# Body\n"
        "hello\n",
        encoding="utf-8",
    )
    valid, message = validate_direct_import_skill(skill_root)
    assert valid is True
    assert "通过" in message


def test_validate_direct_import_skill_name_mismatch(tmp_path: Path) -> None:
    skill_root = tmp_path / "wrong-dir"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: test\n---\nbody\n",
        encoding="utf-8",
    )
    valid, message = validate_direct_import_skill(skill_root)
    assert valid is False
    assert "父目录名" in message


def test_validate_direct_import_skill_collects_all_errors(tmp_path: Path) -> None:
    skill_root = tmp_path / "wrong-dir"
    skill_root.mkdir()
    (skill_root / "SKILL.md").write_text(
        "---\nname: my-skill\ndescription: \n---\n\n",
        encoding="utf-8",
    )
    valid, message = validate_direct_import_skill(skill_root)
    assert valid is False
    assert "父目录名" in message
    assert "description 不能为空" in message
    assert "正文不能为空" in message


def test_find_skill_root_nested(tmp_path: Path) -> None:
    skill_dir = tmp_path / "skill"
    nested = skill_dir / "nested-skill"
    nested.mkdir(parents=True)
    (nested / "SKILL.md").write_text("---\nname: nested-skill\ndescription: d\n---\n", encoding="utf-8")
    assert find_skill_root(skill_dir) == nested
