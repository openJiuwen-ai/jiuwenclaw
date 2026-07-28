# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""schema_loader / discover_all_turbo_faces 单元测试。"""

from __future__ import annotations

from pathlib import Path

import pytest

from jiuwenclaw.agentserver.skill_turbo.online.schema_loader import (
    discover_all_turbo_faces,
    discover_turbo_face,
)


def _write_turbo_skill(skill_dir: Path, *, name: str, source: str, scenario: str = "create_ppt") -> None:
    turbo = skill_dir / "turbo"
    turbo.mkdir(parents=True, exist_ok=True)
    (turbo / "SKILL_TURBO.md").write_text(
        f"---\nname: {name}\ndescription: \"accel for {source}\"\nsource_skill: {source}\n---\n\n# body\n",
        encoding="utf-8",
    )
    (turbo / f"schema_{scenario}.json").write_text(
        '{"scenario_id": "%s", "execution_flow": []}' % scenario,
        encoding="utf-8",
    )


class TestDiscoverAllTurboFacesParentDir:
    @pytest.mark.unit
    def test_discovers_child_skill_when_root_is_skills_parent(self, tmp_path: Path) -> None:
        """JIUWENCLAW_SHARED_SKILLS_DIRS 指向 office-claw-skills 父目录时应扫子 skill。"""
        parent = tmp_path / "office-claw-skills"
        parent.mkdir()
        (parent / "manifest.yaml").write_text("skills: {}\n", encoding="utf-8")
        (parent / "refs").mkdir()
        pptx = parent / "pptx-craft"
        pptx.mkdir()
        (pptx / "SKILL.md").write_text("# skill\n", encoding="utf-8")
        _write_turbo_skill(pptx, name="pptx-craft_turbo", source="pptx-craft")

        faces = discover_all_turbo_faces([str(parent)])
        assert len(faces) == 1
        assert faces[0].turbo_name == "pptx-craft_turbo"
        assert faces[0].source_skill == "pptx-craft"
        assert faces[0].scenarios == ("create_ppt",)
        assert Path(faces[0].skill_root) == pptx.resolve()

    @pytest.mark.unit
    def test_discovers_direct_skill_root(self, tmp_path: Path) -> None:
        """传入 skill 根目录本身时仍可探测（兼容旧行为）。"""
        skill = tmp_path / "pptx-craft"
        skill.mkdir()
        _write_turbo_skill(skill, name="pptx-craft_turbo", source="pptx-craft")

        faces = discover_all_turbo_faces([str(skill)])
        assert len(faces) == 1
        assert faces[0].skill_root == str(skill.resolve())

    @pytest.mark.unit
    def test_dedupes_parent_and_child_same_skill(self, tmp_path: Path) -> None:
        parent = tmp_path / "office-claw-skills"
        skill = parent / "pptx-craft"
        skill.mkdir(parents=True)
        _write_turbo_skill(skill, name="pptx-craft_turbo", source="pptx-craft")

        faces = discover_all_turbo_faces([str(parent), str(skill)])
        assert len(faces) == 1

    @pytest.mark.unit
    def test_skips_child_without_turbo(self, tmp_path: Path) -> None:
        parent = tmp_path / "office-claw-skills"
        parent.mkdir()
        (parent / "xlsx-craft").mkdir()
        (parent / "xlsx-craft" / "SKILL.md").write_text("# x\n", encoding="utf-8")
        pptx = parent / "pptx-craft"
        pptx.mkdir()
        _write_turbo_skill(pptx, name="pptx-craft_turbo", source="pptx-craft")

        faces = discover_all_turbo_faces([str(parent)])
        assert [f.source_skill for f in faces] == ["pptx-craft"]


class TestDiscoverTurboFace:
    @pytest.mark.unit
    def test_missing_turbo_returns_none(self, tmp_path: Path) -> None:
        assert discover_turbo_face(str(tmp_path)) is None
