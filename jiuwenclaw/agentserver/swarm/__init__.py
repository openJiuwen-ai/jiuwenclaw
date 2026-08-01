# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarm provider-based team assembly."""

from __future__ import annotations

from jiuwenclaw.agentserver.swarm.assembly import enrich_team_spec_for_swarm
from jiuwenclaw.agentserver.swarm.context import SwarmBuildContext
from jiuwenclaw.agentserver.swarm.registry import register_swarm_providers

register_swarm_providers()

__all__ = [
    "enrich_team_spec_for_swarm",
    "SwarmBuildContext",
    "register_swarm_providers",
]
