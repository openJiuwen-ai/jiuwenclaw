# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Per-session tool id qualification for Runner.resource_mgr isolation."""

from __future__ import annotations

import logging
from typing import Any, Iterable

from openjiuwen.core.foundation.tool import LocalFunction, ToolCard
from openjiuwen.core.runner import Runner

logger = logging.getLogger(__name__)


def log_session_tool(
    agent_card_id: str,
    tool_name: str,
    tool_id: str,
    *,
    event: str,
    base_id: str | None = None,
) -> None:
    if base_id and base_id != tool_id:
        logger.info(
            "[session_tool] %s name=%s base_id=%s tool_id=%s agent_card_id=%s",
            event,
            tool_name,
            base_id,
            tool_id,
            agent_card_id,
        )
    else:
        logger.info(
            "[session_tool] %s name=%s tool_id=%s agent_card_id=%s",
            event,
            tool_name,
            tool_id,
            agent_card_id,
        )


def qualify_tool_id(base_id: str, agent_card_id: str) -> str:
    """Return a session-scoped tool resource id."""
    base = str(base_id or "").strip()
    agent = str(agent_card_id or "").strip()
    if not base:
        raise ValueError("base_id is required for tool qualification")
    if not agent:
        raise ValueError("agent_card_id is required for tool qualification")
    suffix = f"_{agent}"
    if base.endswith(suffix):
        return base
    return f"{base}{suffix}"


def _sync_tool_card_to_ability_manager(agent: Any, tool: Any) -> None:
    card = getattr(tool, "card", None)
    if card is None:
        return
    ability_manager = getattr(agent, "ability_manager", None)
    if ability_manager is None:
        return
    tool_name = str(getattr(card, "name", "") or "")
    existing = ability_manager.get(tool_name)
    if existing is not None:
        ability_manager.remove(tool_name)
    ability_manager.add(card)


def _clone_tool_card(card: ToolCard, qualified_id: str) -> ToolCard:
    if hasattr(card, "model_copy"):
        cloned = card.model_copy(deep=True)
        cloned.id = qualified_id
        return cloned
    return ToolCard(
        id=qualified_id,
        name=card.name,
        description=getattr(card, "description", "") or "",
        input_params=getattr(card, "input_params", None) or {"type": "object"},
    )


def clone_tool_for_session(source_tool: Any, agent_card_id: str) -> Any:
    """Clone a tool with a session-qualified card.id; invoke delegates to source."""
    card = getattr(source_tool, "card", None)
    if card is None:
        raise TypeError(f"tool has no card: {source_tool!r}")
    base_id = str(getattr(card, "id", None) or card.name)
    qualified_id = qualify_tool_id(base_id, agent_card_id)
    new_card = _clone_tool_card(card, qualified_id)

    source_func = getattr(source_tool, "func", None)
    source_invoke = getattr(source_tool, "invoke", None)
    if not callable(source_func) and not callable(source_invoke):
        raise TypeError(f"unsupported tool type for session clone: {type(source_tool)!r}")

    async def _invoke(**kwargs: Any) -> Any:
        logger.info(
            "[session_tool] invoke name=%s tool_id=%s agent_card_id=%s",
            new_card.name,
            qualified_id,
            agent_card_id,
        )
        if callable(source_func):
            result = source_func(**kwargs)
            if hasattr(result, "__await__"):
                return await result
            return result
        return await source_invoke(kwargs)

    cloned = LocalFunction(card=new_card, func=_invoke)

    log_session_tool(
        agent_card_id,
        new_card.name,
        qualified_id,
        event="cloned",
        base_id=base_id,
    )
    return cloned


def qualify_tool_instance(tool: Any, agent_card_id: str) -> str:
    """Mutate tool.card.id to a qualified id; return the new id."""
    card = getattr(tool, "card", None)
    if card is None:
        raise TypeError(f"tool has no card: {tool!r}")
    base_id = str(getattr(card, "id", None) or card.name)
    qualified_id = qualify_tool_id(base_id, agent_card_id)
    card.id = qualified_id
    return qualified_id


def remove_tool_from_resource_mgr(tool_id: str) -> None:
    try:
        if Runner.resource_mgr.get_tool(tool_id) is not None:
            Runner.resource_mgr.remove_tool(tool_id)
    except Exception as exc:
        logger.debug("[tool_qualify] remove_tool(%s) skipped: %s", tool_id, exc)


def add_tool_to_resource_mgr(tool: Any) -> None:
    """Register tool in Runner.resource_mgr (compatible with openjiuwen without refresh kwarg)."""
    result = Runner.resource_mgr.add_tool(tool)
    is_err = getattr(result, "is_err", None)
    if callable(is_err) and is_err():
        msg = getattr(result, "msg", None) or str(getattr(result, "error", lambda: result)())
        raise RuntimeError(str(msg))


def reregister_qualified_tool_in_resource_mgr(tool: Any, agent_card_id: str) -> tuple[str, str]:
    """Remove stale ids, qualify card.id, and register tool. Returns (base_id, qualified_id)."""
    card = getattr(tool, "card", None)
    if card is None:
        raise TypeError(f"tool has no card: {tool!r}")

    pre_id = str(getattr(card, "id", None) or card.name)
    suffix = f"_{agent_card_id}"
    base_id = pre_id[: -len(suffix)] if pre_id.endswith(suffix) else pre_id
    qualified_id = qualify_tool_id(base_id, agent_card_id)

    for tool_id in {pre_id, base_id, qualified_id}:
        remove_tool_from_resource_mgr(tool_id)

    qualify_tool_instance(tool, agent_card_id)
    add_tool_to_resource_mgr(tool)
    return base_id, qualified_id


def register_qualified_tool(
    agent: Any,
    tool: Any,
    agent_card_id: str,
) -> ToolCard:
    """Qualify, register in resource_mgr, and sync ability_manager card."""
    card = getattr(tool, "card", None)
    if card is None:
        raise TypeError(f"tool has no card: {tool!r}")

    tool_name = str(getattr(card, "name", "") or "")
    base_id, qualified_id = reregister_qualified_tool_in_resource_mgr(tool, agent_card_id)
    _sync_tool_card_to_ability_manager(agent, tool)
    log_session_tool(
        agent_card_id,
        tool_name,
        qualified_id,
        event="registered",
        base_id=base_id if base_id != qualified_id else None,
    )
    return card


def register_qualified_tools(
    agent: Any,
    tools: Iterable[Any],
    agent_card_id: str,
) -> list[Any]:
    """Qualify and register a batch of tools; return the same instances."""
    registered: list[Any] = []
    for tool in tools:
        register_qualified_tool(agent, tool, agent_card_id)
        registered.append(tool)
    return registered


__all__ = [
    "add_tool_to_resource_mgr",
    "clone_tool_for_session",
    "log_session_tool",
    "qualify_tool_id",
    "qualify_tool_instance",
    "register_qualified_tool",
    "register_qualified_tools",
    "remove_tool_from_resource_mgr",
    "reregister_qualified_tool_in_resource_mgr",
]
