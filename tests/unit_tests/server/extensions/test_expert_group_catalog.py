# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for JiuwenExpertGroupCatalog (scan + load → Descriptor)."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.team.expert_org.catalog import (
    JiuwenExpertGroupCatalog,
    descriptor_from_agent_group_dir,
)


def _write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload), encoding="utf-8")


def _install_group(
    root: Path,
    name: str,
    *,
    capabilities: list[str] | None = None,
    instruction: str = "group instruction",
    display_name: str = "Leader Display",
) -> Path:
    package = root / name
    manifest: dict = {
        "name": name,
        "package_type": "agent_group",
        "instruction": instruction,
        "agents": ["leader", "member1"],
    }
    if capabilities is not None:
        manifest["capabilities"] = capabilities
    _write_json(package / "manifest.json", manifest)
    for agent_name in ("leader", "member1"):
        _write_json(
            package / "agents" / agent_name / "manifest.json",
            {
                "package_type": "agent_template",
                "name": display_name if agent_name == "leader" else agent_name,
                "description": f"{agent_name} description",
                "persona": {"dir": "." if agent_name == "leader" else "./persona"},
            },
        )
        persona = package / "agents" / agent_name / "persona" / f"{agent_name}.md"
        persona.parent.mkdir(parents=True, exist_ok=True)
        persona.write_text(f"# {agent_name}\n", encoding="utf-8")
    (package / "agents" / "leader" / "AGENT.md").write_text(
        "# Leader rules\n", encoding="utf-8"
    )
    return package


def test_catalog_list_maps_validated_packages(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    frontend = _install_group(
        tmp_path,
        "frontend-group",
        capabilities=["frontend", "react"],
        instruction="UI experts",
        display_name="Frontend Lead",
    )
    backend = _install_group(
        tmp_path,
        "backend-group",
        capabilities=["backend"],
        instruction="API experts",
    )

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.expert_org.catalog._iter_agent_group_dirs",
        lambda: [
            ("backend-group", backend),
            ("frontend-group", frontend),
        ],
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.expert_org.catalog._resolve_agent_group_dir",
        lambda name: frontend if name == "frontend-group" else backend,
    )

    def _fake_load(path: Path):
        assert path.name in {"frontend-group", "backend-group"}
        if path.name == "frontend-group":
            return {
                "leader": SimpleNamespace(
                    agent_card=SimpleNamespace(
                        name="Frontend Lead", description="lead desc"
                    )
                ),
                "member1": SimpleNamespace(
                    agent_card=SimpleNamespace(name="member1", description="")
                ),
            }
        return {
            "leader": SimpleNamespace(
                agent_card=SimpleNamespace(name="Backend Lead", description="api")
            )
        }

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.expert_org.catalog._load_agent_group_package",
        _fake_load,
    )

    catalog = JiuwenExpertGroupCatalog()
    all_groups = catalog.list()
    assert [item.agent_group_name for item in all_groups] == [
        "backend-group",
        "frontend-group",
    ]
    frontend_desc = catalog.get("frontend-group")
    assert frontend_desc.display_name == "Frontend Lead"
    assert frontend_desc.description == "UI experts"
    assert frontend_desc.capabilities == ("frontend", "react")
    assert frontend_desc.to_dict()["capabilities"] == ["frontend", "react"]

    filtered = catalog.list(capabilities={"frontend"})
    assert [item.agent_group_name for item in filtered] == ["frontend-group"]


def test_catalog_list_skips_packages_that_fail_load(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    broken = _install_group(tmp_path, "broken-group", capabilities=["x"])
    good = _install_group(tmp_path, "good-group", capabilities=["y"])
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.expert_org.catalog._iter_agent_group_dirs",
        lambda: [("broken-group", broken), ("good-group", good)],
    )

    def _fake_load(path: Path):
        if path.name == "broken-group":
            raise ValueError("invalid roster")
        return {
            "leader": SimpleNamespace(
                agent_card=SimpleNamespace(name="Good", description="")
            )
        }

    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.expert_org.catalog._load_agent_group_package",
        _fake_load,
    )

    catalog = JiuwenExpertGroupCatalog()
    groups = catalog.list()
    assert [item.agent_group_name for item in groups] == ["good-group"]


def test_descriptor_from_dir_uses_load_result(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    package = _install_group(
        tmp_path,
        "sample",
        capabilities=["analysis"],
        instruction="do analysis",
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.expert_org.catalog._load_agent_group_package",
        lambda _path: {
            "leader": SimpleNamespace(
                agent_card=SimpleNamespace(name="专家团负责人", description="fallback")
            )
        },
    )
    descriptor = descriptor_from_agent_group_dir("sample", package)
    assert descriptor.agent_group_name == "sample"
    assert descriptor.display_name == "专家团负责人"
    assert descriptor.description == "do analysis"
    assert descriptor.capabilities == ("analysis",)
