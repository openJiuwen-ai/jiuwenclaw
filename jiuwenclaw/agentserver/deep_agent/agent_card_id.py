# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Per-session AgentCard.id resolution for JiuWenClawDeepAdapter."""

from __future__ import annotations

import uuid

# Shared resource tag for Runner.resource_mgr tool/MCP registration.
# AgentCard.id uses a per-session suffix so outer rail callbacks stay isolated.
JIUWENCLAW_RESOURCE_AGENT_ID = "jiuwenclaw"
# AgentManager fallback when request.session_id is missing; needs per-instance uuid suffix.
DEFAULT_SESSION_ID = "default"


def is_default_session(session_id: str | None) -> bool:
    """Return True when session_id is empty or the Manager fallback literal."""
    raw = (session_id or "").strip()
    return not raw or raw == DEFAULT_SESSION_ID


def resolve_agent_card_id(
    session_id: str | None,
    *,
    cached_session_id: str | None,
    fallback_card_suffix: str | None,
) -> tuple[str, str | None]:
    """Return ``(agent_card_id, fallback_card_suffix)``.

    Allocates and returns a new fallback suffix when entering the uuid fallback path
    for the first time on an adapter instance.
    """
    raw_session = (session_id or cached_session_id or "").strip()

    if raw_session and not is_default_session(raw_session):
        return f"{JIUWENCLAW_RESOURCE_AGENT_ID}_{raw_session}", fallback_card_suffix

    suffix = fallback_card_suffix
    if suffix is None:
        suffix = uuid.uuid4().hex[:12]
    effective = f"{DEFAULT_SESSION_ID}_{suffix}"
    card_id = f"{JIUWENCLAW_RESOURCE_AGENT_ID}_{effective}"
    return card_id, suffix
