# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Lazy per-message tenant path helpers for Gateway channels (方案 A)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from jiuwenclaw.utils import (
    get_multi_tenant_user_workspace_dir,
    normalize_tenant_scope_id,
)


def normalize_channel_tenant_ids(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> tuple[str, str]:
    """Always return a concrete ``(service_id, agent_id)`` pair (default/default)."""
    return normalize_tenant_scope_id(service_id), normalize_tenant_scope_id(agent_id)


def tenant_ids_from_im_identity(
    chat_id: str,
    bot_id: str,
    user_id: str = "",
) -> tuple[str, str]:
    """Derive tenant ids the same way SessionMap does for IM traffic."""
    from jiuwenclaw.gateway.session_map import (
        invoke_ids_from_identity,
        load_session_map_scope,
    )

    sid, aid = invoke_ids_from_identity(
        str(chat_id or "").strip(),
        str(bot_id or "").strip(),
        str(user_id or "").strip(),
        load_session_map_scope(),
    )
    return normalize_channel_tenant_ids(sid, aid)


def tenant_ids_from_message(msg: Any) -> tuple[str, str]:
    """Prefer ``msg.params`` sid/aid; else parse SessionMap ``session_id``; else default."""
    params = getattr(msg, "params", None)
    if isinstance(params, dict) and (
        params.get("service_id") is not None or params.get("agent_id") is not None
    ):
        return normalize_channel_tenant_ids(
            params.get("service_id"),
            params.get("agent_id"),
        )
    session_id = str(getattr(msg, "session_id", None) or "").strip()
    if session_id and "::" in session_id:
        from jiuwenclaw.gateway.session_map import invoke_ids_from_session_id_string

        sid, aid = invoke_ids_from_session_id_string(session_id)
        return normalize_channel_tenant_ids(sid, aid)
    return "default", "default"


def resolve_channel_agent_workspace(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> Path:
    """``service_{sid}/agent_{aid}/agent/jiuwenclaw_workspace``."""
    sid, aid = normalize_channel_tenant_ids(service_id, agent_id)
    base = get_multi_tenant_user_workspace_dir(sid, aid)
    if base is None:
        base = get_multi_tenant_user_workspace_dir("default", "default")
    if base is None:
        raise RuntimeError(
            "failed to resolve multi-tenant workspace for channel "
            f"(service_id={sid!r}, agent_id={aid!r})"
        )
    return base / "agent" / "jiuwenclaw_workspace"


def resolve_channel_group_chat_memory_dir(
    service_id: str | None = None,
    agent_id: str | None = None,
) -> Path:
    """Per-tenant group chat memory root under agent workspace."""
    return resolve_channel_agent_workspace(service_id, agent_id) / "memory" / "group_chat"
