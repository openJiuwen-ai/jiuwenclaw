# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""DiagnosisAgent read-only tool implementations.

Operates on NormalizedTrace dicts (output of OtelTraceAdapter + _extract_trace_data).
This means DiagnosisAgent consumes the same structured data format used by
TraceOutcomeEvaluator and AheProposer — the CLEAN step is NOT bypassed.
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any

logger = logging.getLogger(__name__)

# ── Output truncation ────────────────────────────────────────────────────


def _truncate_tool_output(
    content: str,
    max_chars: int = 10000,
    head_lines: int = 50,
    tail_lines: int = 30,
) -> str:
    """Truncate long tool output, keeping head + tail with truncation notice."""
    lines = content.split("\n")
    total_lines = len(lines)
    total_chars = len(content)

    if total_chars <= max_chars:
        return content

    head = lines[:head_lines]
    tail = lines[-tail_lines:]
    omitted = total_lines - head_lines - tail_lines

    truncation_notice = (
        f"\n...[truncated: {omitted} lines omitted, "
        f"total_chars={total_chars}. "
        f"Use offset/limit or search to read specific sections]...\n"
    )

    return "\n".join(head) + truncation_notice + "\n".join(tail)


# ── Helpers ──────────────────────────────────────────────────────────────


def _find_trace(
    normalized_traces: list[dict], trace_id: str
) -> int | None:
    """Find index of a trace in normalized_traces by trace_id."""
    for i, nt in enumerate(normalized_traces):
        tid = nt.get("id") or nt.get("trace_id") or ""
        if tid == trace_id:
            return i
    return None


# ── Tool executor ────────────────────────────────────────────────────────


