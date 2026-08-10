"""Bridge jiuwenswarm config -> search agent ``AgentConfig``.

Reads the dedicated search model from ``config.models.search`` and the
search-agent tuning knobs from ``config.react.subagents.search_agent``, and
builds an :class:`AgentConfig` whose ``model_name``/``api_key``/``base_url``
point at the dedicated search model (independent of the main agent's model).

Returns ``None`` when no dedicated search model is configured, so callers can
skip mounting the SearchAgent tool entirely.
"""

from __future__ import annotations

import logging
from typing import Any

from jiuwenswarm.agents.harness.search.agent.nlp_react_agent import AgentConfig

# Default prompt templates shipped in prompts.py (see SYSTEM_TEMPLATES / QUERY_TEMPLATES).
_DEFAULT_SYSTEM_PROMPT_NAME = "SYSTEM_TEMPLATE_XIAOHAN0319"
_DEFAULT_QUERY_PROMPT_NAME = "QUERY_TEMPLATE"
_DEFAULT_TOOL_NAMES = [
    "web_search",
    "web_fetch_and_summary",
    "check_confidence_gate",
]
_DEFAULT_MAX_ITERATIONS = 15
_DEFAULT_MAX_CONTEXT_TOKENS = 262144


def build_search_agent_config_from_jiuwenswarm(
    config: dict[str, Any],
    logger: logging.Logger | None = None,
) -> AgentConfig | None:
    """Build an :class:`AgentConfig` for the SearchAgent from jiuwenswarm config.

    Reads:
      - ``config["models"]["search"]["model_client_config"]`` for the dedicated
        search model (``api_base`` / ``api_key`` / ``model_name``).
      - ``config["react"]["subagents"]["search_agent"]`` for tuning knobs:
        ``system_prompt_name`` / ``query_prompt_name`` / ``tool_names`` /
        ``max_iterations`` / ``max_context_tokens`` / ``temperature``.

    Returns ``None`` when ``models.search`` is absent or lacks a model_name,
    so the caller can treat "no dedicated search model" as "do not mount".
    """
    log = logger or logging.getLogger(__name__)

    models = config.get("models") if isinstance(config, dict) else None
    search_model = models.get("search") if isinstance(models, dict) else None
    if not isinstance(search_model, dict):
        return None
    mcc = search_model.get("model_client_config") or {}
    model_name = mcc.get("model_name")
    if not model_name:
        log.warning("[search_agent] models.search.model_client_config.model_name missing; skipping SearchAgent")
        return None

    react = config.get("react") if isinstance(config, dict) else None
    subagents_cfg = react.get("subagents") if isinstance(react, dict) else None
    sa_cfg = subagents_cfg.get("search_agent") if isinstance(subagents_cfg, dict) else None
    if not isinstance(sa_cfg, dict):
        sa_cfg = {}

    model_config_obj = search_model.get("model_config_obj") or {}
    temperature = model_config_obj.get("temperature")

    return AgentConfig(
        model_name=model_name,
        api_key=mcc.get("api_key", ""),
        base_url=mcc.get("api_base", ""),
        system_prompt_name=sa_cfg.get("system_prompt_name", _DEFAULT_SYSTEM_PROMPT_NAME),
        query_prompt_name=sa_cfg.get("query_prompt_name", _DEFAULT_QUERY_PROMPT_NAME),
        tool_names=sa_cfg.get("tool_names") or list(_DEFAULT_TOOL_NAMES),
        max_iterations=int(sa_cfg.get("max_iterations", _DEFAULT_MAX_ITERATIONS)),
        max_context_tokens=int(sa_cfg.get("max_context_tokens", _DEFAULT_MAX_CONTEXT_TOKENS)),
        temperature=temperature,
        # No hard per-call timeout for the subagent's LLM calls (mirror NLPRunner);
        # the OpenAIClient retry/semaphore still bounds runaway calls.
        timeout=None,
        # Avoid the hardcoded tokenizer path from the upstream default; let
        # ContextManager degrade to char estimation when no tokenizer is loadable.
        tokenizer_name=None,
    )


__all__ = ["build_search_agent_config_from_jiuwenswarm"]
