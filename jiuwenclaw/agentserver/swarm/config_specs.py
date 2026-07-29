# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Config-sourced capability specs for swarm team members (minimal profile)."""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.agent_teams.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    RailSpec,
)
from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig
from openjiuwen.core.foundation.tool import McpServerConfig

from jiuwenclaw.agentserver.swarm.registry import PLATFORM_MEMBER_RAILS
from jiuwenclaw.agentserver.utils import DEFAULT_ENABLE_READ_IMAGE_MULTIMODAL

logger = logging.getLogger(__name__)

_CODE_MODES: frozenset[str] = frozenset({"code.team", "team.plan"})


def _is_code_mode(mode: str) -> bool:
    return mode in _CODE_MODES


def _kv_cache_affinity_config(config: dict[str, Any]) -> KVCacheAffinityConfig:
    react = config.get("react")
    react = react if isinstance(react, dict) else {}
    raw = react.get("kv_cache_affinity_config")
    raw = raw if isinstance(raw, dict) else {}
    return KVCacheAffinityConfig(
        enable_kv_cache_release=bool(raw.get("enable_kv_cache_release", False)),
        enable_kv_cache_affinity=False,
    )


def build_member_capability_specs(
    config: dict[str, Any],
    mode: str,
    role: str,
    *,
    enable_permissions: bool = False,
    leader_member_name: str = "",
) -> tuple[list[RailSpec], list[BuiltinToolSpec]]:
    """Build minimal rail/tool specs for a team member.

    Full tool/MCP/evolution providers are added incrementally; the enrich
    pipeline always declares platform member rails here.
    """
    if _is_code_mode(mode):
        return [], []

    params: dict[str, Any] = {"enable_permissions": enable_permissions}
    if leader_member_name:
        params["leader_member_name"] = leader_member_name
    rails = [RailSpec(type=PLATFORM_MEMBER_RAILS, params=params)]
    return rails, []


def build_member_deep_agent_spec(
    config: dict[str, Any],
    mode: str,
    role: str,
    base_spec: DeepAgentSpec,
    *,
    enable_permissions: bool = False,
    mcp_configs: list[McpServerConfig] | None = None,
    leader_member_name: str = "",
) -> DeepAgentSpec:
    """Fold member capability specs onto *base_spec*."""
    rails_specs, tool_specs = build_member_capability_specs(
        config,
        mode,
        role,
        enable_permissions=enable_permissions,
        leader_member_name=leader_member_name,
    )

    merged_rails = list(base_spec.rails or [])
    merged_rails.extend(rails_specs)
    merged_tools = list(base_spec.tools or [])
    merged_tools.extend(tool_specs)
    merged_mcps = list(base_spec.mcps or [])
    if mcp_configs:
        merged_mcps.extend(mcp_configs)

    update: dict[str, Any] = {
        "rails": merged_rails,
        "tools": merged_tools,
        "mcps": merged_mcps,
        "kv_cache_affinity_config": _kv_cache_affinity_config(config),
        "enable_read_image_multimodal": DEFAULT_ENABLE_READ_IMAGE_MULTIMODAL,
    }
    if role == "leader":
        update["enable_task_planning"] = False

    return base_spec.model_copy(update=update)


__all__ = [
    "build_member_capability_specs",
    "build_member_deep_agent_spec",
]
