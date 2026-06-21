# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""DiagnosisAgent read-only tool implementations.

Pluggable: PDA algorithm owns these tools. No dependency on LLMProposer
or other proposal generators. Tools only read from SqliteStore and local
files — no write capabilities.
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
    """Truncate long tool output, keeping head + tail with truncation notice.

    The truncation message hints the Agent to use offset/limit for targeted
    re-reading — truncation is not data loss, it's a navigation hint.
    """
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


# ── Tool executor ────────────────────────────────────────────────────────


class DiagnosisToolExecutor:
    """Dispatches tool calls to the appropriate method.

    Each tool receives a store (SqliteStore) for data access.
    All methods return dicts that are serialized back to the LLM.
    """

    def __init__(self, store: Any, workspace_dir: str | None = None):
        self._store = store
        self._workspace_dir = workspace_dir

    def execute(self, tool_name: str, arguments: dict) -> dict:
        """Dispatch a tool call by name."""
        dispatch = {
            "read_spans": self._read_spans,
            "search_spans": self._search_spans,
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
            # Truncate if needed (except submit_result)
            if tool_name != "submit_result" and isinstance(result, str):
                result = _truncate_tool_output(result)
            elif tool_name != "submit_result" and isinstance(result, dict):
                # Truncate the 'spans' or 'content' fields if they exist
                for key in ("spans", "content", "matches"):
                    if key in result and isinstance(result[key], (str, list)):
                        raw = json.dumps(result[key], ensure_ascii=False)
                        if len(raw) > 10000:
                            result[key] = _truncate_tool_output(raw)
            return result
        except Exception as exc:
            logger.warning("Tool '%s' execution failed: %s", tool_name, exc)
            return {"error": str(exc)}

    # ── Tool implementations ──────────────────────────────────────────

    def _read_spans(
        self,
        trace_id: str,
        offset: int = 0,
        limit: int = 50,
        name_filter: str = "",
    ) -> dict:
        """Read OTEL spans for trace_id from traces.db with pagination."""
        spans = self._store.read_spans(trace_id)
        total_spans = len(spans)

        # Apply name filter
        if name_filter:
            try:
                pattern = re.compile(name_filter, re.IGNORECASE)
                spans = [s for s in spans if pattern.search(s.get("name", ""))]
            except re.error:
                pass  # Invalid regex — skip filter

        # Pagination
        page = spans[offset : offset + limit]

        # Serialize each span's attributes/events from JSON strings
        serialized = []
        for s in page:
            entry = dict(s)
            for key in ("attributes", "events", "resource"):
                raw = entry.get(key)
                if isinstance(raw, str):
                    try:
                        entry[key] = json.loads(raw)
                    except (json.JSONDecodeError, TypeError):
                        entry[key] = raw
            serialized.append(entry)

        return {
            "trace_id": trace_id,
            "total_spans": total_spans,
            "offset": offset,
            "limit": limit,
            "returned": len(serialized),
            "spans": serialized,
        }

    def _search_spans(
        self,
        trace_id: str,
        pattern: str,
        max_results: int = 20,
    ) -> dict:
        """Regex search within spans for targeted lookups."""
        spans = self._store.read_spans(trace_id)
        matches = []

        try:
            regex = re.compile(pattern, re.IGNORECASE)
        except re.error:
            return {"error": f"Invalid regex pattern: {pattern}"}

        for i, span in enumerate(spans):
            searchable = (
                span.get("name", "")
                + " "
                + str(span.get("attributes", ""))
                + " "
                + str(span.get("events", ""))
                + " "
                + str(span.get("status_description", ""))
            )
            if regex.search(searchable):
                # Extract brief context
                matched_text = ""
                for m in regex.finditer(searchable):
                    matched_text += m.group(0)[:200] + " "

                matches.append({
                    "span_index": i,
                    "name": span.get("name", ""),
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

    def _list_traces(self, limit: int = 20, since: str = "") -> dict:
        """List recent trace_ids with summary info."""
        if since:
            trace_ids = self._store.get_trace_ids_since(since, limit=limit)
        else:
            trace_ids = self._store.get_recent_trace_ids(limit=limit)

        traces = []
        for tid in trace_ids:
            spans = self._store.read_spans(tid)
            first_name = spans[0].get("name", "N/A") if spans else "N/A"
            traces.append({
                "trace_id": tid,
                "span_count": len(spans),
                "first_span_name": first_name,
            })

        return {"traces": traces}

    def _query_evolve_records(self, trace_id: str) -> dict:
        """Query Proposal/Decision/Apply chain for trace_id."""
        return self._store.query_by_trace_id(trace_id)

    def _query_proposals(self, batch_id: str) -> dict:
        """Query all Proposals for a batch."""
        batch = self._store.get_batch(batch_id)
        if not batch:
            return {"error": f"Batch {batch_id} not found"}
        return batch

    def _read_file(self, path: str, offset: int = 0, limit: int = 100) -> dict:
        """Read local file content with pagination and safety constraints."""
        target = Path(path)

        # Safety: only allow specific directories
        if self._workspace_dir:
            allowed_dirs = [
                Path(self._workspace_dir) / "evolution",
                Path(self._workspace_dir) / ".jiuwenswarm",
            ]
            # Also allow the data_dir where traces.db lives
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

    def _submit_result(self, result: str) -> str:
        """Stop tool — submit final JSON result and terminate ReAct loop."""
        return "TASK_COMPLETED"
