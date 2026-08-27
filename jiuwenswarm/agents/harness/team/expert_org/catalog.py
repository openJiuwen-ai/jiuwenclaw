# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Host ExpertGroupCatalog: scan AgentGroup packages and return descriptors."""

from __future__ import annotations

import json
import logging
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExpertGroupDescriptor:
    """AgentGroup template metadata for OrganizationRuntime Catalog injection.

    Shape matches openjiuwen ``ExpertGroupDescriptor`` (duck-typed Protocol).
    """

    agent_group_name: str
    display_name: str = ""
    description: str = ""
    capabilities: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["capabilities"] = list(self.capabilities)
        return data


def _iter_agent_group_dirs() -> list[tuple[str, Path]]:
    """Enumerate AgentGroup packages without adding APIs on extension_package_manager."""
    from jiuwenswarm.agents.harness.team.expert_org.agent_group_scan import (
        scan_agent_group_dirs,
    )
    from jiuwenswarm.server.runtime import extension_package_manager as epm

    kind = epm._AGENT_GROUP_KIND
    return scan_agent_group_dirs(
        [
            ("local", epm._local_root(kind)),
            ("built_in", epm._built_in_root(kind)),
            ("resources", epm._resources_root(kind)),
        ]
    )


def _resolve_agent_group_dir(name: str) -> Path:
    from jiuwenswarm.server.runtime.extension_package_manager import (
        resolve_agent_group_dir,
    )

    return resolve_agent_group_dir(name)


def _load_agent_group_package(path: Path) -> dict[str, Any]:
    """Lazy import so unit tests can monkeypatch without loading swarm assembly."""
    from jiuwenswarm.agents.swarm.agent_group import load_agent_group_package

    return load_agent_group_package(path)


def _read_group_manifest(package_dir: Path) -> dict[str, Any]:
    manifest_path = package_dir / "manifest.json"
    payload = json.loads(manifest_path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError(f"agent_group manifest must be an object: {manifest_path}")
    return payload


def _capabilities_from_manifest(manifest: dict[str, Any]) -> tuple[str, ...]:
    raw = manifest.get("capabilities", [])
    if not isinstance(raw, list):
        return ()
    names: list[str] = []
    seen: set[str] = set()
    for item in raw:
        name = str(item or "").strip()
        if not name or name in seen:
            continue
        seen.add(name)
        names.append(name)
    return tuple(names)


def descriptor_from_agent_group_dir(
    agent_group_name: str, package_dir: Path
) -> ExpertGroupDescriptor:
    """Validate one package with load_agent_group_package and map to a descriptor."""
    templates = _load_agent_group_package(package_dir)
    manifest = _read_group_manifest(package_dir)
    leader = templates.get("leader")
    leader_card = getattr(leader, "agent_card", None) if leader is not None else None
    display_name = str(getattr(leader_card, "name", "") or "").strip() or agent_group_name
    instruction = manifest.get("instruction", "")
    if not isinstance(instruction, str):
        instruction = ""
    description = instruction.strip() or str(
        getattr(leader_card, "description", "") or ""
    ).strip()
    return ExpertGroupDescriptor(
        agent_group_name=agent_group_name,
        display_name=display_name,
        description=description,
        capabilities=_capabilities_from_manifest(manifest),
    )


class JiuwenExpertGroupCatalog:
    """Scan AgentGroup roots, validate packages, return ExpertGroupDescriptor list."""

    def list(
        self, *, capabilities: set[str] | None = None
    ) -> list[ExpertGroupDescriptor]:
        required = set(capabilities or ())
        results: list[ExpertGroupDescriptor] = []
        for name, package_dir in _iter_agent_group_dirs():
            try:
                descriptor = descriptor_from_agent_group_dir(name, package_dir)
            except (ValueError, OSError, json.JSONDecodeError) as exc:
                logger.warning(
                    "[ExpertGroupCatalog] skip invalid agent_group %s: %s",
                    name,
                    exc,
                )
                continue
            if required and not required.issubset(set(descriptor.capabilities)):
                continue
            results.append(descriptor)
        return results

    def get(self, name: str) -> ExpertGroupDescriptor:
        package_dir = _resolve_agent_group_dir(str(name).strip())
        return descriptor_from_agent_group_dir(str(name).strip(), package_dir)


__all__ = [
    "ExpertGroupDescriptor",
    "JiuwenExpertGroupCatalog",
    "descriptor_from_agent_group_dir",
]
