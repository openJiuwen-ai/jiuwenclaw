# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""No-factory class-rail declarations for swarm-owned team rails.

These rails take no construction params (a bare ``cls()``), so they are declared
with a class ``builder`` rather than a factory function. Only swarm-owned rail
classes live here; the generic openjiuwen rails (``sys_operation`` /
``task_planning`` / ``security`` / ``heartbeat`` / ...) are declared and
registered by openjiuwen itself and referenced by their bare names from
``config_specs``.

Declarations run at import time (the module-level ``harness_element`` calls
populate the catalog). ``registry`` imports this module so the declarations are
present before ``register_from_catalog`` runs.
"""

from __future__ import annotations

from openjiuwen.agent_teams.harness.manifest import (
    ElementKind,
    harness_element,
)

from jiuwenswarm.agents.harness.common.rails.avatar_rail import AvatarPromptRail
from jiuwenswarm.agents.harness.common.rails.response_prompt_rail import (
    ResponsePromptRail,
)
from jiuwenswarm.agents.harness.common.rails.stream_event_rail import (
    JiuSwarmStreamEventRail,
)

# No-parameter swarm-owned rail type names; namespaced under "swarm.".
RESPONSE_PROMPT = "swarm.response_prompt"
STREAM_EVENT = "swarm.stream_event"
AVATAR_PROMPT = "swarm.avatar_prompt"

harness_element(
    kind=ElementKind.RAIL,
    name=RESPONSE_PROMPT,
    description="Appends the response-format prompt segment before each model call.",
    builder=ResponsePromptRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=STREAM_EVENT,
    description="Emits JiuSwarm streaming events across the member's lifecycle.",
    builder=JiuSwarmStreamEventRail,
)
harness_element(
    kind=ElementKind.RAIL,
    name=AVATAR_PROMPT,
    description="Injects per-request digital-avatar prompt sections.",
    builder=AvatarPromptRail,
)

__all__ = [
    "RESPONSE_PROMPT",
    "STREAM_EVENT",
    "AVATAR_PROMPT",
]
