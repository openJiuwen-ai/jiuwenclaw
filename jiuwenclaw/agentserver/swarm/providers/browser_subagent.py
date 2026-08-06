# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Swarm-side browser sub-agent provider for per-member browser isolation.

Only ``swarm.browser_agent`` lives here (``swarm.code_agent`` is intentionally
not migrated). The provider gives each swarm member its own isolated browser by
passing a unique ``browser_key`` (derived from the session id + member name) to
``build_browser_agent_config``. agent-core turns that key into a per-member
``BrowserInstanceConfig``: a suffixed MCP ``server_id`` (own ``@playwright/mcp``
subprocess), an auto-allocated debug port, and an own
``.browser-profiles/<key>`` user-data-dir under managed mode. Without a key,
every member shares ``Runner.resource_mgr``'s single ``playwright_official_stdio``
connection — one shared browser. We only need a swarm-side provider (rather than
the generic ``core.browser_agent``) because the key must be read from the
per-member build context, not a static param.

The parent member model is read from ``ctx.extras["_parent_model"]``
(published by ``DeepAgentSpec.build``). The provider skips when no model
is present.

Language is resolved from ``BuildContext.language`` (default ``cn``), never
hardcoded to ``en``.
"""

from __future__ import annotations

import inspect
import logging
from typing import Any

from openjiuwen.agent_teams.harness.manifest import (
    ConstructionInput,
    ElementKind,
    context_field,
    harness_element,
    param_field,
)
from openjiuwen.harness.subagents.browser_agent import build_browser_agent_config

from jiuwenclaw.agentserver.swarm.context import SwarmBuildContext

logger = logging.getLogger(__name__)

SWARM_BROWSER_AGENT = "swarm.browser_agent"

_PARENT_MODEL_EXTRAS_KEY = "_parent_model"
_DEFAULT_MAX_ITERATIONS = 15
# Capture at import time so monkeypatched fakes in tests do not hide the real API.
_SUPPORTS_BROWSER_KEY = "browser_key" in inspect.signature(build_browser_agent_config).parameters


def _workspace_root(ctx: SwarmBuildContext) -> str | None:
    """Resolve the member workspace root path."""
    return getattr(ctx.workspace, "root_path", None) if ctx.workspace else None


class BrowserAgentInput(ConstructionInput):
    """Construction inputs for the swarm browser sub-agent."""

    max_iterations: int = param_field(
        default=_DEFAULT_MAX_ITERATIONS,
        description="Maximum task-loop iterations for the sub-agent.",
    )
    workspace_root: str | None = context_field(
        resolver=_workspace_root,
        description="Member workspace root (defaults to ./ when absent).",
    )
    language: str = context_field(
        attr="language",
        default="cn",
        description="Runtime-prompt language for the sub-agent (cn/en; default cn).",
    )
    session_id: str = context_field(
        attr="session_id",
        default="",
        description="Active session id — combined with member_name to derive a unique browser_key.",
    )
    role: str = context_field(
        attr="role",
        default="",
        description="Member role ('leader'/'teammate') — only a coarse fallback discriminator.",
    )
    member_name: str = context_field(
        attr="member_name",
        default="",
        description=(
            "Unique member name — the real per-member browser discriminator. "
            "Every teammate shares role='teammate', so role alone collides; "
            "member_name (e.g. 'browser-usd-sgd') is distinct per spawned member."
        ),
    )


def _browser_key(session_id: str, member_name: str, role: str) -> str:
    """Return the per-member ``browser_key`` for ``build_browser_agent_config``.

    ``role`` is only ever 'leader'/'teammate', so it collides across teammates;
    ``member_name`` is unique per member (the leader/template specs carry only a
    role, so fall back to role when member_name is absent). The session id is
    folded in so members of different concurrent sessions that happen to share a
    member_name never collide onto one browser. agent-core sanitizes the key to
    id-safe chars; an empty key preserves legacy shared-browser behavior.
    """
    disc = (member_name or "").strip() or (role or "").strip()
    if not disc:
        return ""
    return f"{session_id}-{disc}" if session_id else disc


@harness_element(
    kind=ElementKind.SUBAGENT,
    name=SWARM_BROWSER_AGENT,
    description=(
        "Browser sub-agent with per-member browser isolation: each member passes a "
        "unique browser_key, so agent-core allocates a separate @playwright/mcp "
        "subprocess, debug port and user-data-dir (managed mode) per member."
    ),
    input_model=BrowserAgentInput,
)
def build_swarm_browser_agent(factory_kwargs: dict[str, Any], ctx: SwarmBuildContext) -> Any:
    """Build the browser sub-agent config with a per-member ``browser_key``."""
    inp = BrowserAgentInput.resolve(factory_kwargs, ctx)
    model = ctx.extras.get(_PARENT_MODEL_EXTRAS_KEY)
    if model is None:
        logger.warning("[swarm.browser_agent] skipped: no parent model on build context")
        return None

    browser_key = _browser_key(inp.session_id, inp.member_name, inp.role)

    build_kwargs: dict[str, Any] = {
        "workspace": str(inp.workspace_root or "./"),
        "language": inp.language,
        "max_iterations": inp.max_iterations,
    }
    if _SUPPORTS_BROWSER_KEY:
        build_kwargs["browser_key"] = browser_key
    elif browser_key:
        logger.warning(
            "[swarm.browser_agent] openjiuwen build_browser_agent_config has no browser_key; "
            "falling back to shared browser (wanted key=%r)",
            browser_key,
        )

    spec = build_browser_agent_config(model, **build_kwargs)
    spec.factory_kwargs = {**(spec.factory_kwargs or {}), "auto_create_workspace": False}
    logger.info(
        "[swarm.browser_agent] member_name=%r role=%r browser_key=%r language=%r",
        inp.member_name,
        inp.role,
        browser_key if _SUPPORTS_BROWSER_KEY else "",
        inp.language,
    )
    return spec


__all__ = [
    "SWARM_BROWSER_AGENT",
    "build_swarm_browser_agent",
    "_browser_key",
]