class DiagnosisToolExecutor:
    """Dispatches tool calls — operates on NormalizedTrace data.

    Consumes NormalizedTrace dicts (not raw OTEL spans). Optional store
    for evolution.db queries (query_evolve_records, query_proposals).
    """

    def __init__(
        self,
        normalized_traces: list[dict] | None = None,
        store: Any | None = None,
        workspace_dir: str | None = None,
    ) -> None:
        self._normalized_traces = normalized_traces or []
        self._store = store
        self._workspace_dir = workspace_dir

    def execute(self, tool_name: str, arguments: dict) -> dict:
        """Dispatch a tool call by name."""
        dispatch = {
            "read_trace": self._read_trace,
            "search_trace": self._search_trace,
            "list_traces": self._list_traces,
            "query_evolve_records": self._query_evolve_records,
            "query_proposals": self._query_proposals,
            "read_file": self._read_file,
            "submit_result": self._submit_result,
        }

        handler = dispatch.get(tool_name)
        if handler is None:
            return {"error": f"Unknown tool: {tool_name}"}

        try:
            result = handler(**arguments)
            if tool_name != "submit_result" and isinstance(result, str):
                result = _truncate_tool_output(result)
            elif tool_name != "submit_result" and isinstance(result, dict):
                for key in ("content", "matches"):
                    if key in result and isinstance(result[key], (str, list)):
                        raw = json.dumps(result[key], ensure_ascii=False)
                        if len(raw) > 10000:
                            result[key] = _truncate_tool_output(raw)
            return result
        except Exception as exc:
            logger.warning("Tool '%s' execution failed: %s", tool_name, exc)
            return {"error": str(exc)}

    # ── NormalizedTrace tools ─────────────────────────────────────────

    def _read_trace(
        self,
        trace_id: str,
        target: str = "overview",
        offset: int = 0,
        limit: int = 20,
    ) -> dict:
        """Read NormalizedTrace data — works on structured messages, not raw spans.

        Args:
            trace_id: Trace to read.
            target: "overview" | "messages" | "tool_calls" | "subagents"
            offset: 0-based message index offset.
            limit: Max messages to return.
        """
        idx = _find_trace(self._normalized_traces, trace_id)
        if idx is None:
            return {"error": f"Trace {trace_id} not found in normalized data"}

        nt = self._normalized_traces[idx]
        result = {"trace_id": trace_id}

        if target == "overview":
            # Return trace summary
            messages = nt.get("messages", [])
            result.update({
                "trace_id": trace_id,
                "message_count": len(messages),
                "system_prompt": str(nt.get("system_prompt", ""))[:500],
                "input_snippet": self._get_input_snippet(nt)[:300],
                "output_snippet": self._get_output_snippet(nt)[:300],
                "subagent_count": len(nt.get("subagents", [])),
                "total_tokens": nt.get("total_tokens", "N/A"),
            })

        elif target == "messages":
            messages = nt.get("messages", [])
            total = len(messages)
            page = messages[offset : offset + limit]

            # Flatten each message for LLM readability
            flat = []
            for i, msg in enumerate(page):
                flat.append({
                    "index": offset + i,
                    "role": msg.get("role", ""),
                    "content": str(msg.get("content", ""))[:2000],
                    "tool_call_count": len(msg.get("tool_calls", [])),
                })
            result.update({
                "total_messages": total,
                "offset": offset,
                "limit": limit,
                "returned": len(flat),
                "messages": flat,
            })

        elif target == "tool_calls":
            messages = nt.get("messages", [])
            tool_calls = []
            for msg in messages:
                for tc in msg.get("tool_calls", []):
                    tool_calls.append({
                        "name": tc.get("name", ""),
                        "input": str(tc.get("input", ""))[:500],
                        "output": str(tc.get("output", ""))[:500],
                        "latency": tc.get("latency"),
                    })
            total = len(tool_calls)
            page = tool_calls[offset : offset + limit]
            result.update({
                "total_tool_calls": total,
                "offset": offset,
                "limit": limit,
                "returned": len(page),
                "tool_calls": page,
            })

        elif target == "subagents":
            subagents = nt.get("subagents", [])
            total = len(subagents)
            page = subagents[offset : offset + limit]
            summaries = []
            for sa in page:
                summaries.append({
                    "name": sa.get("name", ""),
                    "mode": sa.get("mode", ""),
                    "message_count": len(sa.get("messages", [])),
                })
            result.update({
                "total_subagents": total,
                "subagents": summaries,
            })

        else:
            return {"error": f"Unknown target: {target}. Use: overview, messages, tool_calls, subagents"}

        return result

    def _search_trace(
        self,
        trace_id: str,
        pattern: str,
        max_results: int = 20,
    ) -> dict:
        """Regex search within normalized trace messages."""
        idx = _find_trace(self._normalized_traces, trace_id)
        if idx is None:
            return {"error": f"Trace {trace_id} not found"}

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return {"error": f"Invalid regex pattern: {pattern}"}

        nt = self._normalized_traces[idx]
        messages = nt.get("messages", [])
        matches = []

        for i, msg in enumerate(messages):
            searchable = (
                str(msg.get("content", ""))
                + " "
                + str(msg.get("tool_calls", ""))
            )
            if regex.search(searchable):
                matched_text = ""
                for m in regex.finditer(searchable):
                    matched_text += m.group(0)[:200] + " "
                matches.append({
                    "message_index": i,
                    "role": msg.get("role", ""),
                    "matched_text": matched_text.strip()[:500],
                })
                if len(matches) >= max_results:
                    break

        return {
            "trace_id": trace_id,
            "pattern": pattern,
            "matches": matches,
            "total_matches": len(matches),
        }

    def _list_traces(self) -> dict:
        """List all available NormalizedTrace summaries."""
        traces = []
        for nt in self._normalized_traces:
            tid = nt.get("id") or nt.get("trace_id") or "unknown"
            messages = nt.get("messages", [])
            traces.append({
                "trace_id": tid,
                "message_count": len(messages),
                "input_snippet": self._get_input_snippet(nt)[:100],
                "output_snippet": self._get_output_snippet(nt)[:100],
            })

        return {"traces": traces}

    # ── Legacy tools (from evolution.db) ──────────────────────────────

    def _query_evolve_records(self, trace_id: str) -> dict:
        """Query Proposal/Decision/Apply chain — uses store (evolution.db)."""
        if not self._store:
            return {"error": "No evolution store configured"}
        return self._store.query_by_trace_id(trace_id)

    def _query_proposals(self, batch_id: str) -> dict:
        """Query all Proposals for a batch."""
        if not self._store:
            return {"error": "No evolution store configured"}
        batch = self._store.get_batch(batch_id)
        if not batch:
            return {"error": f"Batch {batch_id} not found"}
        return batch

    # ── File tool ─────────────────────────────────────────────────────

    def _read_file(self, path: str, offset: int = 0, limit: int = 100) -> dict:
        """Read local file content with pagination and safety constraints."""
        target = Path(path)

        if self._workspace_dir:
            allowed_dirs = [
                Path(self._workspace_dir) / "evolution",
                Path(self._workspace_dir) / ".jiuwenswarm",
            ]
            if self._store:
                try:
                    allowed_dirs.append(Path(self._store._traces_db_path).parent)
                except Exception:
                    pass
            is_allowed = any(
                str(target.resolve()).startswith(str(d.resolve()))
                for d in allowed_dirs
            )
            if not is_allowed and not target.exists():
                return {"error": f"Path not in allowed directories: {path}"}

        if not target.exists():
            return {"error": f"File not found: {path}"}

        try:
            lines = target.read_text(encoding="utf-8").split("\n")
            total_lines = len(lines)
            page = lines[offset : offset + limit]
            content = "\n".join(page)
            return {
                "path": path,
                "total_lines": total_lines,
                "offset": offset,
                "limit": limit,
                "content": content,
            }
        except Exception as exc:
            return {"error": f"Failed to read file: {exc}"}

    # ── Stop signal ───────────────────────────────────────────────────

    @staticmethod
    def _submit_result(result: str) -> str:
        return "TASK_COMPLETED"

    # ── Helpers ───────────────────────────────────────────────────────

    @staticmethod
    def _get_input_snippet(nt: dict) -> str:
        input_data = nt.get("input", {})
        if isinstance(input_data, dict):
            return input_data.get("message", str(input_data))
        return str(input_data)

    @staticmethod
    def _get_output_snippet(nt: dict) -> str:
        output_data = nt.get("output", {})
        if isinstance(output_data, dict):
            return output_data.get("content", str(output_data))
        return str(output_data)
