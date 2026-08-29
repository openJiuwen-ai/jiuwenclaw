# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo LLM thinking inject seam (allowlist + shotgun fallback)."""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


def resolve_skill_turbo_thinking_kwargs(
    thinking: str | None,
    model_client: Any,
) -> dict[str, Any]:
    """Optional thinking inject for SkillTurbo LLM calls.

    ``thinking is None`` → empty dict (no adapt, identical to legacy invoke/stream).
    Allowlisted models → vendor kwargs; ``off`` on others → shotgun fallback.
    Unsupported ``on`` / degraded → empty dict (never abort).
    """
    if thinking is None:
        return {}
    from jiuwenswarm.common.thinking.adapter import (
        adapt_thinking,
        thinking_disabled_invoke_kwargs,
    )
    from jiuwenswarm.common.thinking.types import (
        kwargs_digest,
        normalize_thinking,
        thaw_llm_call_kwargs,
    )

    level, _invalid = normalize_thinking(thinking)
    profile = adapt_thinking(thinking, model_client)
    if profile.injected and profile.llm_call_kwargs:
        kwargs = thaw_llm_call_kwargs(profile.llm_call_kwargs)
        logger.info(
            "[SkillTurboExecutor] thinking inject thinking=%s model=%r digest=%s",
            profile.thinking,
            profile.model_name,
            kwargs_digest(kwargs),
        )
        return kwargs

    if level == "off":
        logger.info(
            "[SkillTurboExecutor] thinking shotgun fallback thinking=off model=%r reason=%s",
            profile.model_name,
            profile.reason,
        )
        return thinking_disabled_invoke_kwargs()

    if profile.degraded:
        logger.info(
            "[SkillTurboExecutor] thinking not injected thinking=%s model=%r reason=%s",
            profile.thinking,
            profile.model_name,
            profile.reason,
        )
    return {}


def is_skill_turbo_thinking_param_error(exc: BaseException) -> bool:
    """Heuristic: API/client rejected thinking-related call kwargs."""
    if isinstance(exc, TypeError):
        return True
    msg = str(exc).lower()
    needles = (
        "extra_body",
        "thinking",
        "enable_thinking",
        "reasoning_effort",
        "unexpected keyword",
        "invalid_request",
        "bad request",
        "validation error",
        "unrecognized",
    )
    return any(n in msg for n in needles)
