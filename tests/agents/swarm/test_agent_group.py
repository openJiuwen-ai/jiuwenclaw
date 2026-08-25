# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Strict AgentGroup package loading tests."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from jiuwenswarm.agents.swarm.agent_group import load_agent_group_package


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _minimal_group(tmp_path: Path) -> Path:
    group = tmp_path / "group"
    _write_json(
        group / "manifest.json",
        {
            "name": "group",
            "package_type": "agent_group",
            "agents": ["leader", "member1"],
        },
    )
    for name in ("leader", "member1"):
        _write_json(
            group / "agents" / name / "manifest.json",
            {
                "package_type": "agent_template",
                "name": f"{name} display name",
                "description": f"{name} description",
                "persona": {"dir": "." if name == "leader" else "./persona"},
            },
        )
        persona = group / "agents" / name / "persona" / f"{name}.md"
        persona.parent.mkdir(parents=True, exist_ok=True)
        persona.write_text(f"# {name}\n", encoding="utf-8")
    (group / "agents" / "leader" / "AGENT.md").write_text(
        "# Leader rules\n",
        encoding="utf-8",
    )
    return group


def test_load_agent_group_uses_directory_as_id_and_name_as_display_name(
    tmp_path: Path,
) -> None:
    group = _minimal_group(tmp_path)

    templates = load_agent_group_package(group)

    assert templates["leader"].agent_card.id == "leader"
    assert templates["leader"].agent_card.name == "leader display name"
    assert templates["member1"].agent_card.id == "member1"
    assert templates["member1"].agent_card.name == "member1 display name"


def test_load_agent_group_rejects_member_agent_md(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    (group / "agents" / "member1" / "AGENT.md").write_text(
        "# unexpected\n",
        encoding="utf-8",
    )

    with pytest.raises(ValueError, match="must not contain AGENT.md"):
        load_agent_group_package(group)


def test_load_agent_group_discovers_unlisted_skills(tmp_path: Path) -> None:
    group = _minimal_group(tmp_path)
    skill_dir = group / "skills" / "discovered_skill"
    skill_dir.mkdir(parents=True)
    (skill_dir / "SKILL.md").write_text(
        "# Discovered skill\n",
        encoding="utf-8",
    )

    templates = load_agent_group_package(group)

    for template in templates.values():
        assert [Path(skill.dir).name for skill in template.skills] == [
            "discovered_skill"
        ]


def test_load_agent_group_merges_declared_and_discovered_skills(
    tmp_path: Path,
) -> None:
    group = _minimal_group(tmp_path)
    manifest_path = group / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["skills"] = ["declared_skill"]
    _write_json(manifest_path, manifest)

    for name in ("declared_skill", "another_skill"):
        skill_dir = group / "skills" / name
        skill_dir.mkdir(parents=True)
        (skill_dir / "SKILL.md").write_text(f"# {name}\n", encoding="utf-8")

    templates = load_agent_group_package(group)

    for template in templates.values():
        assert [Path(skill.dir).name for skill in template.skills] == [
            "declared_skill",
            "another_skill",
        ]


@pytest.mark.parametrize(
    "agents",
    [
        ["member1"],
        ["leader", "leader"],
        ["leader", "../member1"],
    ],
)
def test_load_agent_group_rejects_invalid_roster(
    tmp_path: Path,
    agents: list[str],
) -> None:
    group = _minimal_group(tmp_path)
    manifest = json.loads((group / "manifest.json").read_text(encoding="utf-8"))
    manifest["agents"] = agents
    _write_json(group / "manifest.json", manifest)

    with pytest.raises(ValueError):
        load_agent_group_package(group)
