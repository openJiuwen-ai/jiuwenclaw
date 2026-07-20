# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Generic Agent/Code debug trace logger.

Mirrors ``openjiuwen.agent_teams.monitor.TeamStreamLogger`` in spirit —
aggregate token-streamed model output, categorise tool calls/results, write
human-readable timestamped text records, and never let logging break the
run — but adapted for single-agent / coding-agent streams:

* record header uses ``mode=`` / ``source=`` (not team ``member=``/``role=``);
* explicit ``run start`` / ``run end`` boundaries bracket each request;
* chunk payloads follow the Agent/Code shapes (``payload["tool_call"]``,
  ``payload["tool_result"]``, ``payload["usage_metadata"]`` …) seen in
  ``interface_deep._parse_stream_chunk``;
* payload truncation and secret-key masking are always on.

The logger is best-effort throughout: any failure (dir creation, file open,
per-chunk formatting) is swallowed so it can never affect the model run.
"""

from __future__ import annotations

import json
import logging
import re
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from jiuwenswarm.server.runtime.debug_trace.config import DebugTraceSettings

_logger = logging.getLogger(__name__)

# ── chunk type vocabulary (mirrors interface_deep in-loop switch) ──────────
_CHUNK_LLM_OUTPUT = "llm_output"
_CHUNK_LLM_REASONING = "llm_reasoning"
_CHUNK_LLM_USAGE = "llm_usage"
_CHUNK_ANSWER = "answer"
_CHUNK_TOOL_CALL = "tool_call"
_CHUNK_TOOL_UPDATE = "tool_update"
_CHUNK_TOOL_RESULT = "tool_result"

_ACCUMULATING_TYPES = frozenset({_CHUNK_LLM_OUTPUT, _CHUNK_LLM_REASONING})

# category -> level
_CATEGORY_LEVEL = {
    "text": "INFO",
    "reasoning": "DEBUG",
    "tool_call": "DEBUG",
    "tool_result": "DEBUG",
    "tool_update": "DEBUG",
    "context_usage": "DEBUG",
    "interaction": "WARN",
    "controller_output": "WARN",
    "message": "INFO",
    "todo": "INFO",
    "other": "INFO",
}

# secret-like dict keys whose values are masked even without full redaction.
# Matched by tokenising the key on non-alphanumerics so that token COUNTS
# (tokens_used, total_tokens, input_tokens …) are NOT mistaken for secrets,
# while genuine secret names (access_token, api_key, password …) still match.
_SECRET_TOKENS = frozenset(
    {"token", "password", "passwd", "pwd", "secret", "apikey", "authorization",
     "authorisation", "cookie"}
)


def _looks_secret(key: str) -> bool:
    """True if *key* names a secret-like field whose value should be masked.

    Tokenises the key on non-alphanumeric separators (``_ - . /`` …) so
    ``tokens_used`` → {"tokens","used"} is NOT a secret (plural = a count),
    but ``access_token`` → {"access","token"} and ``api_key`` → {"api","key"} are.
    """
    if not isinstance(key, str):
        return False
    tokens = {t for t in re.split(r"[^a-z0-9]+", key.lower()) if t}
    if not tokens:
        return False
    if "tokens" in tokens:  # plural = token counts (tokens_used, total_tokens)
        return False
    if tokens & _SECRET_TOKENS:
        return True
    if {"api", "key"} <= tokens:  # api_key / api-key
        return True
    return False


_UNKNOWN = "<unknown>"


def _now_ts() -> str:
    return datetime.now(timezone.utc).astimezone().strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def _truncate(text: str, limit: int | None) -> str:
    """Truncate *text* to *limit* chars with a visible original-length marker.

    ``None`` limit (or non-positive) means never truncate.
    """
    if not limit or limit <= 0:
        return text
    if len(text) <= limit:
        return text
    return text[:limit] + f"... (truncated, original_chars={len(text)})"


def _mask_secrets(value: Any) -> Any:
    """Recursively mask values whose dict key looks secret-like.

    Returns a shallow-ish copy for dicts/lists so the caller's payload is
    untouched. Non-container values pass through unchanged.
    """
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for k, v in value.items():
            if isinstance(k, str) and _looks_secret(k):
                out[k] = "***"
            else:
                out[k] = _mask_secrets(v)
        return out
    if isinstance(value, list):
        return [_mask_secrets(v) for v in value]
    return value


def _extract_content(payload: Any) -> str:
    if isinstance(payload, dict):
        return payload.get("content", "") or payload.get("output", "") or ""
    if isinstance(payload, str):
        return payload
    return str(payload) if payload is not None else ""


def _classify(ctype: str, payload: Any) -> str:
    if ctype in (_CHUNK_LLM_OUTPUT, _CHUNK_ANSWER):
        return "text"
    if ctype == _CHUNK_LLM_REASONING:
        return "reasoning"
    if ctype == _CHUNK_LLM_USAGE:
        return "context_usage"
    if ctype == _CHUNK_TOOL_CALL:
        return "tool_call"
    if ctype == _CHUNK_TOOL_RESULT:
        return "tool_result"
    if ctype == _CHUNK_TOOL_UPDATE:
        return "tool_update"
    if ctype == "controller_output":
        return "controller_output"
    if ctype == "message":
        return "message"
    if ctype == "todo.updated":
        return "todo"
    return "other"


class _Run:
    """A pending accumulation of token-streamed chunks (single source)."""

    __slots__ = ("category", "buf")

    def __init__(self, category: str) -> None:
        self.category = category
        self.buf: list[str] = []


class DebugTraceLogger:
    """Best-effort human-readable dump writer for one Agent/Code run.

    Construct per request (when ``/debug`` is active), call ``start_run``
    once, ``feed`` every stream chunk, then ``end_run`` + ``flush``. All
    public methods are no-ops after a construction failure (``_disabled``).
    """

    def __init__(
        self,
        *,
        file_path: Path | str,
        mode: str,
        session_id: str,
        request_id: str | None,
        round_id: int | None = None,
        settings: DebugTraceSettings,
    ) -> None:
        self._mode = mode or ""
        self._session_id = session_id or ""
        self._request_id = request_id
        self._round_id = round_id
        self._settings = settings

        self._path = Path(file_path)
        self._file: TextIO | None = None
        self._disabled = False
        self._started = False
        self._ended = False
        self._start_monotonic: float | None = None
        self._chunk_count = 0

        # Single-source accumulation (Phase 1: no subagent source metadata).
        self._run: _Run | None = None
        self._llm_output_seen = False

        try:
            self._path.parent.mkdir(parents=True, exist_ok=True)
            self._file = open(self._path, "a", encoding="utf-8")
        except Exception as exc:
            _logger.warning(
                "[DebugTrace] disabled for session=%s mode=%s: open failed: %s",
                self._session_id, self._mode, exc,
            )
            self._disabled = True

    # ── public API ───────────────────────────────────────────────────────
    def start_run(
        self,
        *,
        input_text: str | None = None,
        otel_trace_id: str = "",
        otel_span_id: str = "",
    ) -> None:
        if self._disabled or self._started:
            return
        self._started = True
        self._start_monotonic = time.monotonic()
        try:
            lines = [
                "========== run start ==========",
                f"timestamp={_now_ts()}",
                f"mode={self._mode or _UNKNOWN}",
                f"session_id={self._session_id or _UNKNOWN}",
                f"request_id={self._request_id or _UNKNOWN}",
            ]
            if self._round_id is not None:
                lines.append(f"round_id={self._round_id}")
            # OTel ids when a run span was opened (empty when OTel not enabled).
            # Lets the dump be cross-referenced with a Langfuse/collector trace.
            lines.append(f"otel_trace_id={otel_trace_id or ''}")
            lines.append(f"otel_span_id={otel_span_id or ''}")
            preview = _truncate(
                input_text or "",
                self._settings.generic_payload_max_chars,
            )
            lines.append(f"input={preview}")
            self._write_raw("\n".join(lines))
        except Exception as exc:
            self._safe_warn(f"start_run error: {exc!r}")

    def feed(self, chunk: Any) -> None:
        if self._disabled:
            return
        try:
            self._feed(chunk)
        except Exception as exc:  # never break the stream
            self._safe_warn(f"feed error: {exc!r}")

    def end_run(self, *, status: str, error: BaseException | None = None) -> None:
        if self._disabled or self._ended:
            return
        self._ended = True
        try:
            self._flush_run()
            elapsed = (
                int((time.monotonic() - self._start_monotonic) * 1000)
                if self._start_monotonic is not None
                else 0
            )
            lines = [
                "========== run end ==========",
                f"timestamp={_now_ts()}",
                f"status={status}",
                f"elapsed_ms={elapsed}",
                f"chunks={self._chunk_count}",
            ]
            if error is not None:
                lines.append(f"error_type={type(error).__name__}")
                lines.append(f"error={error}")
            self._write_raw("\n".join(lines))
        except Exception as exc:
            self._safe_warn(f"end_run error: {exc!r}")

    def flush(self) -> None:
        if self._disabled:
            return
        try:
            self._flush_run()
            if self._file is not None:
                self._file.flush()
        except Exception as exc:
            _logger.debug("[DebugTrace] flush error: %s", exc)
        finally:
            if self._file is not None:
                try:
                    self._file.close()
                except Exception as exc:
                    _logger.debug("[DebugTrace] close error: %s", exc)
                self._file = None

    # ── internals ────────────────────────────────────────────────────────
    def _feed(self, chunk: Any) -> None:
        self._chunk_count += 1

        ctype, payload = self._unpack(chunk)
        if ctype is None:
            # Dict / untyped chunk: record as 'other' for visibility.
            self._flush_run()
            self._emit("other", "main", _truncate(_safe_str(chunk), self._settings.generic_payload_max_chars))
            return

        category = _classify(ctype, payload)

        # `answer` duplicates already-streamed llm_output — drop if seen.
        if ctype == _CHUNK_ANSWER and self._llm_output_seen:
            return

        if ctype in _ACCUMULATING_TYPES:
            if ctype == _CHUNK_LLM_OUTPUT and not self._settings.include_model_output:
                return
            if ctype == _CHUNK_LLM_REASONING and not self._settings.include_reasoning:
                return
            content = _extract_content(payload)
            if not content:
                return
            if self._run is not None and self._run.category != category:
                self._flush_run()
            if self._run is None:
                self._run = _Run(category)
            self._run.buf.append(content)
            if ctype == _CHUNK_LLM_OUTPUT:
                self._llm_output_seen = True
            return

        # Discrete chunk: flush pending text, then emit a summary now.
        self._flush_run()
        summary = self._discrete_summary(ctype, category, payload)
        if summary:
            self._emit(category, "main", summary)

    @staticmethod
    def _unpack(chunk: Any) -> tuple[str | None, Any]:
        """Extract (type, payload) from a chunk, tolerating shapes."""
        ctype = getattr(chunk, "type", None)
        payload = getattr(chunk, "payload", None)
        if ctype is None and isinstance(chunk, dict):
            ctype = chunk.get("type") or chunk.get("event_type")
            payload = chunk.get("payload") or chunk.get("data") or chunk
        return (str(ctype) if ctype is not None else None), payload

    def _discrete_summary(self, ctype: str, category: str, payload: Any) -> str:
        s = self._settings
        if category == "tool_call":
            return self._tool_call_summary(payload, s.tool_args_max_chars)
        if category == "tool_result":
            return self._tool_result_summary(payload, s.tool_result_max_chars)
        if category == "tool_update":
            return self._tool_update_summary(payload, s.tool_args_max_chars)
        if category == "context_usage":
            return self._usage_summary(payload)
        if ctype in ("controller_output", "message", "todo.updated"):
            content = _extract_content(payload)
            if content:
                return _truncate(content, s.generic_payload_max_chars)
            return _truncate(_safe_str(_mask_secrets(_as_dict(payload))), s.generic_payload_max_chars)
        # other
        return _truncate(_safe_str(_mask_secrets(_as_dict(payload))), s.generic_payload_max_chars)

    def _tool_call_summary(self, payload: Any, args_limit: int) -> str:
        info = payload.get("tool_call", payload) if isinstance(payload, dict) else payload
        info = _mask_secrets(_as_dict(info)) if isinstance(info, dict) else info
        if not isinstance(info, dict):
            return _truncate(_safe_str(info), self._settings.generic_payload_max_chars)
        name = info.get("name") or info.get("tool_name") or ""
        call_id = info.get("id") or info.get("tool_call_id") or ""
        args_raw = info.get("arguments") or info.get("args") or info.get("tool_args") or ""
        if not self._settings.include_tool_args:
            args_raw = "<redacted>"
        args = _truncate(_stringify(args_raw), args_limit)
        return f"tool_name={name} tool_call_id={call_id}\narguments={args}"

    def _tool_result_summary(self, payload: Any, result_limit: int) -> str:
        info = payload.get("tool_result", payload) if isinstance(payload, dict) else payload
        info = _mask_secrets(_as_dict(info)) if isinstance(info, dict) else info
        if not isinstance(info, dict):
            return _truncate(_safe_str(info), self._settings.generic_payload_max_chars)
        name = info.get("tool_name") or info.get("name") or ""
        call_id = info.get("tool_call_id") or info.get("id") or ""
        lines = [f"tool_name={name} tool_call_id={call_id}"]
        if info.get("is_error") or info.get("error"):
            err = info.get("error", "")
            lines.append(f"is_error=True error={_truncate(_stringify(err), result_limit)}")
        status = info.get("status")
        if status:
            lines.append(f"status={status}")
        if self._settings.include_tool_result:
            result_raw = info.get("result")
            if result_raw is None:
                result_raw = info.get("raw_output", "")
            lines.append(f"result: {_truncate(_stringify(result_raw), result_limit)}")
        return "\n".join(lines)

    def _tool_update_summary(self, payload: Any, args_limit: int) -> str:
        update = payload.get("tool_update", payload) if isinstance(payload, dict) else payload
        update = _mask_secrets(_as_dict(update)) if isinstance(update, dict) else update
        if not isinstance(update, dict):
            return _truncate(_safe_str(update), self._settings.generic_payload_max_chars)
        name = update.get("tool_name") or update.get("name") or ""
        status = update.get("status", "")
        call_id = update.get("tool_call_id") or update.get("id") or ""
        args = _truncate(_stringify(update.get("arguments", "")), args_limit)
        return f"tool_name={name} status={status} tool_call_id={call_id}\narguments={args}"

    def _usage_summary(self, payload: Any) -> str:
        meta = payload.get("usage_metadata", payload) if isinstance(payload, dict) else {}
        if not isinstance(meta, dict):
            return _truncate(_safe_str(payload), self._settings.generic_payload_max_chars)
        parts = []
        for key in ("input_tokens", "output_tokens", "total_tokens"):
            if key in meta:
                parts.append(f"{key}={meta[key]}")
        for key in ("model_name",):
            if key in meta and meta[key]:
                parts.append(f"{key}={meta[key]}")
        if not parts:
            return _truncate(_safe_str(payload), self._settings.generic_payload_max_chars)
        return " ".join(parts)

    def _flush_run(self) -> None:
        run = self._run
        self._run = None
        if run is None or not run.buf:
            return
        text = "".join(run.buf)
        limit = self._settings.max_model_output_chars if run.category == "text" else None
        self._emit(run.category, "main", _truncate(text, limit))

    def _emit(self, category: str, source: str, content: str) -> None:
        if not content:
            return
        level = _CATEGORY_LEVEL.get(category, "INFO")
        header = f"[{level}] mode={self._mode or _UNKNOWN} source={source} category={category}"
        prefixed = "\n".join(f"  | {line}" for line in content.split("\n"))
        self._write_raw(f"{header}\n{prefixed}")

    def _write_raw(self, body: str) -> None:
        if self._file is None:
            return
        try:
            self._file.write(f"{body}\n")
            self._file.flush()
        except Exception as exc:
            _logger.debug("[DebugTrace] write failed: %s", exc)

    def _safe_warn(self, msg: str) -> None:
        _logger.warning("[DebugTrace] %s (session=%s)", msg, self._session_id)
        if self._file is not None:
            try:
                self._file.write(f"{_now_ts()} [WARN] {msg}\n")
                self._file.flush()
            except Exception as exc:
                _logger.debug("[DebugTrace] failed to write warning to dump file: %s", exc)


def _as_dict(value: Any) -> Any:
    """Coerce a payload into a dict for masking when it's dict-like."""
    return value if isinstance(value, dict) else value


def _stringify(value: Any) -> str:
    if isinstance(value, str):
        return value
    try:
        return json.dumps(value, ensure_ascii=False, default=str)
    except Exception:
        return str(value)


def _safe_str(value: Any) -> str:
    try:
        return _stringify(value)
    except Exception:
        return repr(value)


__all__ = ["DebugTraceLogger"]
