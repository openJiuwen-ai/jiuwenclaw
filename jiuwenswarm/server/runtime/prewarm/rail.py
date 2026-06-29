# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""PrewarmRail — bridges KV-cache prewarming into the ReAct model-call lifecycle.

Registered on the DeepAgent, bridged to the inner ReActAgent for
BEFORE_MODEL_CALL / AFTER_MODEL_CALL. Uses the existing
InferenceAffinityModelClient's param builder so the prewarm body shares
the real request's token sequence (single construction path).

Scenarios:
  - A (before_model_call, first call only): prewarm static prefix
        messages + tools so the first real LLM call hits a warm cache.
  - B (after_model_call, response has tool_calls): prewarm
        messages + [response] so the next round (after tool dispatch)
        hits a warm cache for the shared prefix.
  - C (after_model_call, response has no tool_calls): prewarm
        messages + [response] for the next user turn.
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
            return getter()
        except Exception:  # noqa: BLE001
            return None
    return getattr(session, "session_id", None)


class PrewarmRail(AgentRail):
    """Rail that fires prewarm requests around model calls."""

    priority = 5  # run early so the prewarm body reflects pre-call state

    def __init__(self, coordinator: Optional[PrewarmCoordinator] = None):
        self._coordinator = coordinator or PrewarmCoordinator()
        self._scenario_a_fired: bool = False

    @property
    def coordinator(self) -> PrewarmCoordinator:
        return self._coordinator

    async def before_model_call(self, ctx: AgentCallbackContext) -> None:
        """Scenario A: on the first model call, prewarm the static prefix."""
        if not self._coordinator.config.enabled:
            return
        if not self._coordinator.config.scenario_a:
            return
        if self._scenario_a_fired:
            return
        self._scenario_a_fired = True

        client = _resolve_llm_client(ctx.agent)
        if client is None:
            return
        if not self._coordinator.is_supported_client(client):
            return

        inputs = ctx.inputs
        messages = list(getattr(inputs, "messages", []) or [])
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
            scenario="A",
        )

    async def after_model_call(self, ctx: AgentCallbackContext) -> None:
        """Scenarios B/C: prewarm messages + [response] after the LLM responds."""
        if not self._coordinator.config.enabled:
            return
        if not self._coordinator.config.scenario_bc:
            return

        client = _resolve_llm_client(ctx.agent)
        if client is None:
            return
        if not self._coordinator.is_supported_client(client):
            return

        inputs = ctx.inputs
        base_messages: List[Any] = list(getattr(inputs, "messages", []) or [])
        response = getattr(inputs, "response", None)
        if response is None:
            return
        messages = base_messages + [response]
        tools = getattr(inputs, "tools", None)
        model_name = _resolve_model_name(ctx.agent)
        session_id = _resolve_session_id(ctx)
        enable_sharing = _resolve_enable_cache_sharing(ctx.agent)

        has_tool_calls = bool(getattr(response, "tool_calls", None))
        scenario = "B" if has_tool_calls else "C"

        await self._coordinator.prewarm(
            client,
            messages=messages,
            tools=tools,
            model_name=model_name,
            session_id=session_id,
            enable_cache_sharing=enable_sharing,
            scenario=scenario,
        )

    async def after_invoke(self, ctx: AgentCallbackContext) -> None:
        """Best-effort: let scenario-C prewarms finish before the session closes."""
        if not self._coordinator.config.enabled:
            return
        session_id = _resolve_session_id(ctx)
        try:
            await self._coordinator.await_pending(session_id)
        except Exception:  # noqa: BLE001
            pass
