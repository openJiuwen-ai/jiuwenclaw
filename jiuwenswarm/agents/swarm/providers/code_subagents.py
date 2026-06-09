# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Config-sourced code sub-agent provider for swarm team assembly.

Declares the swarm-specific ``code`` sub-agent as a ``swarm.*`` sub-agent
provider resolved via ``SubAgentSpec.factory_name`` during ``spec.build()``. The
generic explore / plan / browser sub-agents are provided by openjiuwen
(``explore_agent`` / ``plan_agent`` / ``browser_agent``); only ``code_agent``
stays swarm-side because it reuses the swarm ``CodingMemoryRail`` instance.

The parent member model and the main agent's ``CodingMemoryRail`` are read from
``ctx.extras`` (published by ``DeepAgentSpec.build`` and the
``swarm.code_coding_memory`` rail provider respectively, both before sub-agents
build under the same build context). ``build_code_agent_config`` requires a
model, so it skips when none is present.
"""

from __future__ import annotations

import logging
from typing import Any

from openjiuwen.agent_teams.harness.manifest import (
    ConstructionInput,
    context_field,
    ElementKind,
    harness_element,
    param_field,
)
from openjiuwen.harness.subagents.code_agent import build_code_agent_config

from jiuwenswarm.agents.swarm.context import SwarmBuildContext
from jiuwenswarm.agents.swarm.providers.code_rails import (
    code_runtime_language,
    CODING_MEMORY_EXTRAS_KEY,
)

logger = logging.getLogger(__name__)

CODE_AGENT = "swarm.code_agent"

# Key under ``ctx.extras`` where ``DeepAgentSpec.build`` publishes the resolved
# parent member model for sub-agent providers to reuse.
_PARENT_MODEL_EXTRAS_KEY = "_parent_model"
_DEFAULT_MAX_ITERATIONS = 15


def _workspace_root(ctx: SwarmBuildContext) -> str | None:
    """Resolve the member workspace root path."""
    return getattr(ctx.workspace, "root_path", None) if ctx.workspace else None


class CodeAgentInput(ConstructionInput):
    """Construction inputs for the swarm code sub-agent."""

    max_iterations: int = param_field(
        default=_DEFAULT_MAX_ITERATIONS,
        description="Maximum task-loop iterations for the sub-agent.",
    )
    workspace_root: str | None = context_field(
        resolver=_workspace_root,
        description="Member workspace root (defaults to ./ when absent).",
    )
    language: str = context_field(
        resolver=code_runtime_language,
        default="en",
        description="Code runtime language for the sub-agent.",
    )


@harness_element(
    kind=ElementKind.SUBAGENT,
    name=CODE_AGENT,
    description="Code execution sub-agent reusing the main agent's CodingMemoryRail; "
    "skipped when no parent model is available.",
    input_model=CodeAgentInput,
)
def build_code_agent(factory_kwargs: dict[str, Any], ctx: SwarmBuildContext) -> Any:
    """Build the code sub-agent, reusing the main agent's CodingMemoryRail.

    ``build_code_agent_config`` requires a model; returns None (skipped) when no
    parent model is available on the context.
    """
    inp = CodeAgentInput.resolve(factory_kwargs, ctx)
    model = ctx.extras.get(_PARENT_MODEL_EXTRAS_KEY)
    if model is None:
        logger.warning("[swarm.code_agent] skipped: no parent model on build context")
        return None
    rails = None
    coding_memory_rail = ctx.extras.get(CODING_MEMORY_EXTRAS_KEY)
    if coding_memory_rail is not None:
        # SysOperationRail is code_agent's default rail; passing rails overrides
        # the defaults, so it must be included explicitly alongside the shared
        # CodingMemoryRail.
        from openjiuwen.harness.rails import SysOperationRail

        rails = [SysOperationRail(), coding_memory_rail]
    spec = build_code_agent_config(
        model,
        rails=rails,
        workspace=str(inp.workspace_root or "./"),
        language=inp.language,
        max_iterations=inp.max_iterations,
    )
    spec.factory_kwargs = {"auto_create_workspace": False}
    return spec


__all__ = [
    "CODE_AGENT",
]
