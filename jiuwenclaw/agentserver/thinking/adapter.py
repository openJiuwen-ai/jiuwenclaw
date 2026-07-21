# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Adapt semantic thinking (default|off|on) to frozen vendor kwargs."""

from __future__ import annotations

from typing import Any

from jiuwenclaw.agentserver.thinking.types import (
    ThinkingProfile,
    freeze_llm_call_kwargs,
    normalize_thinking,
)
from jiuwenclaw.agentserver.thinking.vendor_map import match_vendor_style, style_to_kwargs
from jiuwenclaw.utils import logger


def _resolve_model_name(model: Any) -> str:
    """Best-effort model name from openjiuwen Model / config objects."""
    if model is None:
        return ""
    if isinstance(model, str):
        return model.strip()
    cfg = getattr(model, "model_config", None)
    if cfg is not None:
        for attr in ("model_name", "model"):
            val = getattr(cfg, attr, None)
            if isinstance(val, str) and val.strip():
                return val.strip()
    client_cfg = getattr(model, "model_client_config", None)
    if client_cfg is not None:
        val = getattr(client_cfg, "model_name", None)
        if isinstance(val, str) and val.strip():
            return val.strip()
    return ""


def adapt_thinking(thinking: str | None, model: Any = None, *, model_name: str = "") -> ThinkingProfile:
    """Build a frozen ThinkingProfile for one subagent lifetime.

    Never raises: unsupported / invalid → empty kwargs + degraded flag.
    Nested kwargs are deep-copied before freeze so later inject mutations
    cannot write through into the profile.
    """
    name = (model_name or "").strip() or _resolve_model_name(model)
    try:
        level, invalid = normalize_thinking(thinking)
        if invalid:
            return ThinkingProfile.empty(
                thinking="default",
                degraded=True,
                reason="invalid_value",
                model_name=name,
            )
        if level == "default":
            return ThinkingProfile.empty(thinking="default", model_name=name)

        style = match_vendor_style(name)
        if style is None:
            logger.info(
                "[Thinking] unsupported model for thinking toggle model=%r thinking=%s",
                name,
                level,
            )
            return ThinkingProfile.empty(
                thinking=level,
                degraded=True,
                reason="unsupported_model",
                model_name=name,
            )

        kwargs = style_to_kwargs(style, enabled=(level == "on"))
        return ThinkingProfile(
            thinking=level,
            llm_call_kwargs=freeze_llm_call_kwargs(kwargs),
            injected=True,
            degraded=False,
            reason=None,
            vendor_style=style,
            model_name=name,
        )
    except Exception as exc:
        logger.warning("[Thinking] adapt failed: %s", exc)
        return ThinkingProfile.empty(
            thinking="default",
            degraded=True,
            reason="adapter_error",
            model_name=name,
        )
