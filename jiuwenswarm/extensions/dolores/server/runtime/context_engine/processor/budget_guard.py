# coding: utf-8
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Internal helpers for compression/offload token budget guards."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from openjiuwen.core.common.logging import logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.context_utils import ContextUtils
from openjiuwen.core.foundation.llm import BaseMessage

TRUNCATED_BY_BUDGET_MARKER = "... placeholder truncated; original content is preserved in offload storage ..."
TRUNCATED_SIDE_MAX_CHARS = 2000


def _positive_int(value: Any) -> int | None:
    if isinstance(value, int) and value > 0:
        return value
    return None


def _model_name(model_config: Any) -> str | None:
    if isinstance(model_config, str) and model_config:
        return model_config
    value = getattr(model_config, "model", None)
    if isinstance(value, str) and value:
        return value
    value = getattr(model_config, "model_name", None)
    if isinstance(value, str) and value:
        return value
    return None


def effective_context_budget(
        context: ModelContext,
        *,
        model_config: Any = None,
        call_budget: int | None = None,
) -> int:
    """Return the strictest positive context budget available."""
    candidates: list[int] = []

    configured = _positive_int(getattr(context, "_context_window_tokens", None))
    if configured is not None:
        candidates.append(configured)

    call_limit = _positive_int(call_budget)
    if call_limit is not None:
        candidates.append(call_limit)

    model_context_window_tokens = getattr(context, "_model_context_window_tokens", None)
    resolved_model_budget = ContextUtils.resolve_context_max(
        model_name=_model_name(model_config),
        fallback_context_window_tokens=None,
        model_context_window_tokens=(
            model_context_window_tokens
            if isinstance(model_context_window_tokens, dict)
            else None
        ),
    )
    model_budget = _positive_int(resolved_model_budget)
    if model_budget is not None:
        candidates.append(model_budget)

    return min(candidates) if candidates else resolved_model_budget


def count_messages_tokens(context: ModelContext, messages: list[BaseMessage]) -> int:
    """Count message tokens with tokenizer first, then char/3 fallback."""
    token_counter = context.token_counter()
    if token_counter is not None:
        try:
            return token_counter.count_messages(messages)
        except Exception as exc:  # pragma: no cover - defensive fallback
            logger.warning(f"[budget_guard] token_counter failed, fallback to estimate: {exc}")
    return sum(_estimate_text_tokens(getattr(message, "content", "")) for message in messages)


def _estimate_text_tokens(content: Any) -> int:
    if isinstance(content, str):
        return len(content) // 3
    return len(str(content)) // 3


def truncate_message_content_to_fixed_head_tail_if_over_token_limit(
        *,
        content: str,
        trigger_token_limit: int,
        count_content_tokens: Callable[[str], int],
        preserved_suffix: str = "",
) -> str:
    """Trim content to a fixed head/tail preview when it exceeds a token limit.

    ``trigger_token_limit`` only decides whether truncation is triggered. Once
    triggered, this helper keeps a fixed-size head/tail preview and preserves
    ``preserved_suffix``. The returned content is not guaranteed to fit within
    ``trigger_token_limit``.
    """
    if trigger_token_limit <= 0:
        return preserved_suffix or TRUNCATED_BY_BUDGET_MARKER
    if count_content_tokens(content) <= trigger_token_limit:
        return content

    body = content
    if preserved_suffix and body.endswith(preserved_suffix):
        body = body[:-len(preserved_suffix)].rstrip()

    return _build_head_tail(body, preserved_suffix)


def _build_head_tail(body: str, preserved_suffix: str) -> str:
    if not body:
        return _join_truncated_parts("", "", preserved_suffix)

    head_chars = min(TRUNCATED_SIDE_MAX_CHARS, max(len(body) // 2, 1))
    tail_chars = min(TRUNCATED_SIDE_MAX_CHARS, len(body) - head_chars)
    head = body[:head_chars]
    tail = body[-tail_chars:] if tail_chars else ""
    return _join_truncated_parts(head, tail, preserved_suffix)


def _join_truncated_parts(head: str, tail: str, preserved_suffix: str) -> str:
    parts = [part for part in (head, tail) if part]
    body = f"\n{TRUNCATED_BY_BUDGET_MARKER}\n".join(parts) if parts else TRUNCATED_BY_BUDGET_MARKER
    return f"{body}{preserved_suffix}"
