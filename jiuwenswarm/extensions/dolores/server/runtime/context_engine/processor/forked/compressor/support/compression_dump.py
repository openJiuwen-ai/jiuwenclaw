from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.common.logging import logger
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.base import ContextWindow, ModelContext
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.base import (
    PrefixCompactProcessor,
    PrefixCompactSpan,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.support.compression_executor import (
    CompressionExecutor,
    CompressionRequest,
)
from openjiuwen.core.foundation.llm import BaseMessage


COMPRESSION_DUMP_DIR_ENV = "OPENJIUWEN_COMPRESSION_DUMP_DIR"


@dataclass(frozen=True)
class CompressionDumpInput:
    processor: PrefixCompactProcessor
    context: ModelContext
    context_window: ContextWindow
    config: Any
    processor_type: str
    original_messages: list[BaseMessage]
    span: PrefixCompactSpan
    prompt: str
    request: CompressionRequest
    response_content: str
    summary: str
    new_messages: list[BaseMessage]
    usage: dict[str, Any] | None


def dump_compression_artifact(compression: CompressionDumpInput) -> str | None:
    """Persist one compression invocation for offline effect analysis.

    Resolution order for the dump directory:
      1. ``config.compression_dump_dir`` (explicit override)
      2. ``OPENJIUWEN_COMPRESSION_DUMP_DIR`` env var
      3. ``{workspace_dir}/context/{session_id}_context/compression_logs/``

    Returns the written file path, or ``None`` when dumping is disabled or
    fails. All filesystem errors are swallowed so tracing never breaks the
    compression pipeline.
    """
    if not getattr(compression.config, "enable_compression_dump", False):
        return None

    dump_dir = _resolve_dump_dir(compression.context, compression.config)
    if not dump_dir:
        logger.info("[%s] compression dump skipped: no dump dir resolved", compression.processor_type)
        return None

    context_max = compression.processor.resolve_context_max(compression.context, {})
    tokens_before = compression.processor.count_messages_tokens(compression.original_messages, compression.context)
    tokens_after = compression.processor.count_messages_tokens(compression.new_messages, compression.context)
    target_tokens = compression.processor.count_messages_tokens(
        compression.span.messages_to_compress,
        compression.context,
    )
    messages_sent_to_model = CompressionExecutor.build_messages(compression.request)

    payload = {
        "processor_type": compression.processor_type,
        "session_id": _safe_context_value(compression.context, "session_id", "unknown_session"),
        "context_id": _safe_context_value(compression.context, "context_id", "unknown_context"),
        "trigger": {
            "context_max": context_max,
            "total_tokens_before": tokens_before,
            "target_tokens": target_tokens,
        },
        "span": {
            "preserved_prefix_count": len(compression.span.preserved_prefix),
            "messages_to_compress_count": len(compression.span.messages_to_compress),
            "protected_tail_count": len(compression.span.protected_tail),
        },
        "compression_request": {
            "prompt": compression.prompt,
            "exclude_recent_messages": compression.request.exclude_recent_messages,
            "system_messages_count": len(list(compression.request.system_messages or [])),
            "tools_count": len(list(compression.request.tools or [])),
            "tools": [_to_jsonable(tool) for tool in (compression.request.tools or [])],
            "messages_sent_to_model": [_to_jsonable(message) for message in messages_sent_to_model],
        },
        "compression_response": {
            "raw_content": compression.response_content,
            "extracted_summary": compression.summary,
            "usage": compression.usage,
        },
        "context_after": {
            "messages": [_to_jsonable(message) for message in compression.new_messages],
            "total_tokens_after": tokens_after,
        },
        "benefit": _build_benefit(tokens_before, tokens_after),
    }

    file_name = _build_file_name(payload["session_id"], compression.processor_type)
    dump_path = os.path.join(dump_dir, file_name)
    payload["timestamp"] = time.time()
    try:
        os.makedirs(dump_dir, exist_ok=True)
        with open(dump_path, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, ensure_ascii=False, indent=2)
    except Exception as exc:  # pragma: no cover - defensive: tracing must not break compression
        logger.warning("[%s] failed to dump compression artifact: %s", compression.processor_type, exc, exc_info=True)
        return None
    logger.info("[%s] dumped compression artifact path=%s", compression.processor_type, dump_path)
    return dump_path


def _resolve_dump_dir(context: ModelContext, config: Any) -> str:
    configured = getattr(config, "compression_dump_dir", None)
    if configured:
        return _expand_dump_dir_template(str(configured), context)
    env_dir = os.getenv(COMPRESSION_DUMP_DIR_ENV)
    if env_dir:
        return _expand_dump_dir_template(env_dir, context)
    workspace_dir = _workspace_dir(context)
    if not workspace_dir:
        return ""
    session_id = _safe_context_value(context, "session_id", "unknown_session")
    return os.path.join(workspace_dir, "context", f"{session_id}_context", "compression_logs")


def _workspace_dir(context: ModelContext) -> str:
    method = getattr(context, "workspace_dir", None)
    if not callable(method):
        return ""
    try:
        return str(method() or "")
    except Exception:
        return ""


def _expand_dump_dir_template(path: str, context: ModelContext) -> str:
    if "{session_id}" not in path and "{context_id}" not in path:
        return path
    return path.format(
        session_id=_safe_filename_part(_safe_context_value(context, "session_id", "unknown_session")),
        context_id=_safe_filename_part(_safe_context_value(context, "context_id", "unknown_context")),
    )


def _safe_context_value(context: ModelContext, method_name: str, fallback: str) -> str:
    method = getattr(context, method_name, None)
    if not callable(method):
        return fallback
    try:
        value = method()
    except Exception:
        return fallback
    return str(value or fallback)


def _build_benefit(tokens_before: int, tokens_after: int) -> dict[str, Any]:
    reduction = max(tokens_before - tokens_after, 0)
    ratio = (reduction / tokens_before) if tokens_before > 0 else 0.0
    return {
        "tokens_before": tokens_before,
        "tokens_after": tokens_after,
        "reduction": reduction,
        "reduction_ratio": round(ratio, 4),
    }


def _build_file_name(session_id: str, processor_type: str) -> str:
    # Millisecond timestamp prefix keeps each invocation's file unique and
    # sortable, mirroring RuleCompressionPipeline's dump naming.
    parts = [
        str(int(time.time() * 1000)),
        _safe_filename_part(processor_type),
        _safe_filename_part(session_id),
    ]
    return "_".join(parts) + ".json"


def _safe_filename_part(value: str) -> str:
    safe = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return safe[:80] or "unknown"


def _to_jsonable(value: Any) -> Any:
    """Convert compression artifacts to JSON-compatible values."""
    if hasattr(value, "model_dump"):
        try:
            return _to_jsonable(value.model_dump(exclude_none=True))
        except (AttributeError, TypeError, ValueError):
            pass
    if isinstance(value, dict):
        return {str(key): _to_jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_to_jsonable(item) for item in value]
    if isinstance(value, (str, int, float, bool)) or value is None:
        return value
    if hasattr(value, "__dict__"):
        try:
            return _to_jsonable(vars(value))
        except TypeError:
            pass
    return str(value)
