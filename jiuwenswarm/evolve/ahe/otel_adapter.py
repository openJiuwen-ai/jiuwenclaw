# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""OTEL SQLite spans → Langfuse-style trace dict adapter.

The sole purpose: return cleaned_trace format directly,
matching extract_trace_data() output structure exactly.
"""

from __future__ import annotations

import json
import logging
import re
from datetime import datetime, timezone
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from jiuwenswarm.evolve.models import TraceBatch

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
    """Tolerant parser for Python repr tool_calls strings."""
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
    """OTEL SQLite spans → cleaned_trace dict.

    Returns data structure identical to trace_converter.extract_trace_data().
    """

    def __init__(self, db_path: str):
        self._db_path = db_path

    def convert_trace(self, trace_id: str) -> dict[str, Any]:
        """Convert OTEL trace to cleaned_trace format.

        Returns dict identical to extract_trace_data() output.
        """
        spans = self._read_flat_spans(trace_id)
        if not spans:
            logger.warning("OtelTraceAdapter: no spans for trace_id=%s", trace_id)
            return {
                "id": trace_id,
                "timestamp": "N/A",
                "name": "N/A",
                "input": "N/A",
                "output": "N/A",
                "latency": "N/A",
                "system_prompt": "",
                "messages_count": 0,
                "messages": [],
                "total_tokens": "N/A",
                "observation_count": 0,
                "generation_count": 0,
                "subagents": [],
                "tool_definitions": [],
            }

        # Parse all spans
        parsed_spans = [self._parse_span(s) for s in spans]

        # Find root span
        root = self._find_root_span(parsed_spans)
        root_attrs = root.get("attributes", {})

        # Extract trace-level fields
        trace_input = self._extract_trace_input(parsed_spans)
        trace_output = self._extract_trace_output(parsed_spans)

        # Extract observations-level data
        system_prompt = self._extract_system_prompt(parsed_spans)
        agent_turns = self._build_agent_turns(parsed_spans)
        subagents = self._extract_subagents(parsed_spans)
        tool_definitions = self._extract_tool_definitions(parsed_spans)

        # Build messages
        messages = []
        if system_prompt:
            messages.append({"role": "system", "content": system_prompt})

        # Extract user message from trace input
        user_message = self._extract_user_message(trace_input)
        if user_message:
            messages.append({"role": "user", "content": user_message})

        messages.extend(agent_turns)

        # Calculate aggregates
        total_tokens = self._sum_total_tokens(parsed_spans)
        generation_count = len([s for s in parsed_spans if s.get('span_type') == 'LLM'])

        # Extract task_name from root span attributes or name
        # Priority: jiuwenclaw.task.name > gen_ai.task.name > jiuwenswarm.req.method > span.name
        task_name = (
            root_attrs.get("jiuwenclaw.task.name") or
            root_attrs.get("gen_ai.task.name") or
            root_attrs.get("jiuwenswarm.req.method") or  # Gateway-level method (session.create, etc.)
            root.get("name", "unknown")
        )

        # Build cleaned_trace
        cleaned_trace = {
            "id": trace_id,
            "timestamp": _ns_to_iso(root.get("start_time_ns")),
            "name": root.get("name", "N/A"),
            "task_name": task_name,  # Add task_name field for filtering
            "input": trace_input,
            "output": trace_output,
            "latency": _ns_to_ms(root.get("duration_ns")),
            "system_prompt": system_prompt,
            "messages_count": len(messages),
            "messages": messages,
            "total_tokens": total_tokens if total_tokens > 0 else "N/A",
            "observation_count": len(spans),
            "generation_count": generation_count,
            "subagents": subagents,
            "tool_definitions": tool_definitions,
            "user_message": user_message or "",
        }

        return cleaned_trace

    # ── Internal methods ──────────────────────────────────────────────

    def _read_flat_spans(self, trace_id: str) -> list[dict[str, Any]]:
        """Read all spans for trace_id from SQLite, sorted by start_time_ns."""
        from jiuwenswarm.telemetry.sqlite_exporter import read_flat_span

        try:
            return read_flat_span(self._db_path, trace_id)
        except Exception as exc:
            logger.warning("OtelTraceAdapter._read_flat_spans failed: %s", exc)
            return []

    def _parse_span(self, span: dict) -> dict[str, Any]:
        """Parse raw span dict to processed dict."""
        attrs = _parse_attrs(span.get("attributes"))
        events = _parse_events(span.get("events"))

        span_type_otel = attrs.get("gen_ai.span.type", "unknown")

        # Determine span_type
        if span_type_otel == "model":
            span_type = "LLM"
        elif span_type_otel == "tool":
            span_type = "TOOL"
        elif span_type_otel == "agent":
            span_type = "AGENT"
        else:
            span_type = "SPAN"

        return {
            "span_id": span.get("span_id"),
            "parent_span_id": span.get("parent_span_id"),
            "name": _adapt_observation_name(span.get("name", ""), attrs, span_type_otel),
            "span_type": span_type,
            "start_time_ns": span.get("start_time_ns"),
            "end_time_ns": span.get("end_time_ns"),
            "duration_ns": span.get("duration_ns"),
            "attributes": attrs,
            "events": events,
        }

    @staticmethod
    def _find_root_span(spans: list[dict]) -> dict:
        """Find the root span — no parent_span_id, or earliest such span."""
        roots = [s for s in spans if not s.get("parent_span_id")]
        if roots:
            roots.sort(key=lambda s: s.get("start_time_ns", 0))
            return roots[0]
        spans.sort(key=lambda s: s.get("start_time_ns", 0))
        return spans[0] if spans else {}

    def _extract_trace_input(self, spans: list[dict]) -> dict:
        """Extract trace-level input from first LLM span."""
        llm_spans = [s for s in spans if s.get("span_type") == "LLM"]
        if llm_spans:
            first_llm = llm_spans[0]
            return self._extract_llm_input(first_llm)
        return {}

    def _extract_trace_output(self, spans: list[dict]) -> dict:
        """Extract trace-level output - find final assistant response.

        Strategy:
        1. Find assistant message WITHOUT tool_calls → this is final response to user
        2. If all have tool_calls → execution trace, aggregate execution info
        3. This distinguishes "conversation traces" from "execution traces"
        """
        llm_spans = [s for s in spans if s.get("span_type") == "LLM"]
        if not llm_spans:
            return {}

        # Step 1: Find final response (assistant message without tool_calls)
        for llm_span in reversed(llm_spans):  # Check from last to first
            output = self._extract_llm_output(llm_span)
            tool_calls = output.get("tool_calls", [])
            content = output.get("content", "")

            # If no tool_calls and has content → final response
            if not tool_calls and content.strip():
                output["response_type"] = "final_response"
                return output

        # Step 2: No final response found → execution trace
        # Aggregate execution information from all LLM spans
        first_llm = self._extract_llm_output(llm_spans[0])
        last_llm = self._extract_llm_output(llm_spans[-1])

        # Collect all tool calls
        all_tools = []
        for llm_span in llm_spans:
            output = self._extract_llm_output(llm_span)
            tool_calls = output.get("tool_calls", [])
            for tc in tool_calls:
                func = tc.get("function", {})
                tool_name = func.get("name", tc.get("name", "unknown"))
                if tool_name not in all_tools:  # Deduplicate
                    all_tools.append(tool_name)

        # Build execution summary
        first_content = first_llm.get("content", "")
        last_tools = [tc.get("function", {}).get("name", tc.get("name", ""))
                      for tc in last_llm.get("tool_calls", [])]

        result = {
            "role": "assistant",
            "response_type": "execution_step",
            "content": first_content,  # Initial thinking/planning
            "tool_calls": last_llm.get("tool_calls", []),  # Last step's tool calls
            "execution_summary": {
                "total_llm_calls": len(llm_spans),
                "tools_used": all_tools,
                "first_thinking": first_content[:200] if first_content else "",
                "last_step_tools": last_tools,
            }
        }

        return result

    def _extract_llm_input(self, span: dict) -> dict:
        """Extract LLM input - prioritize attributes."""
        attrs = span.get("attributes", {})

        # ✅ Direct read from attributes (完整数据)
        if "gen_ai.input.messages" in attrs:
            messages_raw = attrs["gen_ai.input.messages"]
            # Handle double encoding
            if isinstance(messages_raw, str):
                try:
                    messages = json.loads(messages_raw)
                except json.JSONDecodeError:
                    messages = []
            else:
                messages = messages_raw if isinstance(messages_raw, list) else []

            # Extract tools
            tools = []
            if "gen_ai.tool.definitions" in attrs:
                tools_raw = attrs["gen_ai.tool.definitions"]
                if isinstance(tools_raw, str):
                    try:
                        tools = json.loads(tools_raw)
                    except Exception:
                        tools = []
                else:
                    tools = tools_raw if isinstance(tools_raw, list) else []

            return {
                "model": attrs.get("gen_ai.request.model", ""),
                "messages": messages,
                "tools": tools,
            }

        # Fallback: rebuild from events
        messages = []
        for ev in span.get("events", []):
            ev_attrs = _parse_attrs(ev.get("attributes"))
            ev_name = ev.get("name", "")

            if ev_name == "gen_ai.system.message":
                messages.append({"role": "system", "content": ev_attrs.get("content", "")})
            elif ev_name == "gen_ai.user.message":
                messages.append({"role": "user", "content": ev_attrs.get("content", "")})
            elif ev_name == "gen_ai.assistant.message":
                content = ev_attrs.get("content", "")
                tool_calls_raw = ev_attrs.get("tool_calls", "")
                msg = {"role": "assistant", "content": content}
                if tool_calls_raw:
                    parsed_tc = _parse_tool_calls_repr(tool_calls_raw)
                    if parsed_tc:
                        msg["tool_calls"] = parsed_tc
                messages.append(msg)

        return {
            "model": attrs.get("gen_ai.request.model", ""),
            "messages": messages,
            "tools": [],
        }

    def _extract_llm_output(self, span: dict) -> dict:
        """Extract LLM output."""
        attrs = span.get("attributes", {})
        events = span.get("events", [])

        # ✅ 优先从attributes读取 (完整数据)
        if "gen_ai.output.messages" in attrs:
            output_msgs_raw = attrs["gen_ai.output.messages"]
            # Handle encoding
            if isinstance(output_msgs_raw, str):
                try:
                    output_msgs = json.loads(output_msgs_raw)
                except json.JSONDecodeError:
                    output_msgs = []
            else:
                output_msgs = output_msgs_raw if isinstance(output_msgs_raw, list) else []

            # Find last assistant message
            if output_msgs:
                for msg in reversed(output_msgs):  # 从后往前找
                    if isinstance(msg, dict) and msg.get("role") == "assistant":
                        result = {"role": "assistant", "content": ""}

                        # Extract content from parts or direct
                        content = msg.get("content")
                        if isinstance(content, str):
                            result["content"] = content
                        elif content is None and "parts" in msg:
                            parts = msg.get("parts", [])
                            if isinstance(parts, list):
                                texts = []
                                for part in parts:
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        part_content = part.get("content", "")
                                        if isinstance(part_content, str):
                                            texts.append(part_content)
                                result["content"] = " ".join(texts) if texts else ""

                        # Extract tool_calls if available
                        if "tool_calls" in msg:
                            # Normalize tool_calls to ensure arguments are complete
                            normalized_tool_calls = []
                            for tc in msg["tool_calls"]:
                                normalized_tc = _normalize_tool_call(tc)

                                # Parse arguments string to dict for better accessibility
                                func = normalized_tc.get("function", {})
                                args_str = func.get("arguments", "{}")
                                if isinstance(args_str, str):
                                    try:
                                        args_dict = json.loads(args_str)
                                        # Store both string and dict versions
                                        func["arguments"] = args_str  # Keep original string
                                        func["_arguments_dict"] = args_dict  # Add dict version
                                    except json.JSONDecodeError:
                                        pass

                                normalized_tool_calls.append(normalized_tc)

                            result["tool_calls"] = normalized_tool_calls

                        # Add usage
                        usage = {}
                        if attrs.get("gen_ai.usage.total_tokens"):
                            usage["total_tokens"] = attrs["gen_ai.usage.total_tokens"]
                        if attrs.get("gen_ai.usage.input_tokens"):
                            usage["input_tokens"] = attrs["gen_ai.usage.input_tokens"]
                        if attrs.get("gen_ai.usage.output_tokens"):
                            usage["output_tokens"] = attrs["gen_ai.usage.output_tokens"]
                        if usage:
                            result["usage"] = usage

                        return result

        # Fallback: from events
        assistant_events = [ev for ev in events if ev.get("name") == "gen_ai.assistant.message"]

        if assistant_events:
            last = assistant_events[-1]
            ev_attrs = _parse_attrs(last.get("attributes"))
            content = ev_attrs.get("content", "")
            tool_calls_raw = ev_attrs.get("tool_calls", "")

            result = {"role": "assistant", "content": content}
            if tool_calls_raw:
                parsed_tc = _parse_tool_calls_repr(tool_calls_raw)
                if parsed_tc:
                    # Normalize and parse arguments
                    normalized_tool_calls = []
                    for tc in parsed_tc:
                        normalized_tc = _normalize_tool_call(tc)
                        func = normalized_tc.get("function", {})
                        args_str = func.get("arguments", "{}")
                        if isinstance(args_str, str):
                            try:
                                args_dict = json.loads(args_str)
                                func["_arguments_dict"] = args_dict
                            except json.JSONDecodeError:
                                pass
                        normalized_tool_calls.append(normalized_tc)
                    result["tool_calls"] = normalized_tool_calls

            # Add usage
            usage = {}
            if attrs.get("gen_ai.usage.total_tokens"):
                usage["total_tokens"] = attrs["gen_ai.usage.total_tokens"]
            if attrs.get("gen_ai.usage.input_tokens"):
                usage["input_tokens"] = attrs["gen_ai.usage.input_tokens"]
            if attrs.get("gen_ai.usage.output_tokens"):
                usage["output_tokens"] = attrs["gen_ai.usage.output_tokens"]
            if usage:
                result["usage"] = usage

            return result

        return {"role": "assistant", "content": ""}

    def _extract_system_prompt(self, spans: list[dict]) -> str:
        """Extract system prompt from first LLM span."""
        llm_spans = [s for s in spans if s.get("span_type") == "LLM"]
        if llm_spans:
            attrs = llm_spans[0].get("attributes", {})

            # ✅ Direct read from messages
            if "gen_ai.input.messages" in attrs:
                messages_raw = attrs["gen_ai.input.messages"]
                if isinstance(messages_raw, str):
                    try:
                        messages = json.loads(messages_raw)
                    except json.JSONDecodeError:
                        messages = []
                else:
                    messages = messages_raw if isinstance(messages_raw, list) else []

                # Find first system message
                for msg in messages:
                    if isinstance(msg, dict) and msg.get("role") == "system":
                        # Handle different content formats
                        content = msg.get("content")

                        # Format 1: Direct string content
                        if isinstance(content, str):
                            return content

                        # Format 2: content is None, check parts
                        if content is None and "parts" in msg:
                            parts = msg.get("parts", [])
                            if isinstance(parts, list):
                                # Extract text from parts
                                texts = []
                                for part in parts:
                                    if isinstance(part, dict) and part.get("type") == "text":
                                        part_content = part.get("content", "")
                                        if isinstance(part_content, str):
                                            texts.append(part_content)
                                return " ".join(texts) if texts else ""

        # Fallback: from events (only if llm_spans exists)
        if llm_spans:
            events = llm_spans[0].get("events", [])
            for ev in events:
                if ev.get("name") == "gen_ai.system.message":
                    ev_attrs = _parse_attrs(ev.get("attributes"))
                    return ev_attrs.get("content", "")

        return ""

    def _extract_user_message(self, trace_input: dict) -> str:
        """Extract user message from trace input."""
        if isinstance(trace_input, dict):
            messages = trace_input.get("messages", [])
            for msg in messages:
                if isinstance(msg, dict) and msg.get("role") == "user":
                    # Handle different content formats
                    content = msg.get("content")

                    # Format 1: Direct string content
                    if isinstance(content, str):
                        return content

                    # Format 2: content is None, check parts
                    if content is None and "parts" in msg:
                        parts = msg.get("parts", [])
                        if isinstance(parts, list):
                            # Extract text from parts
                            texts = []
                            for part in parts:
                                if isinstance(part, dict) and part.get("type") == "text":
                                    part_content = part.get("content", "")
                                    if isinstance(part_content, str):
                                        texts.append(part_content)
                            return " ".join(texts) if texts else ""

                    # Format 3: content is list (parts directly)
                    if isinstance(content, list):
                        texts = [p.get("content", "") for p in content if isinstance(p, dict) and p.get("type") == "text"]
                        return " ".join(texts)
        return ""

    def _build_agent_turns(self, spans: list[dict]) -> list[dict]:
        """Build agent turns from LLM spans + TOOL spans.

        Returns interleaved assistant + tool messages in chronological order.
        """
        turns = []

        # Get all LLM and TOOL spans, sorted by time
        llm_spans = [s for s in spans if s.get("span_type") == "LLM"]
        tool_spans = [s for s in spans if s.get("span_type") == "TOOL"]

        # Sort both by start_time
        llm_spans.sort(key=lambda s: s.get("start_time_ns", 0))
        tool_spans.sort(key=lambda s: s.get("start_time_ns", 0))

        # Build assistant messages from LLM spans
        for llm_span in llm_spans:
            output = self._extract_llm_output(llm_span)
            if output.get("content") or output.get("tool_calls"):
                # Add span_id for matching tool results
                output["_llm_span_id"] = llm_span.get("span_id")
                turns.append(output)

                # Extract tool result messages for this LLM span
                tool_results = self._extract_tool_results_for_llm(
                    llm_span, tool_spans
                )
                if tool_results:
                    turns.extend(tool_results)

        # Remove temporary field
        for turn in turns:
            if "_llm_span_id" in turn:
                del turn["_llm_span_id"]

        return turns

    def _extract_tool_results_for_llm(
        self, llm_span: dict, all_tool_spans: list[dict]
    ) -> list[dict]:
        """Extract tool result messages for a specific LLM span.

        Returns list of tool messages in OpenAI format:
        {"role": "tool", "tool_call_id": "...", "name": "...", "content": "..."}
        """
        llm_span_id = llm_span.get("span_id")
        tool_results = []

        # Find TOOL spans that are children of this LLM span
        child_tool_spans = [
            s for s in all_tool_spans
            if s.get("parent_span_id") == llm_span_id
        ]

        # Sort by start_time
        child_tool_spans.sort(key=lambda s: s.get("start_time_ns", 0))

        for tool_span in child_tool_spans:
            attrs = tool_span.get("attributes", {})

            # Extract tool call id (from gen_ai.tool.call.id or generate)
            tool_call_id = attrs.get("gen_ai.tool.call.id", "")
            if not tool_call_id:
                # Generate a synthetic id from span_id
                tool_call_id = f"call_tool_{tool_span.get('span_id', '')}"

            # Extract tool name
            tool_name = attrs.get("gen_ai.tool.name", "")

            # Extract tool result (output)
            tool_result_raw = attrs.get("gen_ai.tool.result", "")
            if isinstance(tool_result_raw, dict):
                tool_result_str = json.dumps(tool_result_raw, ensure_ascii=False)
            else:
                tool_result_str = str(tool_result_raw)

            # Build tool result message
            tool_msg = {
                "role": "tool",
                "tool_call_id": tool_call_id,
                "name": tool_name,
                "content": tool_result_str[:5000],  # Truncate for safety
            }

            tool_results.append(tool_msg)

        return tool_results

    def _extract_subagents(self, spans: list[dict]) -> list[dict]:
        """Extract subagent traces."""
        subagents = []
        agent_spans = [s for s in spans if s.get("span_type") == "AGENT"]

        for span in agent_spans:
            if span.get("parent_span_id"):  # Has parent, so it's a subagent
                attrs = span.get("attributes", {})
                subagents.append({
                    "id": span.get("span_id"),
                    "name": attrs.get("gen_ai.agent.name", attrs.get("jiuwenclaw.agent.name", "")),
                    "parent_id": span.get("parent_span_id"),
                })

        return subagents

    def _extract_tool_definitions(self, spans: list[dict]) -> list[dict]:
        """Extract tool definitions from LLM span."""
        llm_spans = [s for s in spans if s.get("span_type") == "LLM"]

        if llm_spans:
            attrs = llm_spans[0].get("attributes", {})

            # ✅ Direct read from attributes
            if "gen_ai.tool.definitions" in attrs:
                tools_raw = attrs["gen_ai.tool.definitions"]
                if isinstance(tools_raw, str):
                    try:
                        tools = json.loads(tools_raw)
                    except Exception:
                        tools = []
                else:
                    tools = tools_raw if isinstance(tools_raw, list) else []

                # Normalize to OpenAI function format
                normalized = []
                for tool in tools:
                    if isinstance(tool, dict):
                        if "type" == "function" and "function" in tool:
                            normalized.append(tool)  # Already correct format
                        elif "name" in tool:
                            # Simplified format → OpenAI format
                            normalized.append({
                                "type": "function",
                                "function": {
                                    "name": tool.get("name"),
                                    "parameters": tool.get("parameters", {}),
                                }
                            })

                return normalized

        return []

    def _sum_total_tokens(self, spans: list[dict]) -> int:
        """Sum total tokens from all LLM spans."""
        total = 0
        for span in spans:
            if span.get("span_type") == "LLM":
                attrs = span.get("attributes", {})
                tokens = attrs.get("gen_ai.usage.total_tokens")
                if tokens and isinstance(tokens, (int, float)):
                    total += int(tokens)
        return total

    def convert_batch(self, batch: TraceBatch) -> list[dict[str, Any]]:
        """Convert all trace_ids in a TraceBatch."""
        results = []
        for trace_id in batch.trace_ids:
            try:
                results.append(self.convert_trace(trace_id))
            except Exception as exc:
                logger.warning(
                    "OtelTraceAdapter: convert_trace(%s) failed: %s",
                    trace_id, exc,
                )
        return results