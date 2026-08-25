# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""External tool registry loaded from workspace JSON definitions."""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class ExternalToolSpec:
    """Spec for an external plugin tool resolved from workspace files."""

    plugin_id: str
    tool_name: str
    description: str = ""
    protocol: str = "REST"
    plugin_type: str = "Cloud"
    parameters: dict[str, Any] = field(default_factory=dict)


def resolve_available_tools_dir(workspace: str | Path) -> Path:
    """Prefer skill references; fall back to resources/available-tools."""
    root = Path(workspace)
    references = root / "skill" / "references" / "tools"
    if references.is_dir():
        return references
    available = root / "resources" / "available-tools"
    return available


def _parse_spec_from_dict(data: dict[str, Any], *, fallback_name: str = "") -> ExternalToolSpec | None:
    plugin_id = str(
        data.get("pluginId")
        or data.get("plugin_id")
        or data.get("bundleName")
        or data.get("toolId")
        or ""
    ).strip()
    tool_name = str(
        data.get("toolName")
        or data.get("tool_name")
        or data.get("name")
        or data.get("actionName")
        or fallback_name
        or ""
    ).strip()
    if not plugin_id or not tool_name:
        return None

    parameters = data.get("parameters") or data.get("arguments") or {}
    if not isinstance(parameters, dict):
        parameters = {}

    return ExternalToolSpec(
        plugin_id=plugin_id,
        tool_name=tool_name,
        description=str(data.get("description") or ""),
        protocol=str(data.get("protocol") or "REST").strip() or "REST",
        plugin_type=str(data.get("pluginType") or data.get("plugin_type") or "Cloud").strip()
        or "Cloud",
        parameters=dict(parameters),
    )


def _parse_filename_ids(path: Path) -> tuple[str, str] | None:
    """Parse ``<pluginId>__<toolName>.json`` filenames."""
    stem = path.stem
    if "__" not in stem:
        return None
    plugin_id, tool_name = stem.split("__", 1)
    plugin_id = plugin_id.strip()
    tool_name = tool_name.strip()
    if plugin_id and tool_name:
        return plugin_id, tool_name
    return None


def _load_json_file(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception as exc:  # noqa: BLE001
        logger.warning("[external_tool_registry] failed to read %s: %s", path, exc)
        return None


def load_external_tools(workspace: str | Path) -> dict[tuple[str, str], ExternalToolSpec]:
    """Load ``(pluginId, toolName) -> ExternalToolSpec`` from workspace."""
    tools_dir = resolve_available_tools_dir(workspace)
    registry: dict[tuple[str, str], ExternalToolSpec] = {}
    if not tools_dir.is_dir():
        logger.debug("[external_tool_registry] tools dir missing: %s", tools_dir)
        return registry

    for path in sorted(tools_dir.glob("*.json")):
        if path.name == "tool_usage.json":
            continue
        raw = _load_json_file(path)
        if not isinstance(raw, dict):
            continue

        fallback = ""
        parsed_ids = _parse_filename_ids(path)
        if parsed_ids:
            fallback = parsed_ids[1]
            if "pluginId" not in raw and "plugin_id" not in raw and "bundleName" not in raw:
                raw = {**raw, "pluginId": parsed_ids[0]}
            if "toolName" not in raw and "tool_name" not in raw and "name" not in raw:
                raw = {**raw, "toolName": parsed_ids[1]}

        spec = _parse_spec_from_dict(raw, fallback_name=fallback)
        if spec is None:
            continue
        registry[(spec.plugin_id, spec.tool_name)] = spec

    logger.info(
        "[external_tool_registry] loaded %d tools from %s",
        len(registry),
        tools_dir,
    )
    return registry
