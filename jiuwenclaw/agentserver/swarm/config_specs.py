# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Config-sourced capability specs for swarm team members (minimal profile)."""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.agent_teams.schema.deep_agent_spec import (
    BuiltinToolSpec,
    DeepAgentSpec,
    RailSpec,
    SubAgentSpec,
)
from openjiuwen.core.foundation.kv_cache import KVCacheAffinityConfig
from openjiuwen.core.foundation.tool import McpServerConfig
from openjiuwen.core.single_agent.schema.agent_card import AgentCard

from jiuwenclaw.agentserver.swarm.providers import catalog_tools as _catalog_tools
from jiuwenclaw.agentserver.swarm.registry import (
    CORE_AUDIO,
    CORE_VISION,
    JIUWEN_WEB_FETCH,
    JIUWEN_WEB_SEARCH,
    PLATFORM_CATALOG_TOOLS,
    PLATFORM_MEMBER_RAILS,
    SWARM_BROWSER_AGENT,
)
from jiuwenclaw.agentserver.utils import DEFAULT_ENABLE_READ_IMAGE_MULTIMODAL

logger = logging.getLogger(__name__)

_CODE_MODES: frozenset[str] = frozenset({"code.team", "team.plan"})
_DEFAULT_SUBAGENT_MAX_ITERATIONS = 15


def _is_code_mode(mode: str) -> bool:
    return mode in _CODE_MODES


def _is_subagent_enabled(sub_cfg: Any) -> bool:
    """Return whether a ``react.subagents.<name>`` entry is enabled."""
    return isinstance(sub_cfg, dict) and bool(sub_cfg.get("enabled", False))


def _kv_cache_affinity_config(config: dict[str, Any]) -> KVCacheAffinityConfig:
    react = config.get("react")
    react = react if isinstance(react, dict) else {}
    raw = react.get("kv_cache_affinity_config")
    raw = raw if isinstance(raw, dict) else {}
    return KVCacheAffinityConfig(
        enable_kv_cache_release=bool(raw.get("enable_kv_cache_release", False)),
        enable_kv_cache_affinity=False,
    )


def _subagent_language(config: dict[str, Any]) -> str:
    """Resolve language for swarm sub-agents (ENT default cn)."""
    preferred = str((config or {}).get("preferred_language") or "").strip().lower()
    if preferred in {"en", "english"}:
        return "en"
    if preferred in {"zh", "cn", "zh-cn", "zh_cn", "chinese"}:
        return "cn"
    return "cn"


def _browser_subagent_spec(react_cfg: dict[str, Any], language: str) -> SubAgentSpec:
    """Build declarative ``SubAgentSpec`` for ``swarm.browser_agent``."""
    subagents_cfg = react_cfg.get("subagents", {}) if isinstance(react_cfg, dict) else {}
    sub_cfg = subagents_cfg.get("browser_agent") if isinstance(subagents_cfg, dict) else None
    max_iterations = react_cfg.get("max_iterations", _DEFAULT_SUBAGENT_MAX_ITERATIONS)
    if isinstance(sub_cfg, dict) and sub_cfg.get("max_iterations"):
        max_iterations = sub_cfg["max_iterations"]
    return SubAgentSpec(
        agent_card=AgentCard(name="browser_agent"),
        system_prompt="",
        factory_name=SWARM_BROWSER_AGENT,
        factory_kwargs={
            "max_iterations": int(max_iterations),
            "language": language,
        },
    )


def _build_catalog_tool_specs(config: dict[str, Any]) -> list[BuiltinToolSpec]:
    """Declare plan-parity catalog tools so team members can invoke them.

    Web search uses jiuwenclaw's ``web_search`` (paid chain includes petal),
    replacing openjiuwen's ``core.web_search`` / ``core.web_paid_search`` which
    lack petal support. Vision / audio only when dedicated model config exists.
    ENT extras via ``swarm.platform_catalog_tools``.
    """
    tool_specs: list[BuiltinToolSpec] = [
        BuiltinToolSpec(type=JIUWEN_WEB_SEARCH),
        BuiltinToolSpec(type=JIUWEN_WEB_FETCH),
    ]

    vision_params = _catalog_tools.vision_tool_params(config)
    if vision_params.get("vision_model_config"):
        tool_specs.append(BuiltinToolSpec(type=CORE_VISION, params=vision_params))

    audio_params = _catalog_tools.audio_tool_params(config)
    if audio_params.get("dedicated"):
        tool_specs.append(BuiltinToolSpec(type=CORE_AUDIO, params=audio_params))

    tool_specs.append(
        BuiltinToolSpec(
            type=PLATFORM_CATALOG_TOOLS,
            params=_catalog_tools.platform_catalog_tool_params(config),
        )
    )
    return tool_specs


def build_member_capability_specs(
    config: dict[str, Any],
    mode: str,
    role: str,
    *,
    enable_permissions: bool = False,
    leader_member_name: str = "",
) -> tuple[list[RailSpec], list[BuiltinToolSpec]]:
    """Build platform rails + catalog tool specs for a team member.

    ``team.plan`` / ``code.team`` previously returned empty and dropped all
    platform rails/tools; they now share the same catalog capability set.
    """
    _ = role  # role-specific rails still come from PLATFORM_MEMBER_RAILS params
    params: dict[str, Any] = {"enable_permissions": enable_permissions}
    if leader_member_name:
        params["leader_member_name"] = leader_member_name
    rails = [RailSpec(type=PLATFORM_MEMBER_RAILS, params=params)]
    tool_specs = _build_catalog_tool_specs(config if isinstance(config, dict) else {})
    if _is_code_mode(mode):
        logger.info(
            "[swarm.config_specs] code-mode member still mounts platform rails+catalog tools "
            "mode=%s tools=%d",
            mode,
            len(tool_specs),
        )
    return rails, tool_specs


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

    # Team mode: replace any shared playwright browser_agent with per-member
    # swarm.browser_agent when react.subagents.browser_agent.enabled.
    if not _is_code_mode(mode):
        react_cfg = (config or {}).get("react", {})
        react_cfg = react_cfg if isinstance(react_cfg, dict) else {}
        subagents_cfg = react_cfg.get("subagents", {}) if isinstance(react_cfg, dict) else {}
        if isinstance(subagents_cfg, dict) and _is_subagent_enabled(subagents_cfg.get("browser_agent")):
            team_browser_spec = _browser_subagent_spec(react_cfg, _subagent_language(config))
            merged_subagents = []
            for s in list(base_spec.subagents or []):
                st = getattr(s, "subagent_type", None)
                name = getattr(getattr(s, "agent_card", None), "name", None)
                if st != "browser_agent" and name != "browser_agent":
                    merged_subagents.append(s)
            merged_subagents.append(team_browser_spec)
            update["subagents"] = merged_subagents
            logger.info(
                "[swarm.config_specs] mounted %s for role=%s",
                SWARM_BROWSER_AGENT,
                role,
            )

    return base_spec.model_copy(update=update)


__all__ = [
    "build_member_capability_specs",
    "build_member_deep_agent_spec",
]
