# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Central registration for swarm capability providers."""

from __future__ import annotations

from typing import Any

from openjiuwen.agent_teams.rails.registration import ensure_harness_elements_registered
from openjiuwen.agent_teams.schema.build_context import register_build_context_factory
from openjiuwen.harness.manifest import register_from_catalog

from jiuwenclaw.agentserver.swarm.context import SwarmBuildContext
from jiuwenclaw.agentserver.swarm.providers import member_rails as _member_rails
from jiuwenclaw.config import get_config

PLATFORM_MEMBER_RAILS = _member_rails.PLATFORM_MEMBER_RAILS

_REGISTERED = False


def _trajectory_registry_for(seed: dict[str, Any]) -> Any:
    """Placeholder trajectory registry; per-team registry not wired yet."""
    _ = seed
    return None


def _build_swarm_context_from_seed(seed: dict[str, Any]) -> SwarmBuildContext:
    return SwarmBuildContext.from_seed(
        seed,
        config=get_config(),
        trajectory_registry=_trajectory_registry_for(seed),
    )


def register_swarm_providers() -> None:
    """Register swarm providers with openjiuwen (idempotent)."""
    global _REGISTERED
    if _REGISTERED:
        return
    ensure_harness_elements_registered()
    register_from_catalog()
    register_build_context_factory(_build_swarm_context_from_seed)
    _REGISTERED = True


__all__ = [
    "register_swarm_providers",
    "PLATFORM_MEMBER_RAILS",
]
