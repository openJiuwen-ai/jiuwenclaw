"""LLM resolution helpers for the prompt optimizer.

Thin wrappers over :mod:`jiuwenswarm.symphony.llm` so every optimizer role
(policy / environment / judge) resolves an :class:`LLMConfig` the same way and
respects per-role model overrides from ``symphony.optimization.models``.
"""

from __future__ import annotations

from dataclasses import replace

from jiuwenswarm.symphony.llm import LLMConfig, create_llm_client


def resolve_llm_config(model_name: str = "", *, temperature: float | None = None) -> LLMConfig:
    """Resolve an :class:`LLMConfig`, optionally overriding model and temperature.

    ``model_name`` empty -> the JiuwenSwarm default model. When provided, only the
    model identifier is swapped; api_base / api_key / provider stay the default's,
    which matches how a single gateway serves multiple model names.
    """

    config = LLMConfig.from_default_model()
    name = str(model_name or "").strip()
    if name:
        request_config = dict(config.model_config_obj or {})
        request_config["model"] = name
        config = replace(config, model=name, model_config_obj=request_config)
    if temperature is not None:
        config = replace(config, temperature=float(temperature))
    return config


def build_client(model_name: str = "", *, temperature: float | None = None):
    """Convenience: resolve a config and return a ready ``JiuwenSwarmChatClient``."""

    return create_llm_client(resolve_llm_config(model_name, temperature=temperature))


__all__ = ["resolve_llm_config", "build_client"]
