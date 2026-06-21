# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""OTEL SQLite spans → Langfuse-style trace dict adapter.

The sole purpose: let ``_extract_trace_data_impl`` from
agentic-harness-engineering consume standard OTEL trace data.

Design principles:
  - OTEL instrumentor is NOT modified
  - trace_converter is NOT modified
  - All mapping logic lives here — one file, one responsibility
  - No dependency on existing evolve proposal generators or policies
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import Any

logger = logging.getLogger(__name__)

# ── Time format helpers ──────────────────────────────────────────────────


def _ns_to_iso(ns: int | None) -> str:
    """Nanosecond timestamp → ISO 8601 string."""
    if ns is None:
        return "N/A"
    seconds = ns / 1_000_000_000
    dt = datetime.fromtimestamp(seconds, tz=timezone.utc)
    return dt.isoformat()


def _ns_to_ms(ns: int | None) -> float | str:
    """Nanosecond → millisecond float."""
    if ns is None:
        return "N/A"
    return ns / 1_000_000


def _parse_attrs(raw: str | dict | None) -> dict:
    """Parse attributes field — JSON string or dict → dict."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def _parse_events(raw: str | list | None) -> list[dict]:
    """Parse events field — JSON string or list → list of dicts."""
    if raw is None:
        return []
    if isinstance(raw, list):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return []


# ── LLM span name adaptation ────────────────────────────────────────────

_LLM_SYSTEM_TO_KEYWORD = {
    "openai": "openai",
    "anthropic": "anthropic",
    "azure": "openai",
    "gemini": "gemini",
    "google": "gemini",
    "deepseek": "openai",
    "unknown": "openai",
}


def _adapt_observation_name(
    original_name: str, attrs: dict, span_type_otel: str
) -> str:
    """Ensure LLM span name contains is_llm_span keywords."""
    if span_type_otel != "model":
        return original_name
    system = attrs.get("gen_ai.system", "unknown")
    keyword = _LLM_SYSTEM_TO_KEYWORD.get(system, "openai")
    return f"{keyword}.chat"


# ── Tool calls repr parser ──────────────────────────────────────────────


def _parse_tool_calls_repr(raw: str) -> list[dict]:
    """Tolerant parser for Python repr tool_calls strings.

    Handles:
      - JSON format: '[{"id": "call_abc", ...}]'
      - Python repr: '[ToolCall(id='call_abc', name='bash', arguments='...')]'
      - Anthropic format: '[{'id': 'toolu_abc', 'type': 'tool_use', ...}]'
    """
    if not raw or raw == "None":
        return []

    # Strategy 1: JSON parse
    try:
        parsed = json.loads(raw)
        if isinstance(parsed, list):
            return [_normalize_tool_call(tc) for tc in parsed]
    except (json.JSONDecodeError, TypeError):
        pass

    # Strategy 2: regex fallback for Python repr
    # Pattern: name='xxx', id='xxx'
    name_pattern = r"""(?:name|function\.name)=['"](\w+)['"]"""
    id_pattern = r"""(?:id|call_id)=['"]([\w_-]+)['"]"""
    names = re.findall(name_pattern, raw)
    ids = re.findall(id_pattern, raw)

    if names:
        results = []
        for i, name in enumerate(names):
            tc_id = ids[i] if i < len(ids) else f"call_gen_{i}"
            results.append({
                "id": tc_id,
                "type": "function",
                "function": {"name": name, "arguments": "{}"},
            })
        return results

    return []


def _normalize_tool_call(tc: dict) -> dict:
    """Normalize a tool call dict to OpenAI function-calling format."""
    if "function" in tc:
        return tc  # Already OpenAI format
    # Anthropic format: {id, type, name, input}
    return {
        "id": tc.get("id", ""),
        "type": "function",
        "function": {
            "name": tc.get("name", tc.get("function", {}).get("name", "")),
            "arguments": json.dumps(tc.get("input", tc.get("arguments", {}))),
        },
    }


# ── Core OtelTraceAdapter ───────────────────────────────────────────────


class OtelTraceAdapter:
    """OTEL SQLite spans → Langfuse-style trace dict.

    Usage:
        adapter = OtelTraceAdapter(db_path="traces.db")
        trace_dict = adapter.convert_trace("abc123def456...")
        # trace_dict can be passed to _extract_trace_data_impl
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    def convert_trace(self, trace_id: str) -> dict[str, Any]:
        """Convert one OTEL trace to a dict compatible with _extract_trace_data_impl."""
        spans = self._read_flat_spans(trace_id)
        if not spans:
            logger.warning("OtelTraceAdapter: no spans for trace_id=%s", trace_id)
            return {"id": trace_id, "observations": []}

        # Find root span (first span without parent_span_id, or earliest)
        root = self._find_root_span(spans)
        observations = [self._span_to_observation(s) for s in spans]

        trace_dict = {
            "id": trace_id,
            "trace_id": trace_id,
            "timestamp": _ns_to_iso(root.get("start_time_ns")),
            "name": root.get("name", "N/A"),
            "input": self._reconstruct_trace_input(root, spans),
            "output": self._reconstruct_trace_output(root, spans),
            "latency": _ns_to_ms(root.get("duration_ns")),
            "observations": observations,
        }
        return trace_dict

    def convert_batch(self, batch: object) -> list[dict[str, Any]]:
        """Convert all trace_ids in a TraceBatch."""
        results = []
        for trace_id in batch.trace_ids:  # type: ignore[attr-defined]
            try:
                results.append(self.convert_trace(trace_id))
            except Exception as exc:
                logger.warning(
                    "OtelTraceAdapter: convert_trace(%s) failed: %s",
                    trace_id, exc,
                )
        return results

    # ── Internal methods ──────────────────────────────────────────────

    def _read_flat_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """Read all spans for trace_id from SQLite, sorted by start_time_ns."""
        from jiuwenswarm.evolve.storage.sqlite_store import SqliteStore

        # Use SqliteStore.read_spans which returns list of dicts
        # Create a temporary read-only connection
        try:
            conn = SqliteStore.__new__(SqliteStore)
            conn._traces_db_path = self._db_path
            spans = conn._get_traces_conn().execute(
                "SELECT * FROM spans WHERE trace_id = ? ORDER BY start_time_ns",
                (trace_id,),
            ).fetchall()
            return [dict(r) for r in spans]
        except Exception as exc:
            logger.warning("OtelTraceAdapter._read_flat_spans failed: %s", exc)
            return []

    @staticmethod
    def _find_root_span(spans: list[dict]) -> dict:
        """Find the root span — no parent_span_id, or earliest such span."""
        roots = [s for s in spans if not s.get("parent_span_id")]
        if roots:
            # Pick the earliest root
            roots.sort(key=lambda s: s.get("start_time_ns", 0))
            return roots[0]
        # Fallback: earliest span overall
        spans.sort(key=lambda s: s.get("start_time_ns", 0))
        return spans[0] if spans else {}

    def _span_to_observation(self, span: dict) -> dict[str, Any]:
        """Single OTEL span dict → Langfuse observation dict."""
        attrs = _parse_attrs(span.get("attributes"))
        events = _parse_events(span.get("events"))

        span_type_otel = attrs.get("gen_ai.span.type", "unknown")

        # Determine Langfuse type/span_type
        if span_type_otel == "model":
            lf_type = "GENERATION"
            lf_span_type = "LLM"
        elif span_type_otel == "tool":
            lf_type = "SPAN"
            lf_span_type = "TOOL"
        elif span_type_otel == "agent":
            lf_type = "SPAN"
            lf_span_type = "AGENT"
        else:
            lf_type = "SPAN"
            lf_span_type = span.get("kind", "SPAN")

        name = _adapt_observation_name(span.get("name", ""), attrs, span_type_otel)

        # Build observation dict
        obs: dict[str, Any] = {
            "id": span.get("span_id", ""),
            "name": name,
            "type": lf_type,
            "span_type": lf_span_type,
            "parentObservationId": span.get("parent_span_id") or None,
            "startTime": _ns_to_iso(span.get("start_time_ns")),
            "endTime": _ns_to_iso(span.get("end_time_ns")),
            "latency": _ns_to_ms(span.get("duration_ns")),
            "model": attrs.get("gen_ai.request.model") or attrs.get("gen_ai.response.model"),
            "totalTokens": attrs.get("gen_ai.usage.total_tokens"),
        }

        # Input/output reconstruction depends on span_type
        if span_type_otel == "model":
            obs["input"] = self._reconstruct_llm_input(attrs, events)
            obs["output"] = self._reconstruct_llm_output(attrs, events)
            obs["totalTokens"] = attrs.get("gen_ai.usage.total_tokens") or "N/A"
            # Also write usage into output for dual-path compatibility
            if isinstance(obs["output"], dict):
                obs["output"]["usage"] = {
                    "total_tokens": attrs.get("gen_ai.usage.total_tokens", "N/A"),
                    "input_tokens": attrs.get("gen_ai.usage.input_tokens", "N/A"),
                    "output_tokens": attrs.get("gen_ai.usage.output_tokens", "N/A"),
                }
        elif span_type_otel == "tool":
            obs["input"] = self._reconstruct_tool_input(attrs, events)
            obs["output"] = self._reconstruct_tool_output(attrs, events)
        else:
            obs["input"] = attrs
            obs["output"] = {}

        # Metadata for subagent detection
        metadata: dict[str, Any] = {}
        if span_type_otel == "agent" and span.get("parent_span_id"):
            metadata["subagent_id"] = span.get("span_id", "")
            metadata["subagent_name"] = (
                attrs.get("jiuwenclaw.agent.name", "")
                or attrs.get("gen_ai.agent.name", "")
            )
            metadata["controller_observation_id"] = span.get("parent_span_id", "")

        if metadata:
            obs["metadata"] = metadata

        obs["calculatedTotalCost"] = "N/A"
        return obs

    def _reconstruct_llm_input(self, attrs: dict, events: list) -> dict:
        """Reconstruct LLM span input from OTEL events → OpenAI messages format."""
        messages = []
        for ev in events:
            ev_attrs = _parse_attrs(ev.get("attributes"))
            ev_name = ev.get("name", "")

            if ev_name == "gen_ai.system.message":
                messages.append({"role": "system", "content": ev_attrs.get("content", "")})
            elif ev_name == "gen_ai.user.message":
                messages.append({"role": "user", "content": ev_attrs.get("content", "")})
            elif ev_name == "gen_ai.assistant.message":
                # May contain tool_calls
                content = ev_attrs.get("content", "")
                tool_calls_raw = ev_attrs.get("tool_calls", "")
                msg = {"role": "assistant", "content": content}
                if tool_calls_raw:
                    parsed_tc = _parse_tool_calls_repr(tool_calls_raw)
                    if parsed_tc:
                        msg["tool_calls"] = parsed_tc
                messages.append(msg)
            elif ev_name == "gen_ai.tool.message":
                msg = {"role": "tool", "content": ev_attrs.get("content", "")}
                if ev_attrs.get("tool_call_id"):
                    msg["tool_call_id"] = ev_attrs["tool_call_id"]
                messages.append(msg)

        # Add model from attributes
        model = attrs.get("gen_ai.request.model", "")

        # Tool definitions — simplified from tool spans (see §4.7)
        # These are collected separately and injected by convert_trace
        tools = []

        return {
            "model": model,
            "messages": messages,
            "tools": tools,
        }

    def _reconstruct_llm_output(self, attrs: dict, events: list) -> dict:
        """Reconstruct LLM span output from OTEL events → assistant message."""
        # Find the last assistant message event
        assistant_events = [
            ev for ev in events
            if ev.get("name") == "gen_ai.assistant.message"
        ]

        if not assistant_events:
            return {"role": "assistant", "content": ""}

        last = assistant_events[-1]
        ev_attrs = _parse_attrs(last.get("attributes"))

        content = ev_attrs.get("content", "")
        tool_calls_raw = ev_attrs.get("tool_calls", "")

        result: dict[str, Any] = {"role": "assistant", "content": content}

        if tool_calls_raw:
            parsed_tc = _parse_tool_calls_repr(tool_calls_raw)
            if parsed_tc:
                result["tool_calls"] = parsed_tc

        return result

    def _reconstruct_tool_input(self, attrs: dict, events: list) -> dict:
        """Reconstruct tool span input from events."""
        for ev in events:
            if ev.get("name") == "gen_ai.tool.arguments":
                ev_attrs = _parse_attrs(ev.get("attributes"))
                return ev_attrs.get("arguments", ev_attrs)
        # Fallback: from attributes
        args_raw = attrs.get("gen_ai.tool.arguments", "")
        if args_raw:
            return _parse_attrs(args_raw)
        return {}

    def _reconstruct_tool_output(self, attrs: dict, events: list) -> dict:
        """Reconstruct tool span output from events."""
        for ev in events:
            if ev.get("name") == "gen_ai.tool.result":
                ev_attrs = _parse_attrs(ev.get("attributes"))
                return ev_attrs.get("result", ev_attrs)
        # Fallback: from attributes
        result_raw = attrs.get("gen_ai.tool.result", "")
        if result_raw:
            return _parse_attrs(result_raw)
        return {}

    def _reconstruct_trace_input(
        self, root_span: dict, all_spans: list[dict]
    ) -> dict:
        """Trace-level input — from first user message event."""
        # Find first LLM span's first user message
        llm_spans = [
            s for s in all_spans
            if _parse_attrs(s.get("attributes")).get("gen_ai.span.type") == "model"
        ]
        if llm_spans:
            first_llm = llm_spans[0]
            events = _parse_events(first_llm.get("events"))
            user_events = [
                e for e in events if e.get("name") == "gen_ai.user.message"
            ]
            if user_events:
                return {
                    "message": _parse_attrs(
                        user_events[0].get("attributes")
                    ).get("content", ""),
                }

        # Fallback: root span attributes
        return _parse_attrs(root_span.get("attributes"))

    def _reconstruct_trace_output(
        self, root_span: dict, all_spans: list[dict]
    ) -> dict:
        """Trace-level output — from last LLM span's final assistant message."""
        llm_spans = [
            s for s in all_spans
            if _parse_attrs(s.get("attributes")).get("gen_ai.span.type") == "model"
        ]
        if llm_spans:
            last_llm = llm_spans[-1]
            events = _parse_events(last_llm.get("events"))
            assistant_events = [
                e for e in events if e.get("name") == "gen_ai.assistant.message"
            ]
            if assistant_events:
                last_ev = assistant_events[-1]
                return {
                    "role": "assistant",
                    "content": _parse_attrs(
                        last_ev.get("attributes")
                    ).get("content", ""),
                }

        return {}

    def _collect_tool_definitions(self, spans: list[dict]) -> list[dict]:
        """From tool spans, infer simplified tool definitions.

        Output format compatible with extract_tool_definitions_from_observations:
        {"type": "function", "function": {"name": "bash", "parameters": {}}

        Note: parameters is empty dict — OTEL doesn't record full tool schemas.
        """
        seen_names: set[str] = set()
        definitions: list[dict] = []
        for span in spans:
            attrs = _parse_attrs(span.get("attributes"))
            if attrs.get("gen_ai.span.type") != "tool":
                continue
            tool_name = attrs.get("gen_ai.tool.name", "")
            if not tool_name or tool_name in seen_names:
                continue
            seen_names.add(tool_name)
            definitions.append({
                "type": "function",
                "function": {"name": tool_name, "parameters": {}},
            })
        return definitions
