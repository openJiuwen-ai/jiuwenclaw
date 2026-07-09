# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PrewarmRail — bridges KV-cache prewarming into the ReAct model-call lifecycle.

Registered on the DeepAgent, bridged to the inner ReActAgent for
AFTER_MODEL_CALL. Uses the existing InferenceAffinityModelClient's
param builder so the prewarm body shares the real request's token
sequence (single construction path).

Scenario:
  - B (after_model_call, response has tool_calls): prewarm
        messages + [response] so the next round (after tool dispatch)
        hits a warm cache for the shared prefix.

Prewarm is fire-and-forget: the background HTTP task is not tracked, so
a session closing mid-flight simply drops the in-progress prewarm.
"""
from __future__ import annotations

from typing import Any, List, Optional

from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
)

from jiuwenswarm.server.runtime.prewarm.coordinator import PrewarmCoordinator

try:
    from openjiuwen.core.common.logging import logger
except Exception:  # pragma: no cover
    import logging
    logger = logging.getLogger("prewarm")


def _resolve_llm_client(agent: Any) -> Any:
    """Walk agent -> Model -> _client, returning the underlying ModelClient or None."""
    get_llm = getattr(agent, "_get_llm", None)
    if not callable(get_llm):
        return None
    try:
        model = get_llm()
    except Exception:  # noqa: BLE001
        return None
    client = getattr(model, "_client", None)
    return client


def _resolve_model_name(agent: Any) -> Optional[str]:
    config = getattr(agent, "_config", None)
    if config is None:
        return None
    return getattr(config, "model_name", None) or None


def _resolve_enable_cache_sharing(agent: Any) -> bool:
    """Match the real request's cache_sharing flag.

    When ContextEngineConfig.enable_kv_cache_release is True, real requests
    carry enable_cache_sharing=True with cache_salt=session_id; the prewarm
    must use the same salt so both share the vLLM cache namespace. Otherwise
    fall back to token-sequence matching (enable_cache_sharing=False).
    """
    config = getattr(agent, "_config", None)
    if config is None:
        return False
    ce = getattr(config, "context_engine_config", None)
    if ce is None:
        return False
    return bool(getattr(ce, "enable_kv_cache_release", False))

def _resolve_session_id(ctx: AgentCallbackContext) -> Optional[str]:
    session = getattr(ctx, "session", None)
    if session is None:
        return None
    getter = getattr(session, "get_session_id", None)
    if callable(getter):
        try:
            return getter() # type: ignore
        except Exception:  # noqa: BLE001
            return None
    return getattr(session, "session_id", None)


_SYSTEM_REMINDER_PREFIX = "<system-reminder>"

def _is_system_reminder_msg(message: Any) -> bool:
    """Detect a system-reminder message (used to skip prewarm)."""
    content = getattr(message, "content", "") if not isinstance(message, dict) else message.get("content", "")
    if isinstance(content, str) and content.startswith(_SYSTEM_REMINDER_PREFIX):
        return True
    if isinstance(content, list):
        for part in content:
            text = part.get("text") if isinstance(part, dict) else ""
            if isinstance(text, str) and text.startswith(_SYSTEM_REMINDER_PREFIX):
                return True
    return False

def _strip_last_system_reminder(messages: List[Any]) -> List[Any]:
    """Remove the last system-reminder message from a list of messages."""
    if not messages:
        return messages
    last = messages[-1]
    role = getattr(last, "role", "") if not isinstance(last, dict) else last.get("role", "")
    if role == "user" and _is_system_reminder_msg(last):
        return messages[:-1]
    return messages


class PrewarmRail(AgentRail):
    """Rail that fires prewarm requests after model calls."""

    priority = 5  # run early so the prewarm body reflects pre-call state

    def __init__(self, coordinator: Optional[PrewarmCoordinator] = None):
        self._coordinator = coordinator or PrewarmCoordinator()

    @property
    def coordinator(self) -> PrewarmCoordinator:
        return self._coordinator

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Scenarios B/C: prewarm messages + [response] after the LLM responds."""
        if not self._coordinator.config.enabled:
            return
        if not self._coordinator.config.scenario_b:
            return

        client = _resolve_llm_client(ctx.agent)
        if client is None:
            return
        if not self._coordinator.is_supported_client(client):
            return

        inputs = ctx.inputs
        response = getattr(inputs, "response", None)
        if response is None:
            logger.debug("[PrewarmRail] after_model_call no response, skipping prewarm")
            return
        
        has_tool_calls = bool(getattr(response, "tool_calls", None))
        if not has_tool_calls:
            logger.debug("[PrewarmRail] after_model_call no tool_calls, skipping scenario-B prewarm")
            return
        
        base_messages: List[Any] = list(getattr(inputs, "messages", []) or [])
        messages = base_messages + [response]
        tools = getattr(inputs, "tools", None)
        model_name = _resolve_model_name(ctx.agent)
        session_id = _resolve_session_id(ctx)
        enable_sharing = _resolve_enable_cache_sharing(ctx.agent)

        await self._coordinator.prewarm(
            client,
            messages=messages,
            tools=tools,
            model_name=model_name,
            session_id=session_id,
            enable_cache_sharing=enable_sharing,
            scenario="B",
        )
