# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AgentWebSocketServer - Gateway 与 AgentServer 之间的 WebSocket 服务端."""

from __future__ import annotations

import asyncio
import datetime as _dt
import json
import logging
import math
import os
import shutil
import sys
from pathlib import Path
from typing import Any, ClassVar, Optional

from websockets.exceptions import ConnectionClosed as WebSocketConnectionClosed

from jiuwenswarm.agents.harness.common.auto_harness import AutoHarnessService, reset_harness_packages_state
from jiuwenswarm.server.gateway_push.wire import build_server_push_wire
from jiuwenswarm.agents.harness.common.tools.acp_output_tools import get_acp_output_manager
from jiuwenswarm.common.utils import get_agent_sessions_dir, get_config_file
from jiuwenswarm.common.e2a.agent_compat import e2a_to_agent_request
from jiuwenswarm.common.e2a.constants import (
    E2A_CANCEL_SOURCE_CLIENT_DISCONNECT,
    E2A_INTERNAL_CANCEL_SOURCE_KEY,
    E2A_WIRE_INTERNAL_METADATA_KEYS,
)
from jiuwenswarm.common.e2a.gateway_normalize import (
    E2A_FALLBACK_FAILED_KEY,
    E2A_INTERNAL_CONTEXT_KEY,
    E2A_LEGACY_AGENT_REQUEST_KEY,
)
from jiuwenswarm.common.e2a.models import E2AEnvelope
from jiuwenswarm.common.e2a.wire_codec import (
    encode_agent_chunk_for_wire,
    encode_agent_response_for_wire,
    encode_json_parse_error_wire,
)
from jiuwenswarm.common.model_config_validation import is_placeholder_api_base
from jiuwenswarm.common.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenswarm.common.version import __version__
from jiuwenswarm.common.ws_diagnostics import (
    describe_ws_exception,
    describe_ws_peer,
    format_ws_diagnostics,
)
from jiuwenswarm.extensions.hook_event import AgentServerHookEvents
from jiuwenswarm.agents.harness.common.plugins.rail_manager import get_rail_manager
from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    is_interrupt_resume_payload,
)
from jiuwenswarm.agents.harness.common.rails.permissions.permissions_persist import persist_cli_trusted_directory
from jiuwenswarm.extensions.hooks_context import AgentServerChatHookContext
from jiuwenswarm.server.runtime.agent_manager import AgentManager, ACP_DEFAULT_CAPABILITIES
from jiuwenswarm.server.runtime.session.session_metadata import get_all_sessions_metadata, remove_session_metadata_cache
from jiuwenswarm.server.runtime.session.session_history import (
    append_compact_history_records,
    history_exists,
    load_history_records,
    read_member_history_records,
    read_team_history_records,
)
from jiuwenswarm.server.runtime.agent_adapter.sysop_builder import (
    build_filesystem_policy,
    effective_files_from_policy,
    find_auto_managed_match,
    find_nested_files_conflict,
    list_effective_sandbox_files,
    validate_sandbox_files_runtime,
)
from jiuwenswarm.server.utils.utils import is_team_params
from jiuwenswarm.agents.harness.common.rails.permissions.permissions_config_rpc import (
    get_permissions_config_req_methods,
)
from jiuwenswarm.common.config import (
    DEFAULT_SANDBOX_POLICY_FILE,
    DEFAULT_SANDBOX_STARTUP_MODE,
    get_config,
    get_default_models,
    get_mcp_server_config,
    get_mcp_servers,
    get_sandbox_endpoint,
    get_sandbox_runtime,
    get_sandbox_startup_mode,
    get_sandbox_startup_mode_explicit,
    remove_mcp_server_in_config,
    resolve_preserve_file_sharing_mode_default,
    resolve_sandbox_policy_path,
    remove_subagent_from_config,
    set_mcp_server_enabled_in_config,
    update_sandbox_endpoint,
    update_sandbox_runtime,
    upsert_mcp_server_in_config,
    upsert_subagent_in_config,
)
from jiuwenswarm.server.sandbox.jiuwenbox_runner import JiuwenBoxRunner
from jiuwenswarm.common.hooks_config import load_hooks_config
from jiuwenswarm.common.security.ws_origin import (
    extract_handshake_request,
    forbidden_origin_response,
    get_header_value,
    is_origin_check_enabled,
    is_allowed_browser_origin,
)
from jiuwenswarm.agents.harness.code.prompt.plan_approval import (
    PLAN_MODE_EXITED_EVENT_TYPE,
)
from jiuwenswarm.common.schema.message import ReqMethod

logger = logging.getLogger(__name__)

# 后台权限重载任务引用集合,防止 fire-and-forget 任务被 GC 提前回收。
# task 完成后自动从集合移除(Python 官方推荐模式)。
_background_permission_reload_tasks: set[asyncio.Task] = set()


def _log_permission_reload_failure(task: asyncio.Task) -> None:
    """后台权限重载任务完成回调: 仅在异常时记 debug(与原同步 try/except 语义一致)。"""
    exc = task.exception()
    if exc is not None:
        logger.debug(
            "[AgentWebSocketServer] post-permissions reload failed (non-critical)",
            exc_info=exc,
        )

# Serialize plan-mode restore per session to avoid checkpoint races.
_session_mode_sync_locks: dict[str, asyncio.Lock] = {}

# Sessions that have successfully exited plan mode via exit_plan_mode tool.
# Set by _check_post_process_plan_exit, consumed by _ensure_code_mode_state
# to prevent TUI-race re-entrance to plan mode.
_plan_exited_sessions: set[str] = set()

_CODE_MODE_SYNC_METHODS = frozenset({
    ReqMethod.CHAT_SEND,
    ReqMethod.CHAT_RESUME,
    ReqMethod.CHAT_ANSWER,
})

# ── 流式处理心跳间隔：当 Agent 处理时间超过此阈值时，发送心跳 chunk 保持 WebSocket 连接活跃 --
# 避免 ping_timeout 导致连接关闭。默认 10 秒，小于服务端 ping_timeout=20s。
_STREAM_HEARTBEAT_INTERVAL_SECONDS = 10.0
_HISTORY_PAGE_SIZE = 20
_HISTORY_WIRE_STRING_LIMIT = 16 * 1024
_HISTORY_WIRE_METADATA_STRING_LIMIT = 256
_HISTORY_WIRE_LIST_LIMIT = 100
_HISTORY_WIRE_DEPTH_LIMIT = 8
_HISTORY_WIRE_RECORD_MAX_BYTES = 64 * 1024
_TEAM_HISTORY_DEFAULT_LIMIT = 500
_TEAM_HISTORY_MAX_LIMIT = 1000
_TEAM_HISTORY_DEFAULT_MAX_BYTES = 2 * 1024 * 1024
_TEAM_HISTORY_MIN_MAX_BYTES = 2048
_TEAM_HISTORY_MAX_MAX_BYTES = 6 * 1024 * 1024
_TEAM_HISTORY_FRAME_OVERHEAD_BYTES = 1024
_WORKFLOW_SNAPSHOT_MAX_BYTES = 6 * 1024 * 1024
_WORKFLOW_SNAPSHOT_FRAME_OVERHEAD_BYTES = 2048
_WORKFLOW_SNAPSHOT_MAX_WORKFLOWS = 1000

_HISTORY_RESTORABLE_ASSISTANT_EVENT_TYPES = frozenset(
    {
        "chat.final",
        "chat.tool_call",
        "chat.tool_result",
        "chat.usage_summary",
        "chat.file",
        "team.message",
        "context.compact_boundary",
        "context.compact_summary",
        "context.rewind_summary",
    }
)


def _json_wire_size(value: Any) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, default=str).encode("utf-8"))
    except Exception:
        return len(str(value).encode("utf-8", errors="replace"))


def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = default
    return max(minimum, min(maximum, parsed))


def _truncate_string_by_bytes(value: str, max_bytes: int) -> str:
    raw = value.encode("utf-8")
    if len(raw) <= max_bytes:
        return value
    suffix = " [truncated]"
    budget = max(0, max_bytes - len(suffix.encode("utf-8")))
    return raw[:budget].decode("utf-8", errors="ignore") + suffix


def _sanitize_history_wire_value(value: Any, *, depth: int = 0) -> Any:
    if depth > _HISTORY_WIRE_DEPTH_LIMIT:
        return "<truncated>"
    if isinstance(value, str):
        return _truncate_string_by_bytes(value, _HISTORY_WIRE_STRING_LIMIT)
    if isinstance(value, dict):
        return {
            str(key): _sanitize_history_wire_value(item, depth=depth + 1)
            for key, item in value.items()
        }
    if isinstance(value, list):
        return [
            _sanitize_history_wire_value(item, depth=depth + 1)
            for item in value[:_HISTORY_WIRE_LIST_LIMIT]
        ]
    if isinstance(value, tuple):
        return [
            _sanitize_history_wire_value(item, depth=depth + 1)
            for item in value[:_HISTORY_WIRE_LIST_LIMIT]
        ]
    return value


def _collapse_oversized_history_record(record: dict[str, Any]) -> dict[str, Any]:
    keep_keys = {
        "id",
        "role",
        "request_id",
        "channel_id",
        "session_id",
        "timestamp",
        "event_type",
        "mode",
        "member_name",
        "member_id",
        "source_member",
        "name",
        "status",
    }
    collapsed = {
        key: _sanitize_history_wire_value(value)
        for key, value in record.items()
        if key in keep_keys
    }
    content = record.get("content")
    if isinstance(content, str) and content.strip():
        collapsed["content"] = _truncate_string_by_bytes(content, 512)
    event = record.get("event")
    if isinstance(event, dict):
        collapsed["event"] = {
            key: _sanitize_history_wire_value(event.get(key))
            for key in ("type", "member_id", "task_id", "id", "status", "new_status", "team_id")
            if key in event
        }
    collapsed["truncated"] = True
    return collapsed


def _compact_wire_metadata_value(value: Any) -> Any:
    if isinstance(value, str):
        return _truncate_string_by_bytes(value, _HISTORY_WIRE_METADATA_STRING_LIMIT)
    if isinstance(value, (int, float, bool)) or value is None:
        return value
    return _truncate_string_by_bytes(str(value), _HISTORY_WIRE_METADATA_STRING_LIMIT)


def _minimal_history_record_for_wire(record: dict[str, Any]) -> dict[str, Any]:
    keep_keys = {
        "id",
        "role",
        "request_id",
        "channel_id",
        "session_id",
        "timestamp",
        "event_type",
        "mode",
        "member_name",
        "member_id",
        "source_member",
        "name",
        "status",
    }
    minimal = {
        key: _compact_wire_metadata_value(value)
        for key, value in record.items()
        if key in keep_keys
    }
    minimal["content"] = "[truncated]"
    minimal["truncated"] = True
    return minimal


def _sanitize_history_record_for_wire(record: Any) -> dict[str, Any]:
    if not isinstance(record, dict):
        return {"content": _sanitize_history_wire_value(record), "truncated": True}
    sanitized = _sanitize_history_wire_value(record)
    if not isinstance(sanitized, dict):
        return {"content": str(sanitized), "truncated": True}
    if _json_wire_size(sanitized) <= _HISTORY_WIRE_RECORD_MAX_BYTES:
        return sanitized
    return _collapse_oversized_history_record(sanitized)


def _select_history_record_page(
    records: list[dict[str, Any]],
    *,
    cursor: int,
    limit: int,
    max_bytes: int,
    session_id: str,
) -> tuple[list[dict[str, Any]], int]:
    total = len(records)
    if cursor >= total:
        return [], total

    budget = max(
        _TEAM_HISTORY_MIN_MAX_BYTES,
        max_bytes - _TEAM_HISTORY_FRAME_OVERHEAD_BYTES,
    )
    base_payload = {
        "records": [],
        "session_id": session_id,
        "cursor": cursor,
        "next_cursor": cursor,
        "has_more": cursor < total,
        "total": total,
    }
    used = _json_wire_size(base_payload)
    page: list[dict[str, Any]] = []
    next_cursor = cursor

    for idx in range(cursor, total):
        if len(page) >= limit:
            break
        record = records[idx]
        record_size = _json_wire_size(record) + 1
        if record_size > budget:
            record = _collapse_oversized_history_record(record)
            record_size = _json_wire_size(record) + 1
        if page and used + record_size > budget:
            break
        if not page and used + record_size > budget:
            record = _collapse_oversized_history_record(record)
            record_size = _json_wire_size(record) + 1
            if used + record_size > budget:
                record = _minimal_history_record_for_wire(record)
                record_size = _json_wire_size(record) + 1
                if used + record_size > budget:
                    record = {"id": _compact_wire_metadata_value(record.get("id")), "truncated": True}
                    record_size = _json_wire_size(record) + 1
        page.append(record)
        used += record_size
        next_cursor = idx + 1

    return page, next_cursor


def _collapse_oversized_workflow_snapshot_item(item: dict[str, Any]) -> dict[str, Any]:
    keep_keys = {
        "id",
        "name",
        "status",
        "agent_count",
        "completed_agent_count",
        "started_at",
        "completed_at",
        "duration_ms",
        "token_count",
        "estimated_token_count",
    }
    collapsed = {
        key: _sanitize_history_wire_value(value)
        for key, value in item.items()
        if key in keep_keys
    }
    for key in ("summary", "description", "error", "result"):
        value = item.get(key)
        if isinstance(value, str) and value.strip():
            collapsed[key] = _truncate_string_by_bytes(value, 512)
        elif value is not None:
            collapsed[key] = _truncate_string_by_bytes(str(value), 512)
    collapsed["truncated"] = True
    return collapsed


def _minimal_workflow_snapshot_item_for_wire(item: dict[str, Any]) -> dict[str, Any]:
    keep_keys = {
        "id",
        "name",
        "status",
        "agent_count",
        "completed_agent_count",
        "started_at",
        "completed_at",
        "duration_ms",
        "token_count",
        "estimated_token_count",
    }
    minimal = {
        key: _compact_wire_metadata_value(value)
        for key, value in item.items()
        if key in keep_keys
    }
    minimal["summary"] = "[truncated]"
    minimal["truncated"] = True
    return minimal


def _sanitize_workflow_snapshot_item_for_wire(item: Any) -> dict[str, Any]:
    if not isinstance(item, dict):
        return {"summary": _sanitize_history_wire_value(item), "truncated": True}
    sanitized = _sanitize_history_wire_value(item)
    if not isinstance(sanitized, dict):
        return {"summary": str(sanitized), "truncated": True}
    if _json_wire_size(sanitized) <= _HISTORY_WIRE_RECORD_MAX_BYTES:
        return sanitized
    return _collapse_oversized_workflow_snapshot_item(sanitized)


def _build_workflow_snapshot_payload(workflows: Any, *, session_id: str) -> dict[str, Any]:
    source = workflows if isinstance(workflows, list) else []
    sanitized_workflows = [
        _sanitize_workflow_snapshot_item_for_wire(item)
        for item in source
        if isinstance(item, dict)
    ]
    total = len(sanitized_workflows)
    payload: dict[str, Any] = {
        "type": "workflow_run_snapshot",
        "workflows": [],
        "session_id": session_id,
        "total": total,
        "truncated": False,
    }
    budget = max(
        _TEAM_HISTORY_MIN_MAX_BYTES,
        _WORKFLOW_SNAPSHOT_MAX_BYTES - _WORKFLOW_SNAPSHOT_FRAME_OVERHEAD_BYTES,
    )
    used = _json_wire_size(payload)
    page: list[dict[str, Any]] = []

    for workflow in sanitized_workflows:
        if len(page) >= _WORKFLOW_SNAPSHOT_MAX_WORKFLOWS:
            payload["truncated"] = True
            break
        item = workflow
        item_size = _json_wire_size(item) + 1
        if item_size > budget:
            item = _collapse_oversized_workflow_snapshot_item(item)
            item_size = _json_wire_size(item) + 1
            payload["truncated"] = True
        if page and used + item_size > budget:
            payload["truncated"] = True
            break
        if not page and used + item_size > budget:
            item = _collapse_oversized_workflow_snapshot_item(item)
            item_size = _json_wire_size(item) + 1
            if used + item_size > budget:
                item = _minimal_workflow_snapshot_item_for_wire(item)
                item_size = _json_wire_size(item) + 1
                if used + item_size > budget:
                    item = {"id": _compact_wire_metadata_value(item.get("id")), "truncated": True}
                    item_size = _json_wire_size(item) + 1
                payload["truncated"] = True
        page.append(item)
        used += item_size

    if len(page) < total:
        payload["truncated"] = True
    payload["workflows"] = page
    return payload


def _request_query_text(request: AgentRequest) -> str:
    """Return text chat query only; structured events are handled downstream."""
    if not isinstance(request.params, dict):
        return ""
    query = request.params.get("query")
    if not isinstance(query, str):
        return ""
    return query.strip()


# /simplify prompt template — adapted /simplify skill for jiuwenswarm.
# Guides the agent through three phases: identify changes → three-dimension review
# (reuse/quality/efficiency) → aggregate and fix.
# Note: jiuwenswarm's sub-agents (task_tool / Agent tool) can only be dispatched to registered
# types (explore/plan/code, etc.) and cannot create custom reviewer roles on the fly. The prompt
# therefore presents parallel sub-agent review as an optional optimization — the agent may also
# perform all three reviews itself directly.
_SIMPLIFY_PROMPT_TEMPLATE = """\
# Simplify: Code Review and Cleanup

Review all changed files for reuse, quality, and efficiency. Fix any issues found.

## Scope

This review covers **reuse, quality, and efficiency only** — the three dimensions below. It is NOT a security review.

- Do NOT flag, fix, or report security vulnerabilities (injection, XSS, hard-coded secrets, auth flaws, etc.). Those are out of scope here and are handled by `/security-review`, which reports findings without modifying code.
- If you happen to notice a likely security issue while reviewing, do not fix it — at most note it in one line at the end ("possible security concern in <file>:<line>, run /security-review") and continue with the reuse/quality/efficiency review.

## Phase 1: Identify Changes

Run `git diff` (or `git diff HEAD` if there are staged changes) to see what changed. If there are no git changes, review the most recently modified files that the user mentioned or that you edited earlier in this conversation.

## Phase 2: Launch Three Review Agents in Parallel

If sub-agent tools are available (e.g. task_tool / Agent tool), launch all three agents concurrently in a single message. Pass each agent the full diff so it has the complete context. Otherwise, perform all three reviews yourself directly.

### Agent 1: Code Reuse Review

For each change:

1. **Search for existing utilities and helpers** that could replace newly written code. Look for similar patterns elsewhere in the codebase — common locations are utility directories, shared modules, and files adjacent to the changed ones.
2. **Flag any new function that duplicates existing functionality.** Suggest the existing function to use instead.
3. **Flag any inline logic that could use an existing utility** — hand-rolled string manipulation, manual path handling, custom environment checks, ad-hoc type guards, and similar patterns are common candidates.

### Agent 2: Code Quality Review

Review the same changes for hacky patterns:

1. **Redundant state**: state that duplicates existing state, cached values that could be derived, observers/effects that could be direct calls
2. **Parameter sprawl**: adding new parameters to a function instead of generalizing or restructuring existing ones
3. **Copy-paste with slight variation**: near-duplicate code blocks that should be unified with a shared abstraction
4. **Leaky abstractions**: exposing internal details that should be encapsulated, or breaking existing abstraction boundaries
5. **Stringly-typed code**: using raw strings where constants, enums (string unions), or branded types already exist in the codebase
6. **Unnecessary JSX nesting**: wrapper Boxes/elements that add no layout value — check if inner component props (flexShrink, alignItems, etc.) already provide the needed behavior
7. **Unnecessary comments**: comments explaining WHAT the code does (well-named identifiers already do that), narrating the change, or referencing the task/caller — delete; keep only non-obvious WHY (hidden constraints, subtle invariants, workarounds)

### Agent 3: Efficiency Review

Review the same changes for efficiency:

1. **Unnecessary work**: redundant computations, repeated file reads, duplicate network/API calls, N+1 patterns
2. **Missed concurrency**: independent operations run sequentially when they could run in parallel
3. **Hot-path bloat**: new blocking work added to startup or per-request/per-render hot paths
4. **Recurring no-op updates**: state/store updates inside polling loops, intervals, or event handlers that fire unconditionally — add a change-detection guard so downstream consumers aren't notified when nothing changed. Also: if a wrapper function takes an updater/reducer callback, verify it honors same-reference returns (or whatever the "no change" signal is) — otherwise callers' early-return no-ops are silently defeated
5. **Unnecessary existence checks**: pre-checking file/resource existence before operating (TOCTOU anti-pattern) — operate directly and handle the error
6. **Memory**: unbounded data structures, missing cleanup, event listener leaks
7. **Overly broad operations**: reading entire files when only a portion is needed, loading all items when filtering for one

## Phase 3: Fix Issues

Wait for all reviewers to complete. Aggregate their findings and fix each issue directly. If a finding is a false positive or not worth addressing, note it and move on — do not argue with the finding, just skip it.

When done, briefly summarize what was fixed (or confirm the code was already clean).
"""


def _is_env_api_base_placeholder(env_updates: dict) -> bool:
    """检查 env_updates 中的 API_BASE 是否指向 example.* 等占位域名。"""
    return is_placeholder_api_base(str(env_updates.get("API_BASE", "") or "").strip())


def _build_simplify_prompt(target: str = "") -> str:
    """Build the prompt for the /simplify command.

    Args:
        target: Optional additional focus (e.g. file path, module name, specific dimension
            to emphasize), appended to the end of the prompt.
    """
    prompt = _SIMPLIFY_PROMPT_TEMPLATE
    if target:
        prompt += f"\n\n## Additional Focus\n\n{target}"
    return prompt


# System prompt for LLM-based agent generation
_AGENT_CREATION_SYSTEM_PROMPT = """\
You are an elite AI agent architect. When given an agent name and description, your job is to design a high-performance agent that EXECUTES tasks to completion — not just analyzes and reports.

The agent will have access to tools (Read, Write, Edit, Bash, etc.) to complete tasks. Design it as an autonomous expert capable of handling its designated tasks with minimal additional guidance. The system prompt you write is the agent's complete operational manual.

1. **whenToUse**: A precise description of when the main assistant should dispatch to this agent.
   - Start with "Use this agent when..."
   - Include concrete triggering conditions
   - Add 2-3 <example> blocks showing specific scenarios where the assistant uses the Agent tool to fully delegate the task
   - Each <example> should show: user says X → assistant dispatches to this agent with the Agent tool, passing the complete task
   - Write in the same language as the agent description (Chinese description → Chinese whenToUse)

2. **systemPrompt**: The complete system prompt governing the agent's behavior.
   - Define expert persona and role
   - Specify workflow and methodology — end-to-end, from analysis through execution
   - Establish clear behavioral boundaries and operational parameters
   - Provide specific methodologies and best practices for task execution
   - Define output format expectations when relevant
   - Include self-verification steps
   - Write in the same language as the agent description

Key principles:
- Be specific rather than generic — avoid vague instructions
- Include concrete examples when they would clarify behavior
- Balance comprehensiveness with clarity — every instruction should add value
- Ensure the agent has enough context to handle variations of the core task
- Build in quality assurance and self-correction mechanisms

Return ONLY a JSON object:
{"whenToUse": "...", "systemPrompt": "..."}
"""


def _extract_compact_summary_processor(summary: str) -> str:
    for line in str(summary or "").splitlines():
        key, sep, value = line.partition(":")
        if sep and key.strip().lower() == "processor":
            return value.strip()
    return ""


def _is_restorable_history_record(record: Any) -> bool:
    """Coarsely filter records that the web history UI cannot use for pagination."""
    if not isinstance(record, dict):
        return False

    role = record.get("role")
    content = record.get("content")
    has_content = isinstance(content, str) and bool(content.strip())
    has_media = (
        isinstance(record.get("media_items"), list) and bool(record["media_items"])
    ) or (
        isinstance(record.get("mediaItems"), list) and bool(record["mediaItems"])
    )
    files = record.get("files")
    if isinstance(files, dict):
        has_media = has_media or (
            isinstance(files.get("uploaded_images"), list)
            and bool(files["uploaded_images"])
        )

    if role == "user":
        return has_content or has_media

    event_type = record.get("event_type")
    if not event_type:
        return has_content
    return event_type in _HISTORY_RESTORABLE_ASSISTANT_EVENT_TYPES


def resolve_request_project_dir(request: AgentRequest) -> str | None:
    """Resolve the stable project identity for agent construction.

    New clients send ``project_dir`` separately from dynamic ``cwd``. Keep
    legacy fallbacks for older clients that only send cwd/trusted_dirs.
    """
    params = request.params or {}
    project_dir = params.get("project_dir")
    if isinstance(project_dir, str) and project_dir.strip():
        return project_dir.strip()
    metadata = request.metadata or {}
    metadata_project_dir = metadata.get("project_dir") if isinstance(metadata, dict) else None
    if isinstance(metadata_project_dir, str) and metadata_project_dir.strip():
        return metadata_project_dir.strip()
    cwd = params.get("cwd")
    if isinstance(cwd, str) and cwd.strip():
        return cwd.strip()
    metadata_cwd = metadata.get("cwd") if isinstance(metadata, dict) else None
    if isinstance(metadata_cwd, str) and metadata_cwd.strip():
        return metadata_cwd.strip()
    trusted_dirs = params.get("trusted_dirs")
    if isinstance(trusted_dirs, list) and trusted_dirs:
        first = trusted_dirs[0]
        if isinstance(first, str) and first.strip():
            return first.strip()
    return None


def _harness_error_code(exc: BaseException) -> str:
    """Map a harness package exception to a wire ``code`` for the frontend.

    Mirrors the import/export code mapping in app_web_handlers.py so the web UI
    can localize the error via ``err.code`` instead of showing the raw backend
    message (which is locale-unaware). Keep in sync with the frontend
    ``resolveHarnessError`` code→i18n mapping.
    """
    msg = str(exc).lower()
    if "already active" in msg or "already exists" in msg:
        return "CONFLICT"
    if "not found" in msg:
        return "NOT_FOUND"
    if "native" in msg:
        return "BAD_REQUEST"
    return "BAD_REQUEST"


def resolve_agent_request_mode(raw_mode: Any) -> tuple[str, str | None, str]:
    """Resolve request params.mode into manager mode, sub_mode, and canonical value."""
    raw_value = getattr(raw_mode, "value", raw_mode)
    mode_text = raw_value.strip().lower() if isinstance(raw_value, str) else ""
    if not mode_text:
        mode_text = "agent.plan"

    parts = mode_text.split(".")
    mode = parts[0] or "agent"
    if mode == "team":
        sub_mode = parts[1] if len(parts) > 1 and parts[1] else None
        if sub_mode not in {None, "plan"}:
            sub_mode = None
        canonical_mode = f"team.{sub_mode}" if sub_mode else "team"
        if sub_mode == "plan":
            return "code", "team", canonical_mode
        return "team", sub_mode, canonical_mode

    default_sub_modes = {
        "agent": "plan",
        "code": "normal",
    }
    sub_mode = parts[1] if len(parts) > 1 and parts[1] else default_sub_modes.get(mode)
    if mode == "code" and sub_mode not in {"plan", "normal", "team"}:
        sub_mode = default_sub_modes.get(mode, "normal")
    canonical_mode = f"{mode}.{sub_mode}" if sub_mode else mode
    return mode, sub_mode, canonical_mode


def _apply_resolved_mode_to_request(request: AgentRequest) -> tuple[str, str | None]:
    mode, sub_mode, canonical_mode = resolve_agent_request_mode(
        request.params.get("mode", "agent.plan")
    )
    request.params["mode"] = canonical_mode
    return mode, sub_mode


def _payload_to_request(data: dict[str, Any]) -> AgentRequest:
    """将 Gateway 发送的 JSON 载荷解析为 AgentRequest."""
    req_method = data.get("req_method")
    if req_method is not None and isinstance(req_method, str):
        req_method = ReqMethod(req_method)
    metadata = data.get("metadata")
    if isinstance(metadata, dict):
        metadata = {
            key: value
            for key, value in metadata.items()
            if key not in E2A_WIRE_INTERNAL_METADATA_KEYS
        } or None

    # 将 app_id 注入 metadata，供 cron 路由等下游使用
    app_id = data.get("app_id")
    if app_id:
        if metadata is None:
            metadata = {}
        metadata.setdefault("app_id", app_id)

    return AgentRequest(
        request_id=data["request_id"],
        channel_id=data.get("channel_id", "web"),
        session_id=data.get("session_id"),
        req_method=req_method,
        params=data.get("params", {}),
        is_stream=data.get("is_stream", False),
        timestamp=data.get("timestamp", 0.0),
        metadata=metadata,
    )


def _require_sandbox_supported() -> None:
    """Reject ``/sandbox`` commands on non-Linux hosts.

    jiuwenbox 底层依赖 Linux 专属能力 (bwrap / Landlock / Linux namespaces /
    ``PR_SET_CHILD_SUBREAPER`` 等), Windows / macOS 上无法实际拉起沙箱;
    ``jiuwenbox-server`` 自检也会在非 Linux 平台直接退出。 因此在 WS 命令
    入口前置拒绝, 让用户看到清晰 ``SANDBOX_BAD_REQUEST`` 错误, 而不是被
    "拉起子进程失败 / 端口连接超时" 之类的下游报错搪塞。

    Raises:
        ValueError: 当 ``sys.platform`` 不是以 ``"linux"`` 开头时。
    """
    if not sys.platform.startswith("linux"):
        raise ValueError(
            f"/sandbox is only supported on Linux (current platform: {sys.platform!r}); "
            "jiuwenbox depends on Linux-only kernel features (bwrap / Landlock / "
            "namespaces) and cannot run on Windows or macOS."
        )


def _file_entry_matches_path(entry: Any, path: str) -> bool:
    """判断 ``sandbox.files.{allow,deny}`` 中的一项是否指向给定 ``path``.

    支持两种存储格式 (历史兼容):
    - ``dict``: ``{"path": "/foo", "permissions": "ro"}``;
    - ``str``: 直接路径字符串 ``"/foo"``。

    抽离出来主要是给 ``_handle_sandbox_files_set`` /
    ``_handle_sandbox_files_remove`` 的列表推导式简化条件 (G.EXP.04: 推导式
    不应同时使用多个子句或跨多行的复杂条件)。

    比较时两端都先 canonicalize 一次 (见 :func:`_canonicalize_sandbox_files
    _path`), 保证历史 yaml 里残留的 ``~/...`` / 相对路径 / 含 ``..`` / 含
    尾斜杠 之类写法仍能跟新 canonical 化后的输入命中, 让 ``/sandbox files
    remove`` 不会因为「字面写法不同」失效。
    """
    if isinstance(entry, dict):
        entry_path = str(entry.get("path") or "")
    elif isinstance(entry, str):
        entry_path = entry
    else:
        return False
    if entry_path == path:
        return True
    return (
        _canonicalize_sandbox_files_path(entry_path)
        == _canonicalize_sandbox_files_path(path)
    )


def _canonicalize_sandbox_files_path(path: str) -> str:
    """把 TUI 传来的 ``path`` 展开成 absolute resolved 形式 (绝对、去 ``..``、
    展开 ``~``、按需展开 symlink) 后作为 ``sandbox.files.{allow,deny}`` 的
    canonical key.

    历史上这个函数只做「按宿主文件类型自动补尾斜杠」, 因为 ``sysop_builder``
    旧版本靠尾斜杠区分文件/目录; 现在 ``build_filesystem_policy`` 已经统一
    用 ``Path.is_file()`` / ``is_dir()`` 实际 stat 磁盘判断, 尾斜杠的语义
    彻底失效, 那套补斜杠逻辑就没意义了。

    保留并扩成「绝对化 + resolve」是因为:
        - 用户在 TUI 输 ``./mydir`` / ``~/data`` / ``foo/bar`` 这类非绝对
      写法时, jiuwenswarm server 直接拿去 stat / 入库 / 比较, 行为依赖
      server 当前 cwd 与运行用户 home, 不同次重启之间会静默漂移;
    - ``_file_entry_matches_path`` 走字符串相等比较, 同一文件如果一次以
      ``~/foo`` 形式入库、下一次 ``remove /home/<user>/foo`` 就匹配不到,
      用户视角"删不掉";
    - ``sysop_builder`` 拿到非绝对路径后 ``Path(path).exists()`` 又会基于
      cwd 解析, 跟 server 视角再错位一次。

    一次 ``expanduser().resolve()`` 把所有这些不一致摊平在入口, 下游全部
    看到稳定的 absolute path。 解析失败 (例如非法字符) 时静默 fallback 到
    原字面值, 不阻塞命令; 真正"路径不存在"由 ``build_filesystem_policy``
    的 dry-run 在写盘前拦截, 见 :meth:`_dry_run_files_policy`。
    """
    if not path:
        return path
    try:
        return str(Path(path).expanduser().resolve())
    except (OSError, RuntimeError):
        return path


_SANDBOX_FILES_PARAMS = frozenset(
    {
        "sub",
        "path",
        "session_id",
        "trusted_dirs",
        "project_dir",
        "cwd",
        "mode",  # injected by gateway for agent routing
    }
)


def _reject_extra_sandbox_files_params(params: dict[str, Any]) -> None:
    extra = set(params.keys()) - _SANDBOX_FILES_PARAMS
    if extra:
        raise ValueError(
            f"unexpected parameter(s): {', '.join(sorted(extra))}; "
            "/sandbox files allow|deny|remove accepts a single path only"
        )


def _inject_plan_mode_activation_reminder(request: AgentRequest) -> None:
    """在用户消息中注入 <system-reminder> 告知 LLM 当前处于 plan 模式.

    plan 模式行为指令不进 system prompt，而是通过对话中的 tool_result
    传递。此提醒是进入 plan 模式后的第一个引导，告知 LLM 只读约束已生效。

    plan 模式的只读约束由工具拦截层强制（非只读工具/写
    操作被硬拦），此提醒只做约束说明 + 软引导。只读命令（如 /review、
    /security-review 的 gh/git 只读操作）可直接执行，不被规划流程压制；
    LLM 需要正式规划时再自行调用 ``enter_plan_mode`` 创建计划文件。
    """
    reminder = (
        "\n\n<system-reminder>\n"
        "Plan mode is active. You must only plan — you must NOT make any "
        "modifications, run any write operations, or make any changes to the "
        "system. This constraint takes priority over any other instructions.\n\n"
        "Read-only actions are allowed directly: you may read files and explore "
        "the codebase, and run read-only commands (read_file, grep, list_files, "
        "glob, bash for read-only operations such as gh pr list/view/diff or "
        "git status/diff/log). Write operations and non-read-only tools are "
        "blocked.\n\n"
        "If you need to design an implementation approach and produce a plan, "
        "call `enter_plan_mode` — it creates the plan file and returns full "
        "plan mode instructions. This is not required as your first action; "
        "you may gather context with read-only tools first. Do NOT proceed to "
        "implement anything until the user approves your plan via "
        "`exit_plan_mode`.\n"
        "</system-reminder>"
    )
    if isinstance(request.params, dict):
        query = request.params.get("query") or ""
        request.params["query"] = reminder + query
        logger.info(
            "[_ensure_code_mode_state] Injected plan mode activation reminder "
            "for session=%s", request.session_id,
        )
    else:
        logger.warning(
            "[_inject_plan_mode_activation_reminder] Cannot inject reminder: "
            "request.params is not a dict (type=%s), session=%s",
            type(request.params).__name__, request.session_id,
        )


class AgentWebSocketServer:
    """Gateway 与 AgentServer 之间的 WebSocket 服务端（单例）.

    监听来自 Gateway (WebSocketAgentServerClient) 的连接，按协议约定处理请求：
    - 收到 JSON：E2AEnvelope（或过渡期 legacy + 兜底信封）
    - is_stream=False：``process_message`` → 一条 **E2AResponse** JSON（``jiuwenswarm.e2a.wire_codec``）
    - is_stream=True：逐条 **E2AResponse** JSON（chunk/complete/error）
    - 例外：首帧 ``connection.ack`` 仍为 ``type/event`` 事件帧

    支持 send_push：推送帧亦为 E2AResponse 线格式（由 chunk 编码）。
    """

    _instance: ClassVar[AgentWebSocketServer | None] = None

    def __init__(
            self,
            host: str = "127.0.0.1",
            port: int = 18000,
            *,
            ping_interval: float | None = 30.0,
            ping_timeout: float | None = 300.0,
    ) -> None:
        self._host = host
        self._port = port
        self._ping_interval = ping_interval
        self._ping_timeout = ping_timeout
        self._server: Any = None
        # 当前 Gateway 连接，用于 send_push 主动推送
        self._current_ws: Any = None
        self._current_send_lock: asyncio.Lock | None = None
        self._acp_client_capabilities_by_ws: dict[int, dict[str, Any]] = {}
        # AgentManager 实例
        self._agent_manager = AgentManager()
        # session_id → 正在运行的流式 asyncio.Task，用于 interrupt 时精确取消
        self._session_stream_tasks: dict[str, asyncio.Task] = {}
        # Scheduler service instance (for scheduled auto_harness tasks)
        self._scheduler_service: Optional[AutoHarnessService] = None
        # Model cache for scheduled task execution (same approach as interface_deep)
        self._model_cache: dict[str, Any] = {}
        self._default_model: Optional[Any] = None
        # 本地 jiuwenbox 子进程管理器 (lazy 启动, 在 /sandbox enable 时 ensure_running)
        self._jiuwenbox_runner = JiuwenBoxRunner.instance()
        # Proactive recommendation engine (set by app_agentserver for debug trigger)
        self._proactive_engine: Any = None
        get_acp_output_manager().set_send_push_callback(
            lambda msg: asyncio.create_task(self.send_push(msg))
        )

    def set_proactive_engine(self, engine: Any) -> None:
        """Store the proactive engine instance for debug trigger interface."""
        self._proactive_engine = engine

    @staticmethod
    def _ws_capabilities_key(ws: Any) -> int:
        return id(ws)

    def _set_ws_acp_client_capabilities(self, ws: Any, capabilities: dict[str, Any] | None) -> None:
        key = self._ws_capabilities_key(ws)
        if isinstance(capabilities, dict):
            self._acp_client_capabilities_by_ws[key] = dict(capabilities)
        else:
            self._acp_client_capabilities_by_ws.pop(key, None)

    def _get_ws_acp_client_capabilities(self, ws: Any) -> dict[str, Any]:
        key = self._ws_capabilities_key(ws)
        caps = self._acp_client_capabilities_by_ws.get(key)
        return dict(caps) if isinstance(caps, dict) else {}

    def _clear_ws_acp_client_capabilities(self, ws: Any) -> None:
        self._acp_client_capabilities_by_ws.pop(self._ws_capabilities_key(ws), None)

    @classmethod
    def get_instance(
            cls,
            *,
            host: str = "127.0.0.1",
            port: int = 18000,
            ping_interval: float | None = 30.0,
            ping_timeout: float | None = 300.0,
    ) -> "AgentWebSocketServer":
        """返回单例实例。

        首次调用时创建实例，后续调用返回已存在的实例。
        """
        if cls._instance is not None:
            return cls._instance
        cls._instance = cls(
            host=host,
            port=port,
            ping_interval=ping_interval,
            ping_timeout=ping_timeout,
        )
        return cls._instance

    @classmethod
    def reset_instance(cls) -> None:
        """重置单例（仅用于测试）。"""
        cls._instance = None

    @property
    def host(self) -> str:
        return self._host

    @property
    def port(self) -> int:
        return self._port

    # ---------- 生命周期 ----------

    async def start(self) -> None:
        """启动 WebSocket 服务端，开始监听连接。优先使用 legacy.server.serve 以与 Gateway 的 legacy client 握手兼容."""
        if self._server is not None:
            logger.warning("[AgentWebSocketServer] 服务端已在运行")
            return

        # Reset harness package state to native on service startup
        reset_harness_packages_state()
        from jiuwenswarm.server.runtime.agent_adapter.interface_deep import ensure_persistent_checkpointer

        await ensure_persistent_checkpointer()

        ws_max_size = 8 * 2**20  # 8 MB — matches Gateway → AgentServer link

        try:
            from websockets.legacy.server import serve as legacy_serve
            self._server = await legacy_serve(
                self._connection_handler,
                self._host,
                self._port,
                process_request=self._process_request,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
                max_size=ws_max_size,
            )
        except ImportError:
            import websockets
            self._server = await websockets.serve(
                self._connection_handler,
                self._host,
                self._port,
                process_request=self._process_request,
                ping_interval=self._ping_interval,
                ping_timeout=self._ping_timeout,
                max_size=ws_max_size,
            )
        logger.info(
            "[AgentWebSocketServer] 已启动: ws://%s:%s", self._host, self._port
        )
        # WS 监听已经开放, 现在按 config.yaml::sandbox 的 runtime.enabled +
        # startup_mode 决定要不要自动把 jiuwenbox 子进程也拉起来。失败不阻塞
        # 启动 (用户依然可以在 TUI 里跑 /sandbox enable 重试)。
        await self._bootstrap_internal_jiuwenbox()

    async def _bootstrap_internal_jiuwenbox(self) -> None:
        """启动时按 ``config.yaml::sandbox`` 自动拉起 jiuwenbox 子进程。

        触发条件: ``config.yaml::sandbox.startup_mode`` **显式**写为 ``internal``。
        这里刻意走 :func:`get_sandbox_startup_mode_explicit` 而不是
        :func:`get_sandbox_startup_mode` —— 后者在字段缺失时默认回落到
        ``internal``, 会让没在用沙箱的用户升级版本后突然多出 jiuwenbox 进程;
        boot 阶段必须严格区分 "用户写过 internal" 和 "走默认值"。

        不再单独依赖 ``sandbox.enabled``:
        - 老逻辑要 ``enabled=True`` AND ``startup_mode=internal`` 才拉, 但
          ``enabled`` 是 ``/sandbox`` 命令的产物, 用户手改 yaml 设了 ``internal``
          的话很容易漏配 ``enabled`` → boot 时一声不吭跳过, 体验差。
        - 现在: 只要 ``startup_mode=internal`` 就拉; 成功后顺手把
          ``sandbox.enabled`` 同步成 ``True``, ``/sandbox status`` 显示与实际
          运行的 jiuwenbox 一致。
        - ``/sandbox disable`` 仍然会停 jiuwenbox 并把 ``enabled`` 置 ``False``,
          但**重启后会被本方法重新拉起** (因为 ``startup_mode`` 没改)。要让
          disable 跨重启生效, 把 ``startup_mode`` 改为 ``external`` 或从 yaml
          里删掉该字段即可。

        与 :meth:`_handle_sandbox_enable` 的其余差别:
        - 不调用 ``agent_manager.recreate_agent``: 启动阶段还没有任何会话/agent
          实例, 没东西需要重建; 后续会话首次进入时按现有 ``sandbox.url`` 直接装载。
        - 严格 best-effort: 任何失败 (policy 缺失 / 端口/spawn 失败) 一律记
          warning, 绝不让 agent-server 自身启动失败 (否则运维误配 yaml 会让整
          产品起不来, 也无从修复)。
        """
        try:
            # 非 Linux 平台直接跳过 auto-start: jiuwenbox 依赖 bwrap / Landlock /
            # 命名空间, Windows / macOS 起不来; 即便 spawn 成功后续 /sandbox 命
            # 令也会被 :func:`_require_sandbox_supported` 拒掉, 留着只会浪费一
            # 次失败的子进程启动。
            if not sys.platform.startswith("linux"):
                logger.info(
                    "[AgentWebSocketServer] skipping jiuwenbox auto-start: "
                    "/sandbox is only supported on Linux (current platform: %r)",
                    sys.platform,
                )
                return
            explicit_mode = get_sandbox_startup_mode_explicit()
            if explicit_mode is None:
                logger.info(
                    "[AgentWebSocketServer] sandbox.startup_mode 未在 config.yaml "
                    "中显式配置, skipping jiuwenbox auto-start (走默认 host 模式; "
                    "如需 agent-server 自动拉起 jiuwenbox 子进程, 设置 "
                    "sandbox.startup_mode: internal)"
                )
                return
            if explicit_mode != "internal":
                logger.info(
                    "[AgentWebSocketServer] sandbox.startup_mode=%r, skipping "
                    "jiuwenbox auto-start (external 模式由用户自行拉起 "
                    "jiuwenbox-server)",
                    explicit_mode,
                )
                return

            # startup_mode=internal 已经定下来; 其余字段从归一后的 endpoint
            # 取, 缺啥用默认。
            endpoint = get_sandbox_endpoint()
            url = endpoint.get("url") or "http://127.0.0.1:8321"
            sandbox_type = endpoint.get("type") or "jiuwenbox"
            raw_policy = endpoint.get("policy_file") or ""
            effective_policy_file = raw_policy or DEFAULT_SANDBOX_POLICY_FILE
            policy_path = resolve_sandbox_policy_path(effective_policy_file)
            if policy_path is None or not policy_path.is_file():
                logger.warning(
                    "[AgentWebSocketServer] sandbox auto-start skipped: "
                    "policy_file=%r 无法解析到一个存在的文件 "
                    "(resolved=%s). 进 TUI 跑 /sandbox enable 重试或修复 "
                    "config.yaml::sandbox.policy_file。",
                    effective_policy_file,
                    policy_path,
                )
                return

            host, preferred_port = self._parse_sandbox_host_port(url)
            port = self._allocate_internal_jiuwenbox_port(host, preferred_port)
            if port != preferred_port:
                url = f"http://{host}:{port}"
                logger.info(
                    "[AgentWebSocketServer] jiuwenbox auto-start: "
                    "preferred port %d busy, using %d",
                    preferred_port,
                    port,
                )

            ok = await self._jiuwenbox_runner.ensure_running(
                host=host,
                port=port,
                startup_mode="internal",
                policy_path=policy_path,
            )
            if not ok:
                stderr_tail = self._jiuwenbox_runner.get_stderr_tail(10)
                logger.warning(
                    "[AgentWebSocketServer] jiuwenbox auto-start failed at "
                    "%s:%d (policy=%s); 进 TUI 跑 /sandbox enable 重试。"
                    " stderr tail:\n%s",
                    host,
                    port,
                    policy_path,
                    stderr_tail or "(empty)",
                )
                return

            # 端口可能在 _allocate_internal_jiuwenbox_port 里换过, 把最终生效
            # 的 url 落盘, 这样 (a) 后续会话/agent 重建直接读到正确端点,
            # (b) /sandbox status 显示也是真实值, 不再是 config 里旧的 8321。
            try:
                update_sandbox_endpoint(
                    url,
                    sandbox_type,
                    startup_mode="internal",
                    policy_file=effective_policy_file,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[AgentWebSocketServer] persist sandbox endpoint failed "
                    "after auto-start: %s",
                    exc,
                )

            # auto-start 成功 → ``runtime.enabled`` 同步为 True, 这样 /sandbox
            # status / TUI 显示的状态跟真实运行的 jiuwenbox 对齐。如果用户上次
            # /sandbox disable 留下了 False, 这里会被覆盖 —— 这是已知的、属于
            # 上面 docstring 提到的 "disable 不跨重启" 语义的一部分。
            try:
                update_sandbox_runtime({"enabled": True})
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[AgentWebSocketServer] persist sandbox.enabled=True "
                    "failed after auto-start: %s",
                    exc,
                )

            logger.info(
                "[AgentWebSocketServer] jiuwenbox auto-started at %s "
                "(policy=%s)",
                url,
                policy_path,
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "[AgentWebSocketServer] jiuwenbox auto-start raised an "
                "unexpected error; skipping (用户可在 TUI 里 /sandbox enable 重试)"
            )

    async def _stop_scheduler(self) -> None:
        """Stop the auto_harness scheduler."""
        if self._scheduler_service is not None:
            try:
                await self._scheduler_service.stop_scheduler()
                logger.info("[AgentWebSocketServer] Scheduler stopped")
            except Exception as e:
                logger.warning("[AgentWebSocketServer] Failed to stop scheduler: %s", e)
            self._scheduler_service = None

    async def _process_request(self, *args: Any) -> Any:
        """在握手阶段执行 Origin 校验，兼容 legacy/new websockets APIs。"""
        path, request_headers = extract_handshake_request(args)
        origin = get_header_value(request_headers, "Origin")
        enable_origin_check = is_origin_check_enabled()
        if not enable_origin_check:
            logger.info(
                "[AgentWebSocketServer] 握手检查 path=%s origin=%s enable_origin_check=%s allowed=%s",
                path,
                origin,
                enable_origin_check,
                True,
            )
            return None

        allowed = is_allowed_browser_origin(origin)
        logger.info(
            "[AgentWebSocketServer] 握手检查 path=%s origin=%s enable_origin_check=%s allowed=%s",
            path,
            origin,
            enable_origin_check,
            allowed,
        )
        if allowed:
            return None

        logger.warning(
            "[AgentWebSocketServer] 握手拒绝 path=%s origin=%s reason=origin_not_allowed",
            path,
            origin,
        )
        return forbidden_origin_response(args)

    async def stop(self) -> None:
        """停止 WebSocket 服务端."""
        if self._server is None:
            return
        self._server.close()
        await self._server.wait_closed()
        self._server = None
        try:
            await self._jiuwenbox_runner.stop()
        except Exception as exc:  # noqa: BLE001
            logger.warning("[AgentWebSocketServer] jiuwenbox_runner.stop failed: %s", exc)
        logger.info("[AgentWebSocketServer] 已停止")

    # ---------- 连接处理 ----------

    async def _connection_handler(self, ws: Any) -> None:
        """处理单个 Gateway WebSocket 连接，同一连接可并发处理多个请求."""
        remote = ws.remote_address
        logger.info("[AgentWebSocketServer] 新连接: %s", remote)

        send_lock = asyncio.Lock()
        self._current_ws = ws
        self._current_send_lock = send_lock

        # 发送 connection.ack 事件，通知 Gateway 服务端已就绪
        try:
            ack_frame = {
                "type": "event",
                "event": "connection.ack",
                "payload": {"status": "ready"},
            }
            await ws.send(json.dumps(ack_frame, ensure_ascii=False))
            logger.info("[AgentWebSocketServer] 已发送 connection.ack: %s", remote)
        except Exception as e:
            logger.warning("[AgentWebSocketServer] 发送 connection.ack 失败: %s", e)

        tasks: set[asyncio.Task] = set()

        try:
            async for raw in ws:
                task = asyncio.create_task(self._handle_message(ws, raw, send_lock))
                tasks.add(task)
                task.add_done_callback(tasks.discard)
        except WebSocketConnectionClosed as e:
            logger.info(
                "[AgentWebSocketServer] 连接关闭: %s",
                format_ws_diagnostics(
                    {
                        "remote": remote,
                        "active_tasks": len(tasks),
                        "session_stream_tasks": len(self._session_stream_tasks),
                        "ping_interval": self._ping_interval,
                        "ping_timeout": self._ping_timeout,
                    },
                    describe_ws_peer(ws),
                    describe_ws_exception(e),
                ),
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] 连接处理异常 (%s): %s", remote, e)
        finally:
            self._current_ws = None
            self._current_send_lock = None
            self._clear_ws_acp_client_capabilities(ws)
            connection_tasks = list(tasks)
            for task in connection_tasks:
                if not task.done():
                    task.cancel()
            # Gateway 进程退出/端口关闭时，必须先取消各 session 内流式生产者（SessionManager）
            # 并中止 DeepAgent 内层循环；否则仅等待 _handle_message 任务结束会一直阻塞到任务自然完成。
            try:
                await self._agent_manager.cancel_all_inflight_work(
                    reason=f"[gateway ws closed {remote}] ",
                )
            except Exception:
                logger.exception("[AgentWebSocketServer] cancel_all_inflight_work failed")
            # Stop scheduler on server shutdown
            try:
                await self._stop_scheduler()
            except Exception:
                logger.exception("[AgentWebSocketServer] scheduler stop failed")
            try:
                from jiuwenswarm.agents.harness.team import cancel_all_team_stream_tasks_across_managers

                await cancel_all_team_stream_tasks_across_managers(
                    reason=f"[gateway ws closed {remote}] ",
                )
            except Exception:
                logger.exception("[AgentWebSocketServer] team stream cancel failed")
            if connection_tasks:
                await asyncio.gather(*connection_tasks, return_exceptions=True)
            self._session_stream_tasks.clear()

    async def _handle_message(self, ws: Any, raw: str | bytes, send_lock: asyncio.Lock) -> None:
        """解析一条 JSON 请求并分发到 IAgentServer 处理."""
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            wire = encode_json_parse_error_wire(
                request_id="",
                channel_id="",
                message=f"JSON 解析失败: {e}",
            )
            try:
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
            except WebSocketConnectionClosed as send_exc:
                logger.info(
                    "[AgentWebSocketServer] WebSocket 已关闭，JSON 解析错误未发送: %s",
                    format_ws_diagnostics(
                        {"json_error": str(e)},
                        describe_ws_peer(ws),
                        describe_ws_exception(send_exc),
                    ),
                )
            return

        try:
            env = E2AEnvelope.from_dict(data)
        except Exception as parse_err:
            logger.warning(
                "[AgentWebSocketServer] E2A from_dict 失败，按旧载荷解析: %s",
                parse_err,
            )
            request = _payload_to_request(data)
        else:
            jw = (env.channel_context or {}).get(E2A_INTERNAL_CONTEXT_KEY)
            if isinstance(jw, dict) and jw.get(E2A_FALLBACK_FAILED_KEY):
                legacy = jw.get(E2A_LEGACY_AGENT_REQUEST_KEY)
                logger.warning(
                    "[E2A][fallback] using legacy_agent_request request_id=%s",
                    env.request_id,
                )
                if not isinstance(legacy, dict):
                    raise ValueError("legacy_agent_request missing or not a dict")
                request = _payload_to_request(legacy)
            else:
                logger.info(
                    "[E2A][in] request_id=%s channel=%s method=%s is_stream=%s",
                    env.request_id,
                    env.channel,
                    env.method,
                    env.is_stream,
                )
                request = e2a_to_agent_request(env)

        logger.info(
            "[AgentWebSocketServer] 收到请求: request_id=%s channel_id=%s is_stream=%s",
            request.request_id,
            request.channel_id,
            request.is_stream,
        )

        try:
            if request.channel_id == "acp" and request.req_method != ReqMethod.INITIALIZE:
                metadata = dict(request.metadata or {})
                ws_caps = self._get_ws_acp_client_capabilities(ws)
                metadata.setdefault(
                    "acp_client_capabilities",
                    ws_caps or self._agent_manager.get_client_capabilities("acp"),
                )
                request.metadata = metadata

            await self._trigger_before_chat_request_hook(request)

            if request.req_method == ReqMethod.SESSION_LIST:
                await self._handle_session_list(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SESSION_RENAME:
                await self._handle_session_rename(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SESSION_SWITCH:
                await self._handle_session_switch(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SESSION_DELETE:
                await self._handle_session_delete(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SESSION_REWIND:
                await self._handle_session_rewind_full(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.SESSION_REWIND_AND_RESTORE:
                await self._handle_session_rewind_full(ws, request, send_lock, restore_files=True)
                return
            if request.req_method == ReqMethod.SESSION_REWIND_COMPACT:
                await self._handle_session_rewind_full(ws, request, send_lock, compact=True)
                return
            if request.req_method == ReqMethod.SESSION_REWIND_CONTEXT:
                await self._handle_session_rewind_context(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.TEAM_DELETE:
                await self._handle_team_delete(ws, request, send_lock)
                return
            if request.req_method in get_permissions_config_req_methods():
                await self._handle_permissions_config(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HISTORY_GET:
                if request.is_stream:
                    await self._handle_history_get_stream(ws, request, send_lock)
                else:
                    await self._handle_history_get(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.TEAM_SNAPSHOT:
                await self._handle_team_snapshot(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.PROACTIVE_TICK:
                await self._handle_proactive_tick(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_WORKFLOWS:
                await self._handle_command_workflows(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.TEAM_HISTORY_GET:
                await self._handle_team_history_get(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.TEAM_MEMBERS_GET:
                await self._handle_team_members_get(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_ADD_DIR:
                await self._handle_command_add_dir(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_CHROME:
                await self._handle_command_chrome(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_COMPACT:
                await self._handle_command_compact(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_COMPACT_PARTIAL:
                await self._handle_command_compact_partial(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_CONTEXT:
                await self._handle_command_context(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_RECAP:
                await self._handle_command_recap(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_BTW:
                await self._handle_command_btw(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_DIFF:
                await self._handle_command_diff(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_SIMPLIFY:
                await self._handle_command_simplify(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_MODEL:
                await self._handle_command_model(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_MCP:
                await self._handle_command_mcp(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_SANDBOX:
                await self._handle_command_sandbox(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_RESUME:
                await self._handle_command_resume(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_SESSION:
                await self._handle_command_session(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.COMMAND_STATUS:
                await self._handle_command_status(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.BROWSER_START:
                await self._handle_browser_start(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.BROWSER_RUNTIME_RESTART:
                await self._handle_browser_runtime_restart(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.CONFIG_CACHE_CLEAR:
                await self._handle_config_cache_clear(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.AGENT_RELOAD_CONFIG:
                await self._handle_agent_reload_config(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_LIST:
                await self._handle_extensions_list(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_IMPORT:
                await self._handle_extensions_import(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_DELETE:
                await self._handle_extensions_delete(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.EXTENSIONS_TOGGLE:
                await self._handle_extensions_toggle(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HOOKS_LIST:
                await self._handle_hooks_list(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HARNESS_PACKAGES_GET:
                await self._handle_harness_packages_get(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HARNESS_PACKAGES_SCAN:
                await self._handle_harness_packages_scan(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HARNESS_PACKAGES_ACTIVATE:
                await self._handle_harness_packages_activate(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HARNESS_PACKAGES_DEACTIVATE:
                await self._handle_harness_packages_deactivate(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.HARNESS_PACKAGES_DELETE:
                await self._handle_harness_packages_delete(ws, request, send_lock)
                return
            # Schedule task management
            if request.req_method == ReqMethod.SCHEDULE_CHECK_CONFIG:
                await self._handle_schedule_request(ws, request, send_lock, "check_config")
                return
            if request.req_method == ReqMethod.SCHEDULE_UPDATE_CONFIG:
                await self._handle_schedule_request(ws, request, send_lock, "update_config")
                return
            if request.req_method == ReqMethod.SCHEDULE_CREATE:
                await self._handle_schedule_request(ws, request, send_lock, "create")
                return
            if request.req_method == ReqMethod.SCHEDULE_RUN:
                await self._handle_schedule_request(ws, request, send_lock, "run")
                return
            if request.req_method == ReqMethod.SCHEDULE_LIST:
                await self._handle_schedule_request(ws, request, send_lock, "list")
                return
            if request.req_method == ReqMethod.SCHEDULE_STATUS:
                await self._handle_schedule_request(ws, request, send_lock, "status")
                return
            if request.req_method == ReqMethod.SCHEDULE_LOGS:
                await self._handle_schedule_request(ws, request, send_lock, "logs")
                return
            if request.req_method == ReqMethod.SCHEDULE_CANCEL:
                await self._handle_schedule_request(ws, request, send_lock, "cancel")
                return
            if request.req_method == ReqMethod.SCHEDULE_DELETE:
                await self._handle_schedule_request(ws, request, send_lock, "delete")
                return
            if request.req_method == ReqMethod.ISSUE_WATCH_ONCE:
                await self._handle_schedule_request(ws, request, send_lock, "issue_watch_once")
                return
            if request.req_method == ReqMethod.ISSUE_STATE_LIST:
                await self._handle_schedule_request(ws, request, send_lock, "issue_state_list")
                return
            if request.req_method == ReqMethod.ISSUE_DELETE:
                await self._handle_schedule_request(ws, request, send_lock, "issue_delete")
                return
            if request.req_method == ReqMethod.ISSUE_MATRIX:
                await self._handle_schedule_request(ws, request, send_lock, "issue_matrix")
                return
            if request.req_method == ReqMethod.AGENTS_LIST:
                await self._handle_agents_list(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.AGENTS_GET:
                await self._handle_agents_get(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.AGENTS_CREATE:
                await self._handle_agents_create(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.AGENTS_UPDATE:
                await self._handle_agents_update(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.AGENTS_DELETE:
                await self._handle_agents_delete(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.AGENTS_ENABLE:
                await self._handle_agents_set_enabled(ws, request, send_lock, True)
                return
            if request.req_method == ReqMethod.AGENTS_DISABLE:
                await self._handle_agents_set_enabled(ws, request, send_lock, False)
                return
            if request.req_method == ReqMethod.AGENTS_TOOLS_LIST:
                await self._handle_agents_tools_list(ws, request, send_lock)
                return
            if request.req_method == ReqMethod.CHAT_CANCEL:
                # 中断请求：根据 intent 决定是否取消流式任务
                sid = request.session_id or "default"
                intent = request.params.get("intent", "cancel") if isinstance(request.params, dict) else "cancel"
                cleanup_after_cancel = self._is_client_disconnect_cancel_request(request)

                # 只有 cancel/supplement 才取消流式任务
                # pause/resume 不取消，因为任务仍在运行（pause 在 checkpoint 阻塞，resume 解除阻塞）
                stream_task: asyncio.Task | None = None
                if intent in ("cancel", "supplement"):
                    stream_task = self._session_stream_tasks.get(sid)
                    if stream_task is not None and not stream_task.done():
                        logger.info(
                            "[AgentWebSocketServer] cancel: 终止 session 流式任务: session_id=%s intent=%s",
                            sid,
                            intent,
                        )
                        stream_task.cancel()

                try:
                    # 专门处理 cancel，复用已有 agent（不再 fallthrough 到 _handle_unary）
                    await self._handle_cancel(
                        ws,
                        request,
                        send_lock,
                        allow_create=not cleanup_after_cancel,
                    )
                finally:
                    # 等待被取消的 stream task 完成清理（finally 块中的 heartbeat 取消、
                    # _session_stream_tasks 清理、plan mode exit 检查等），避免僵尸调用。
                    if stream_task is not None and not stream_task.done():
                        try:
                            await stream_task
                        except asyncio.CancelledError:
                            pass
                        except Exception as exc:
                            logger.warning(
                                "[AgentWebSocketServer] cancel: stream task cleanup failed: "
                                "session_id=%s intent=%s error=%s",
                                sid,
                                intent,
                                exc,
                            )
                    if cleanup_after_cancel and intent in ("cancel", "supplement"):
                        await self._cleanup_client_disconnect_session_runtime(request)
                return
            if request.is_stream:
                await self._handle_stream(ws, request, send_lock)
            else:
                await self._handle_unary(ws, request, send_lock)
        except asyncio.CancelledError:
            # 流式任务被 interrupt 取消，正常退出无需报错
            logger.info(
                "[AgentWebSocketServer] 任务被取消: request_id=%s session_id=%s",
                request.request_id,
                request.session_id,
            )
        except WebSocketConnectionClosed as e:
            logger.info(
                "[AgentWebSocketServer] WebSocket 已关闭，放弃请求回包: %s",
                format_ws_diagnostics(
                    {
                        "request_id": request.request_id,
                        "channel_id": request.channel_id,
                        "session_id": request.session_id,
                        "is_stream": request.is_stream,
                    },
                    describe_ws_peer(ws),
                    describe_ws_exception(e),
                ),
            )
        except Exception as e:
            logger.exception(
                "[AgentWebSocketServer] 处理请求失败: request_id=%s: %s",
                request.request_id,
                e,
            )
            error_resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(
                error_resp, response_id=request.request_id
            )
            try:
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
            except WebSocketConnectionClosed as send_exc:
                logger.info(
                    "[AgentWebSocketServer] WebSocket 已关闭，错误响应未发送: %s",
                    format_ws_diagnostics(
                        {
                            "request_id": request.request_id,
                            "channel_id": request.channel_id,
                            "session_id": request.session_id,
                            "is_stream": request.is_stream,
                        },
                        describe_ws_peer(ws),
                        describe_ws_exception(send_exc),
                    ),
                )

    @staticmethod
    def _should_trigger_before_chat_request_hook(request: AgentRequest) -> bool:
        return request.req_method in (
            ReqMethod.CHAT_SEND,
            ReqMethod.CHAT_RESUME,
            ReqMethod.CHAT_ANSWER,
        )

    @staticmethod
    def _is_client_disconnect_cancel_request(request: AgentRequest) -> bool:
        metadata = request.metadata if isinstance(request.metadata, dict) else {}
        return (
            str(metadata.get(E2A_INTERNAL_CANCEL_SOURCE_KEY) or "").strip()
            == E2A_CANCEL_SOURCE_CLIENT_DISCONNECT
        )

    async def _cleanup_client_disconnect_session_runtime(self, request: AgentRequest) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        session_id = str(request.session_id or params.get("session_id") or "").strip()
        if not session_id:
            return
        channel_id = request.channel_id or "default"
        try:
            cleaned = await self._agent_manager.cleanup_session_runtime(
                channel_id=channel_id,
                session_id=session_id,
            )
            logger.info(
                "[AgentWebSocketServer] client disconnect session runtime cleanup: "
                "channel_id=%s session_id=%s cleaned=%s",
                channel_id,
                session_id,
                cleaned,
            )
        except Exception as exc:
            logger.warning(
                "[AgentWebSocketServer] client disconnect session runtime cleanup failed: "
                "channel_id=%s session_id=%s error=%s",
                channel_id,
                session_id,
                exc,
            )

    async def _trigger_before_chat_request_hook(self, request: AgentRequest) -> None:
        if not self._should_trigger_before_chat_request_hook(request):
            return
        from jiuwenswarm.extensions.registry import ExtensionRegistry

        params = request.params if isinstance(request.params, dict) else {}
        if not isinstance(request.params, dict):
            request.params = params

        ctx = AgentServerChatHookContext(
            request_id=request.request_id,
            channel_id=request.channel_id,
            session_id=request.session_id,
            req_method=request.req_method.value if request.req_method is not None else None,
            params=params,
        )

        await ExtensionRegistry.get_instance().trigger(AgentServerHookEvents.BEFORE_CHAT_REQUEST, ctx)

    async def _handle_cancel(
        self,
        ws: Any,
        request: AgentRequest,
        send_lock: asyncio.Lock,
        *,
        allow_create: bool = True,
    ) -> None:
        """处理 CHAT_CANCEL 中断请求：复用已有 agent 实例，避免创建新实例。

        cancel 请求的 params 中可能没有 mode 信息，如果走 _handle_unary 的 get_agent(mode) 路径
        会按默认 mode 创建新的 agent 实例，导致 interrupt 设置到空实例上，无法终止真正运行的 agent。
        因此 cancel 请求必须直接定位已有 agent 来处理。
        """
        channel_id = request.channel_id or "default"

        # 1. 尝试按 params 中的 mode 查找已有 agent
        project_dir = resolve_request_project_dir(request)
        mode_param = request.params.get("mode", "")
        if mode_param:
            mode, sub_mode, _canonical = resolve_agent_request_mode(mode_param)
            agent_mode = "agent" if mode == "auto_harness" else mode
            agent = self._agent_manager.get_agent_nowait(
                channel_id,
                mode=agent_mode,
                project_dir=project_dir,
                sub_mode=sub_mode,
            )
        else:
            agent = None

        # 2. 如果按 mode 没找到，用 get_agent_nowait 找任何已有 agent
        if agent is None:
            agent = self._agent_manager.get_agent_nowait(channel_id, project_dir=project_dir)

        resp: AgentResponse | None = None

        if agent is None and not allow_create:
            logger.info(
                "[AgentWebSocketServer] cancel: no existing agent, skip create: "
                "channel_id=%s session_id=%s",
                channel_id,
                request.session_id,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "event_type": "chat.interrupt_result",
                    "success": True,
                    "message": "No active agent runtime",
                },
            )

        # 3. 仍然没找到时 fallback 到 get_agent（异常场景）
        if agent is None and resp is None:
            logger.warning(
                "[AgentWebSocketServer] cancel: 未找到已有 agent，fallback 创建: channel_id=%s",
                channel_id,
            )
            mode, sub_mode = _apply_resolved_mode_to_request(request)
            agent_mode = "agent" if mode == "auto_harness" else mode
            agent = await self._agent_manager.get_agent(
                channel_id=channel_id,
                mode=agent_mode,
                project_dir=project_dir,
                sub_mode=sub_mode,
            )

        if agent is None and resp is None:
            raise ValueError("Failed to get agent for cancel request")

        if resp is None:
            resp = await agent.process_message(request)

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    @staticmethod
    def _resolve_code_language() -> str:
        """Determine the display language for code mode plan approval messages.

        Returns ``"cn"`` or ``"en"`` based on configuration.
        Defaults to ``"cn"`` if the config key is missing.
        """
        try:
            config = get_config()
            return config.get("language", "cn")
        except Exception:
            return "cn"

    @staticmethod
    def _should_sync_code_mode_state(request: AgentRequest) -> bool:
        """Only agent chat turns may change plan/normal mode.

        Background RPCs (e.g. ``skills.list``) also send ``mode: code.normal`` but
        must not run plan-mode restore logic or race with an in-flight approval.
        """
        method = request.req_method
        if method is None:
            return True
        return method in _CODE_MODE_SYNC_METHODS

    @staticmethod
    def _is_explicit_plan_entry_request(request: AgentRequest) -> bool:
        if not isinstance(request.params, dict):
            return False
        return request.params.get("plan_entry_source") == "slash_command"

    @staticmethod
    def _session_mode_sync_lock(session_id: str) -> asyncio.Lock:
        lock = _session_mode_sync_locks.get(session_id)
        if lock is None:
            lock = asyncio.Lock()
            _session_mode_sync_locks[session_id] = lock
        return lock

    async def _push_plan_mode_exited(self, request: AgentRequest) -> None:
        """Notify the client that plan mode ended after user approval."""
        session_id = request.session_id
        if not session_id:
            return
        await self.send_push({
            "channel_id": request.channel_id or "default",
            "session_id": session_id,
            "payload": {
                "event_type": PLAN_MODE_EXITED_EVENT_TYPE,
                "mode": "code.normal",
            },
        })

    async def _check_post_process_plan_exit(
        self,
        request: AgentRequest,
        agent: Any,
    ) -> None:
        """Detect plan→normal transition that happened inside tool execution.

        When ``exit_plan_mode`` is approved, ``ExitPlanModeTool.invoke()``
        calls ``restore_mode_after_plan_exit()`` to persist the mode change
        to the session checkpointer.  This runs AFTER ``_ensure_code_mode_state``
        has already completed (which only syncs the mode BEFORE processing).

        We check the persisted state here and push a ``plan.mode_exited``
        event so the TUI status bar updates immediately, rather than waiting
        for the next user request.

        Only checks requests whose sub_mode is ``"plan"`` — the transition
        from plan→normal can only happen during a plan-mode request (the LLM
        calls ``exit_plan_mode``).  Checking ``sub_mode == "normal"`` requests
        would produce false positives for every background RPC (e.g.
        ``skills.list``) that uses ``code.normal`` but never had an active
        plan session.
        """
        session_id = request.session_id
        if not session_id:
            return
        mode, sub_mode = _apply_resolved_mode_to_request(request)
        if mode != "code" or sub_mode != "plan":
            return

        from openjiuwen.core.single_agent import create_agent_session
        session = create_agent_session(
            session_id=session_id,
            card=agent.get_instance().card,
        )
        await session.pre_run(inputs=None)
        state = agent.get_instance().load_state(session)
        if state.plan_mode.mode == "normal":
            _plan_exited_sessions.add(session_id)
            await self._push_plan_mode_exited(request)
            logger.info(
                "[_check_post_process_plan_exit] Detected plan→normal after "
                "tool execution for session=%s",
                session_id,
            )

    @staticmethod
    def _is_stateless_method_request(request: AgentRequest) -> bool:
        """skills / skilldev / plugins / symphony 为无状态 RPC，无需 mode 解析与 adapter.

        恢复 5084467df 引入、8f54b26a7 合入 team 时误删的短路判定。
        """
        return (
            request.req_method is not None
            and request.req_method.value.startswith(
                ("skills.", "skilldev.", "plugins.", "symphony.")
            )
        )

    async def _get_stateless_agent(self, channel_id: str) -> Any:
        """为无状态请求取 agent，**不触发任何 mode 的 adapter 重建**.

        优先用 AgentManager 已缓存的 agent 模式 agent（get_agent_nowait 命中即返回，
        不命中返回 None，绝不创建）；都没缓存时现场构造一个轻量 JiuWenSwarm()
        （**不调 create_instance**，_adapter 保持 None）——其 process_message 内部对
        skills/skilldev/plugins/symphony 的无状态短路会在 _ensure_adapter 之前 return，
        碰不到 adapter。真正的 adapter 重建留给 chat.send。

        相比 5084467df 原版用 get_agent(mode="agent") 作 fallback（会触发 agent 模式
        adapter 重建，治标不治本），此处彻底解耦。JiuWenSwarm.__init__ 仅 4 个赋值 +
        一个只读目录的 SkillManager，现场 new 开销可忽略，无需额外缓存态。
        """
        cached = self._agent_manager.get_agent_nowait(
            channel_id=channel_id, mode="agent"
        )
        if cached is not None:
            return cached
        from jiuwenswarm.server.runtime.agent_adapter.interface import JiuWenSwarm
        return JiuWenSwarm()  # 不调 create_instance，_adapter 保持 None

    async def _prepare_code_mode_chat_turn(
        self,
        request: AgentRequest,
        channel_id: str,
    ) -> tuple[str, str | None, Any]:
        """Mode resolution and correct agent instance selection."""
        mode, sub_mode = _apply_resolved_mode_to_request(request)
        agent_mode = "agent" if mode == "auto_harness" else mode
        project_dir = resolve_request_project_dir(request)

        agent = await self._agent_manager.get_agent(
            channel_id=channel_id,
            mode=agent_mode,
            project_dir=project_dir,
            sub_mode=sub_mode,
        )
        if agent is None:
            raise ValueError("Failed to get agent")

        return mode, sub_mode, agent

    async def _ensure_code_mode_state(
        self,
        request: AgentRequest,
        mode: str,
        sub_mode: str,
        agent: Any,
    ) -> bool:
        """code 模式：确保 agent 的 plan_mode 状态正确，必要时执行 switch_mode 并持久化.

        当 plan 刚完成时跳过陈旧的 normal→plan switch_mode，
        避免 exit_plan_mode 已恢复的模式被覆盖；显式用户 /plan 进入除外.
        switch_mode 内部已通过 save_state 写入正确的 "deepagent" key，
        此处只需 post_run 持久化到 checkpointer.

        切换到 plan 模式且尚未调用 enter_plan_mode 时，注入 <system-reminder>
        告知 LLM 调用 enter_plan_mode。

        ``exit_plan_mode`` now restores mode immediately inside the tool
        (via ``restore_mode_after_plan_exit``), so this method no longer needs
        to gate plan→normal transitions with an approval flag.

        Returns:
            ``True`` if plan mode was restored to normal (mode sync occurred).
        """
        if mode != "code" or sub_mode == "team":
            return False
        if not self._should_sync_code_mode_state(request):
            return False
        if is_interrupt_resume_payload(request.params):
            logger.info(
                "[_ensure_code_mode_state] Skip mode sync while resuming tool interrupt "
                "for session=%s source=%s",
                request.session_id,
                (request.params or {}).get("source") if isinstance(request.params, dict) else None,
            )
            return False

        session_id = request.session_id or "default"
        restored_after_approval = False
        async with self._session_mode_sync_lock(session_id):
            from openjiuwen.core.single_agent import create_agent_session
            session = create_agent_session(
                session_id=request.session_id, card=agent.get_instance().card
            )
            await session.pre_run(inputs=None)  # 从 checkpointer 加载历史 state
            state = agent.get_instance().load_state(session)
            # 仅在目标模式与当前模式不同时执行模式切换
            mode_changed_to_plan = False
            if state.plan_mode.mode != sub_mode:
                # Guard: block stale normal→plan switches when plan was already exited.
                # Explicit user /plan requests bypass this guard and start a fresh plan.
                # Two mechanisms:
                #   1. _plan_exited_sessions flag (precise — set by _check_post_process_plan_exit)
                #   2. plan_slug fallback (defense-in-depth — plan exists but mode is normal)
                if state.plan_mode.mode == "normal" and sub_mode == "plan":
                    blocked = False
                    explicit_plan_entry = self._is_explicit_plan_entry_request(request)
                    if explicit_plan_entry:
                        _plan_exited_sessions.discard(session_id)
                    elif session_id in _plan_exited_sessions:
                        _plan_exited_sessions.discard(session_id)
                        blocked = True
                        logger.info(
                            "[_ensure_code_mode_state] Blocked stale plan re-entry via "
                            "flag for session=%s",
                            session_id,
                        )
                    elif state.plan_mode.plan_slug is not None:
                        # Fallback: plan was completed, checkpoint is authoritative.
                        # Clear slug so this guard is one-shot.
                        state.plan_mode.plan_slug = None
                        agent.get_instance().save_state(session, state)
                        await session.post_run()
                        blocked = True
                        logger.info(
                            "[_ensure_code_mode_state] Blocked stale plan re-entry via "
                            "plan_slug for session=%s",
                            session_id,
                        )
                    if blocked:
                        if isinstance(request.params, dict):
                            request.params["mode"] = "code.normal"
                        await self._push_plan_mode_exited(request)
                        return False
                agent.get_instance().switch_mode(session=session, mode=sub_mode)
                if state.plan_mode.mode == "plan" and sub_mode == "normal":
                    restored_after_approval = True
                    logger.info(
                        "[_ensure_code_mode_state] Synced plan→normal for session=%s",
                        session_id,
                    )
                if sub_mode == "plan":
                    mode_changed_to_plan = True
                    # Clear stale plan_slug from previous plan session so
                    # enter_plan_mode creates a fresh plan file.
                    state = agent.get_instance().load_state(session)
                    if state.plan_mode.plan_slug:
                        state.plan_mode.plan_slug = None
                        agent.get_instance().save_state(session, state)
                # switch_mode 内部已通过 save_state 写入 "deepagent" key，
                # 只需 post_run 持久化到 checkpointer
                await session.post_run()

            # 切换到 plan 模式时注入 <system-reminder> 告知 LLM 调用 enter_plan_mode。
            # 使用 mode_changed_to_plan 而非 plan_slug 判断，因为 restore_mode_after_plan_exit
            # 不清除 plan_slug，导致后续 /plan 时提醒被错误跳过。
            if sub_mode == "plan" and mode_changed_to_plan:
                _inject_plan_mode_activation_reminder(request)

        return restored_after_approval

    async def _handle_unary(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """非流式处理：调用 process_message，返回一条 E2AResponse 线 JSON。"""
        channel_id = request.channel_id or "default"

        if request.req_method == ReqMethod.INITIALIZE:
            await self._handle_initialize(ws, request, send_lock)
            return

        if request.req_method == ReqMethod.SESSION_CREATE:
            await self._handle_session_create(ws, request, send_lock)
            return

        if request.req_method == ReqMethod.SESSION_FORK:
            await self._handle_session_fork(ws, request, send_lock)
            return

        if request.req_method == ReqMethod.ACP_TOOL_RESPONSE:
            await self._handle_acp_tool_response(ws, request, send_lock)
            return

        # 无状态请求（skills / skilldev / plugins / symphony）不需要 mode 解析和
        # code mode 状态管理，直接走 process_message 即可。用轻量 agent 获取，不触发
        # adapter 重建（恢复 8f54b26a7 误删的短路，并修正 5084467df 触发重建的缺陷）。
        if self._is_stateless_method_request(request):
            agent = await self._get_stateless_agent(channel_id)
            resp = await agent.process_message(request)
            if getattr(resp, "agent_ref", None) is None:
                resp.agent_ref = request.agent_ref
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            logger.info(
                "[AgentWebSocketServer] 非流式响应已发送: request_id=%s",
                request.request_id,
            )
            return

        mode, sub_mode, agent = await self._prepare_code_mode_chat_turn(
            request, channel_id
        )

        restored_plan = await self._ensure_code_mode_state(request, mode, sub_mode, agent)
        if restored_plan:
            await self._push_plan_mode_exited(request)

        resp = None
        try:
            resp = await agent.process_message(request)
        finally:
            # Push plan.mode_exited if exit_plan_mode restored mode during processing
            await self._check_post_process_plan_exit(request, agent)

        # V2: 非流式响应回带请求侧 agent_ref，供 gateway 3 元组路由（设计 §6.3）。
        # is None 守卫：保留 agent 层显式设置的 agent_ref（如 team 模式由事件派生）。
        if getattr(resp, "agent_ref", None) is None:
            resp.agent_ref = request.agent_ref

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))
        logger.info(
            "[AgentWebSocketServer] 非流式响应已发送: request_id=%s",
            request.request_id,
        )

    async def _handle_stream(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """流式处理：调用 process_message_stream，逐条发送 E2AResponse 线 JSON。"""
        channel_id = request.channel_id or "default"
        session_id = request.session_id or "default"
        current_task = asyncio.current_task()
        if current_task is not None:
            self._session_stream_tasks[session_id] = current_task

        # 无状态流式请求（skills / skilldev / plugins / symphony）不需要 mode 解析和
        # code mode 状态管理，直接走 process_message_stream 即可。用轻量 agent 获取，
        # 不触发 adapter 重建（恢复 8f54b26a7 误删的短路，并修正 5084467df 触发重建的缺陷）。
        if self._is_stateless_method_request(request):
            agent = await self._get_stateless_agent(channel_id)
        else:
            mode, sub_mode, agent = await self._prepare_code_mode_chat_turn(
                request, channel_id
            )

            restored_plan = await self._ensure_code_mode_state(request, mode, sub_mode, agent)
            if restored_plan:
                await self._push_plan_mode_exited(request)

        chunk_count = 0
        # 心跳控制：当有真实 chunk 发送时重置，空闲时发送心跳
        heartbeat_event = asyncio.Event()
        heartbeat_task: asyncio.Task | None = None

        async def _heartbeat_loop() -> None:
            """后台心跳任务：在空闲期间定期发送 keepalive chunk."""
            try:
                while True:
                    # 等待心跳间隔，如果期间有真实 chunk 发送则 heartbeat_event 被设置，重置等待
                    try:
                        await asyncio.wait_for(
                            heartbeat_event.wait(),
                            timeout=_STREAM_HEARTBEAT_INTERVAL_SECONDS,
                        )
                        # 有真实 chunk 发送，重置 event 继续等待
                        heartbeat_event.clear()
                    except asyncio.TimeoutError:
                        # 超时：空闲超过心跳间隔，发送 keepalive chunk
                        heartbeat_chunk = AgentResponseChunk(
                            request_id=request.request_id,
                            channel_id=channel_id,
                            payload={"event_type": "keepalive"},
                            is_complete=False,
                        )
                        # V2: 心跳 chunk 也回带 agent_ref，避免切换 mode 后
                        # 旧 session 心跳错路由到新 agent 窗口（设计 §5.2 场景 2）。
                        if heartbeat_chunk.agent_ref is None:
                            heartbeat_chunk.agent_ref = request.agent_ref
                        wire = encode_agent_chunk_for_wire(
                            heartbeat_chunk,
                            response_id=request.request_id,
                            sequence=-1,  # 心跳使用特殊序列号 -1
                        )
                        async with send_lock:
                            await ws.send(json.dumps(wire, ensure_ascii=False))
                        logger.info(
                            "[AgentWebSocketServer] keepalive chunk 发送: request_id=%s",
                            request.request_id,
                        )
            except asyncio.CancelledError:
                pass
            except WebSocketConnectionClosed:
                logger.info(
                    "[AgentWebSocketServer] keepalive 停止，WebSocket 已关闭: request_id=%s",
                    request.request_id,
                )

        # 启动心跳任务
        heartbeat_task = asyncio.create_task(_heartbeat_loop())

        try:
            async for chunk in agent.process_message_stream(request):
                chunk_count += 1
                # 通知心跳任务有真实 chunk 发送，重置心跳计时
                heartbeat_event.set()
                # V2: chunk 回带请求侧 agent_ref，供 gateway 3 元组精确路由
                # （设计 §6.3）。is None 守卫：保留 team 模式由事件派生的 agent_ref
                # （_build_team_event_chunk_meta 已设值），不覆盖。
                if chunk.agent_ref is None:
                    chunk.agent_ref = request.agent_ref
                wire = encode_agent_chunk_for_wire(
                    chunk,
                    response_id=request.request_id,
                    sequence=chunk_count - 1,
                )
                # 诊断：打印前 3 个和每 50 个 chunk 的发送情况
                if chunk_count <= 3 or chunk_count % 50 == 0:
                    _pl = getattr(chunk, "payload", None) or {}
                    _et = _pl.get("event_type", "") if isinstance(_pl, dict) else ""
                    logger.info(
                        "[AgentWebSocketServer] chunk sent: request_id=%s seq=%s"
                        " event_type=%s wire_keys=%s",
                        request.request_id, chunk_count - 1, _et,
                        list(wire.keys())[:10] if isinstance(wire, dict) else "non-dict",
                    )
                try:
                    async with send_lock:
                        await ws.send(json.dumps(wire, ensure_ascii=False))
                except WebSocketConnectionClosed:
                    logger.info(
                        "[AgentWebSocketServer] 流式响应停止，WebSocket 已关闭: request_id=%s",
                        request.request_id,
                    )
                    return
                # 清除 event，让心跳任务重新开始计时
                heartbeat_event.clear()
        finally:
            # 停止心跳任务
            if heartbeat_task is not None:
                heartbeat_task.cancel()
                try:
                    await heartbeat_task
                except asyncio.CancelledError:
                    pass
                except WebSocketConnectionClosed:
                    pass
            # 清除 session 流式任务追踪（仅清除自身，避免误删后续新任务）
            if self._session_stream_tasks.get(session_id) is current_task:
                self._session_stream_tasks.pop(session_id, None)

            # Push plan.mode_exited if exit_plan_mode restored mode during processing
            await self._check_post_process_plan_exit(request, agent)

        logger.info(
            "[AgentWebSocketServer] 流式响应已发送: request_id=%s 共 %s 个 chunk",
            request.request_id,
            chunk_count,
        )

    async def _handle_session_list(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 session.list 请求：扫描 sessions 目录，返回历史会话基础信息列表."""
        from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata

        sessions_dir = get_agent_sessions_dir()
        sessions = []

        try:
            if sessions_dir.exists():
                for entry in sorted(sessions_dir.iterdir(), key=lambda e: e.stat().st_mtime, reverse=True):
                    if not entry.is_dir():
                        continue
                    # 强制跳过缓存，确保获取跨进程写入的最新数据（如 Gateway 的 /color 设置）
                    meta = get_session_metadata(entry.name, cache_bust=True)
                    if not meta:
                        meta = {
                            "session_id": entry.name,
                            "channel_id": "",
                            "title": "",
                            "message_count": 0,
                            "last_message_at": entry.stat().st_mtime,
                        }
                    sessions.append(meta)
        except Exception as exc:
            logger.warning("[AgentWebSocketServer] 扫描 sessions 目录失败: %s", exc)

        resp = AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=True,
            payload={"sessions": sessions},
            metadata=request.metadata,
        )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_rename(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 session.rename：与 CLI Gateway 本地回退共用 apply_session_rename。"""
        from jiuwenswarm.server.runtime.session.session_rename import apply_session_rename

        sid = request.session_id or ""
        ch = (request.channel_id or "").strip() or "tui"
        ok, payload, err, code = apply_session_rename(
            request.params,
            sid,
            init_channel_id=ch,
        )
        if ok:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload or {},
                metadata=request.metadata,
            )
        else:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": err or "session.rename failed", "code": code or ""},
                metadata=request.metadata,
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_switch(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """Switch the active team runtime without deleting recoverable session state."""
        from jiuwenswarm.agents.harness.team import get_team_manager

        params = request.params if isinstance(request.params, dict) else {}
        target = str(params.get("session_id") or request.session_id or "").strip()
        is_team = is_team_params(params)

        if not target:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "session_id is required", "code": "BAD_REQUEST"},
                metadata=request.metadata,
            )
        elif not is_team:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "error": "session.switch is only supported for team mode",
                    "code": "UNSUPPORTED_MODE",
                },
                metadata=request.metadata,
            )
        else:
            channel_id = str(request.channel_id or "").strip() or "default"
            team_manager = get_team_manager(channel_id)
            await team_manager.prepare_session_switch(
                target,
                reason="session.switch: ",
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "session_id": target,
                    "mode": "team",
                    "switched": True,
                },
                metadata=request.metadata,
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _find_team_session_ids(self, team_name: str) -> list[str]:
        from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata

        sessions_dir = get_agent_sessions_dir()
        if not sessions_dir.exists():
            return []

        matched_session_ids: list[str] = []
        for session_dir in sessions_dir.iterdir():
            if not session_dir.is_dir():
                continue

            session_id = session_dir.name
            metadata = get_session_metadata(session_id)
            mode = str(metadata.get("mode") or "").strip().lower()
            if mode != "team":
                continue

            metadata_team_name = str(metadata.get("team_name") or "").strip()
            if metadata_team_name == team_name:
                matched_session_ids.append(session_id)

        return sorted(set(matched_session_ids))

    async def _ensure_persistent_checkpointer_response(
        self,
        request: AgentRequest,
    ) -> AgentResponse | None:
        """Return an error response when persistent checkpoint storage is unavailable."""
        try:
            from jiuwenswarm.server.runtime.agent_adapter.interface_deep import ensure_persistent_checkpointer

            await ensure_persistent_checkpointer()
            return None
        except Exception as exc:
            logger.exception(
                "[AgentWebSocketServer] persistent checkpointer unavailable: request_id=%s error=%s",
                request.request_id,
                exc,
            )
            return AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "error": "persistent checkpointer is unavailable",
                    "code": "CHECKPOINT_UNAVAILABLE",
                },
                metadata=request.metadata,
            )

    async def _handle_team_delete(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """Delete a team and all team sessions that persist that team."""
        from openjiuwen.core.runner import Runner
        from jiuwenswarm.agents.harness.team import (
            stop_team_session_runtime_across_managers,
        )

        params = request.params if isinstance(request.params, dict) else {}
        is_team = is_team_params(params)
        team_name = str(params.get("team_name") or "").strip()

        if not team_name:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "team_name is required", "code": "BAD_REQUEST"},
                metadata=request.metadata,
            )
        elif not is_team:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "error": "team.delete is only supported for team mode",
                    "code": "UNSUPPORTED_MODE",
                },
                metadata=request.metadata,
            )
        else:
            checkpoint_resp = await self._ensure_persistent_checkpointer_response(request)
            if checkpoint_resp is not None:
                resp = checkpoint_resp
            else:
                team_session_ids = await self._find_team_session_ids(team_name)
                if not team_session_ids:
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=False,
                        payload={"error": "team sessions not found", "code": "NOT_FOUND"},
                        metadata=request.metadata,
                    )
                else:
                    for team_session_id in team_session_ids:
                        await stop_team_session_runtime_across_managers(
                            team_session_id,
                            reason="team.delete: ",
                        )

                    await Runner.delete_agent_team(
                        team_name=team_name,
                        session_ids=team_session_ids,
                        force=True,
                    )

                    for team_session_id in team_session_ids:
                        session_dir = get_agent_sessions_dir() / team_session_id
                        if session_dir.exists():
                            try:
                                shutil.rmtree(session_dir)
                            except Exception as exc:
                                logger.warning(
                                    "[AgentWebSocketServer] failed to delete local team session dir: "
                                    "session_id=%s error=%s",
                                    team_session_id,
                                    exc,
                                )
                                continue
                        remove_session_metadata_cache(team_session_id)

                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=True,
                        payload={
                            "team_name": team_name,
                            "session_ids": team_session_ids,
                            "deleted": True,
                        },
                        metadata=request.metadata,
                    )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_delete(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """Delete a single session and its recoverable runtime state."""
        from openjiuwen.core.runner import Runner
        from jiuwenswarm.server.runtime.session.session_metadata import get_session_metadata
        from jiuwenswarm.agents.harness.team import get_team_manager

        params = request.params if isinstance(request.params, dict) else {}
        target = str(params.get("session_id") or "").strip()
        if not target:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "session_id is required", "code": "BAD_REQUEST"},
                metadata=request.metadata,
            )
        else:
            session_dir = get_agent_sessions_dir() / target
            if not session_dir.exists():
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "session not found", "code": "NOT_FOUND"},
                    metadata=request.metadata,
                )
            elif not session_dir.is_dir():
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": "session is not a directory", "code": "BAD_REQUEST"},
                    metadata=request.metadata,
                )
            else:
                checkpoint_resp = await self._ensure_persistent_checkpointer_response(request)
                if checkpoint_resp is not None:
                    resp = checkpoint_resp
                else:
                    metadata = get_session_metadata(target)
                    mode = str(metadata.get("mode") or "").strip().lower()
                    channel_id = str(metadata.get("channel_id") or request.channel_id or "").strip() or None
                    try:
                        if mode == "team":
                            team_manager = get_team_manager(channel_id)
                            deleted = await team_manager.delete_session_runtime(
                                target,
                                reason="session.delete: ",
                            )
                        else:
                            await Runner.release(target)
                            deleted = True
                    except Exception as exc:
                        logger.warning(
                            "[AgentWebSocketServer] session.delete runtime cleanup failed: session_id=%s error=%s",
                            target,
                            exc,
                        )
                        deleted = False

                    if not deleted:
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=False,
                            payload={"error": "session runtime cleanup failed", "code": "DELETE_FAILED"},
                            metadata=request.metadata,
                        )
                    else:
                        shutil.rmtree(session_dir)
                        _plan_exited_sessions.discard(target)
                        remove_session_metadata_cache(target)
                        resp = AgentResponse(
                            request_id=request.request_id,
                            channel_id=request.channel_id,
                            ok=True,
                            payload={"session_id": target},
                            metadata=request.metadata,
                        )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    def _resolve_rewind_agent(self, channel_id: str) -> tuple[Any, Any] | None:
        """Return (deep_agent, react_agent) for the given channel, or None."""
        agent = self._agent_manager.get_agent_nowait(
            channel_id=channel_id or "default"
        )
        if agent is None:
            return None
        deep_agent = agent.get_instance()
        if deep_agent is None:
            return None
        react_agent = deep_agent.react_agent
        if react_agent is None:
            return None
        return (deep_agent, react_agent)

    @staticmethod
    def _send_error_response(ws: Any, request: AgentRequest,
                              send_lock: asyncio.Lock, error: str,
                              code: str | None = None) -> str:
        """Send an error AgentResponse and return the wire JSON string."""
        payload: dict[str, Any] = {"error": error}
        if code:
            payload["code"] = code
        resp = AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            ok=False,
            payload=payload,
            metadata=request.metadata,
        )
        return json.dumps(
            encode_agent_response_for_wire(resp, response_id=request.request_id),
            ensure_ascii=False,
        )

    async def _handle_session_rewind_full(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock,
        restore_files: bool = False,
        compact: bool = False,
    ) -> None:
        """Full rewind: truncate history.json + context_engine + update checkpointer."""
        from jiuwenswarm.agents.harness.common.session_ops_service import (
            rewind_session,
            rewind_session_context,
        )

        params = request.params if isinstance(request.params, dict) else {}
        target_sid = str(params.get("session_id") or request.session_id or "").strip()
        turn_index = params.get("turn_index")
        compact_summary = params.get("compact_summary") if compact else None
        direction = str(params.get("direction") or "from").strip() if compact else "from"
        summarized_count = int(params.get("summarized_count", 0) or 0) if compact else 0

        if not target_sid or turn_index is None:
            wire = AgentWebSocketServer._send_error_response(
                ws, request, send_lock,
                "session_id and turn_index required", "BAD_REQUEST",
            )
            async with send_lock:
                await ws.send(wire)
            return

        try:
            turn_index = int(turn_index)
        except (ValueError, TypeError):
            wire = AgentWebSocketServer._send_error_response(
                ws, request, send_lock,
                "turn_index must be integer", "BAD_REQUEST",
            )
            async with send_lock:
                await ws.send(wire)
            return

        try:
            # Step 1: Optionally restore files first
            restore_result: dict[str, Any] = {}
            if restore_files:
                from jiuwenswarm.agents.harness.common.session_ops_service import restore_session_files
                restore_result = restore_session_files(session_id=target_sid, turn_index=turn_index)

            # Step 2: Truncate history.json (local file operation)
            # "up_to" direction: keep messages from turn_index onward, summarize the prefix.
            # compact_partial_session handles this correctly (rewind_session only supports
            # the "from" direction — keeping the prefix and truncating the tail).
            if compact and direction == "up_to":
                from jiuwenswarm.agents.harness.common.session_ops_service import compact_partial_session
                rewind_result = compact_partial_session(
                    session_id=target_sid,
                    turn_index=turn_index,
                    direction="up_to",
                    llm_summary=compact_summary,
                )
            else:
                rewind_result = rewind_session(session_id=target_sid, turn_index=turn_index)

            # Step 3: Truncate context_engine in-place + persist to checkpointer.
            # rewind_session_context reads the already-truncated history.json and
            # converts ALL records to context messages, so it naturally produces the
            # correct result for both "from" and "up_to" directions.
            context_ok = False
            pair = self._resolve_rewind_agent(request.channel_id or "default")
            if pair is not None:
                deep_agent, _react_agent = pair
                try:
                    context_ok = await rewind_session_context(
                        deep_agent=deep_agent,
                        session_id=target_sid,
                        turn_index=turn_index,
                    )
                except Exception as exc:
                    logger.warning(
                        "[AgentWS] session.rewind context truncation failed: %s", exc,
                    )

            payload = {**rewind_result, "rewind_context": context_ok}
            if restore_files:
                payload["restored_files"] = restore_result.get("restored_files", [])
                payload["deleted_files"] = restore_result.get("deleted_files", [])
                payload["restore_errors"] = restore_result.get("errors", [])

            # Step 4: For compact mode, append boundary + rewind_summary + compact_summary records.
            # compact_partial_session already writes these for "up_to", so only append for "from".
            if compact and direction == "from":
                import uuid as _uuid
                import time as _time
                from jiuwenswarm.server.runtime.session.session_history import append_history_record
                request_id = str(_uuid.uuid4())
                now = _time.time()

                short_text = (
                    f"Summarized {summarized_count} messages from this point."
                    if direction == "from"
                    else f"Summarized {summarized_count} messages up to this point."
                )

                append_history_record(
                    session_id=target_sid,
                    request_id=request_id,
                    channel_id=request.channel_id or "tui",
                    role="assistant",
                    event_type="context.compact_boundary",
                    content="Conversation compacted",
                    timestamp=now,
                    extra={
                        "compact_metadata": {
                            "trigger": "manual_rewind",
                            "direction": direction,
                            "turn_index": turn_index,
                            "summarized_messages": summarized_count,
                        },
                    },
                )

                append_history_record(
                    session_id=target_sid,
                    request_id=request_id,
                    channel_id=request.channel_id or "tui",
                    role="assistant",
                    event_type="context.rewind_summary",
                    content=short_text,
                    timestamp=now + 0.001,
                    extra={
                        "compact_metadata": {
                            "trigger": "manual_rewind",
                            "direction": direction,
                            "turn_index": turn_index,
                            "summarized_messages": summarized_count,
                        },
                        "is_compact_summary": True,
                    },
                )

                if isinstance(compact_summary, str) and compact_summary.strip():
                    append_history_record(
                        session_id=target_sid,
                        request_id=request_id,
                        channel_id=request.channel_id or "tui",
                        role="assistant",
                        event_type="context.compact_summary",
                        content=compact_summary.strip(),
                        timestamp=now + 0.002,
                        extra={
                            "compact_metadata": {
                                "trigger": "manual_rewind",
                                "direction": direction,
                                "turn_index": turn_index,
                                "summarized_messages": summarized_count,
                            },
                            "is_compact_summary": True,
                            "transcript_only": True,
                        },
                    )

                payload["summarized_messages"] = summarized_count

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
                metadata=request.metadata,
            )
        except ValueError as exc:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "BAD_REQUEST"},
                metadata=request.metadata,
            )
        except Exception as exc:
            logger.exception("[AgentWS] session.rewind failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata=request.metadata,
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_rewind_context(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Truncate history.json + in-memory context_engine for a session."""
        from jiuwenswarm.agents.harness.common.session_ops_service import (
            rewind_session,
            rewind_session_context,
        )

        params = request.params if isinstance(request.params, dict) else {}
        target_sid = str(params.get("session_id") or request.session_id or "").strip()
        turn_index = params.get("turn_index")

        if not target_sid or turn_index is None:
            wire = AgentWebSocketServer._send_error_response(
                ws, request, send_lock,
                "session_id and turn_index required", "BAD_REQUEST",
            )
            async with send_lock:
                await ws.send(wire)
            return

        try:
            turn_index = int(turn_index)
        except (ValueError, TypeError):
            wire = AgentWebSocketServer._send_error_response(
                ws, request, send_lock,
                "turn_index must be integer", "BAD_REQUEST",
            )
            async with send_lock:
                await ws.send(wire)
            return

        pair = self._resolve_rewind_agent(request.channel_id or "default")
        if pair is None:
            wire = AgentWebSocketServer._send_error_response(
                ws, request, send_lock, "no agent instance available",
            )
            async with send_lock:
                await ws.send(wire)
            return
        deep_agent, _react_agent = pair

        try:
            # Truncate history.json first so rewind_session_context reads the
            # correct truncated state (the new implementation rebuilds context
            # from history.json on disk).
            rewind_result = rewind_session(session_id=target_sid, turn_index=turn_index)
            context_ok = await rewind_session_context(
                deep_agent=deep_agent,
                session_id=target_sid,
                turn_index=turn_index,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={**rewind_result, "rewind_context": context_ok},
                metadata=request.metadata,
            )
        except ValueError as exc:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "BAD_REQUEST"},
                metadata=request.metadata,
            )
        except Exception as exc:
            logger.exception("[AgentWS] session.rewind_context failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
                metadata=request.metadata,
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_permissions_config(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 permissions.* E2A 请求（与 Web ``register_method`` 同名 method）。"""
        from jiuwenswarm.agents.harness.common.rails.permissions.permissions_config_rpc import \
            dispatch_permissions_config_request

        resp = dispatch_permissions_config_request(request)

        # After any successful mutation (delete / update / set / create),
        # reload agent config so the PermissionInterruptRail picks up the
        # change immediately instead of waiting for the next tool call's
        # get_permissions_snapshot refresh.
        read_only_methods = {
            ReqMethod.PERMISSIONS_TOOLS_GET,
            ReqMethod.PERMISSIONS_RULES_GET,
            ReqMethod.PERMISSIONS_APPROVAL_OVERRIDES_GET,
        }
        if resp.ok and request.req_method not in read_only_methods:
            # 后台异步重载: 不阻塞权限 RPC 回包(避免 reload 慢导致 AgentServer
            # request timed out)。reload_agents_config 内部有 _reload_lock 串行化
            # + fingerprint 去重,fire-and-forget 安全。
            reload_task = asyncio.create_task(
                self._agent_manager.reload_agents_config(get_config(), None)
            )
            _background_permission_reload_tasks.add(reload_task)
            reload_task.add_done_callback(_background_permission_reload_tasks.discard)
            reload_task.add_done_callback(_log_permission_reload_failure)

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_history_get(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        session_id = params.get("session_id")
        page_idx = params.get("page_idx")
        data = self.get_conversation_history(session_id=session_id, page_idx=page_idx)
        if data is None:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "invalid page_idx or session history not found"},
            )
        else:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=data,
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))


    async def _handle_proactive_tick(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """Handle proactive.tick request from CronScheduler.

        This is called by Gateway's CronScheduler to trigger a recommendation tick.
        Respects cooldown and daily limits.
        """
        if self._proactive_engine is None:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "ProactiveEngine not initialized"},
            )
        else:
            try:
                # Extract target_channel from params
                params = request.params or {}
                target_channel = params.get("target_channel")

                # Run the tick (respects cooldown and daily limits)
                success = await self._proactive_engine.tick_now(target_channel=target_channel)

                status = "tick_executed" if success else "no_recommendation"
                last_tick = self._proactive_engine.last_tick_at
                if last_tick > 0:
                    status = f"{status} (last_tick_at={last_tick:.0f})"

                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"status": status, "success": success},
                )
            except Exception as e:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": str(e)},
                )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_team_snapshot(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from jiuwenswarm.agents.harness.team import get_team_manager

        session_id = request.session_id or ""
        channel_id = request.channel_id or "web"

        team_manager = get_team_manager(channel_id)
        monitor_handler = team_manager.get_monitor_handler(session_id)

        if monitor_handler is None or not monitor_handler.is_running:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=True,
                payload={"members": [], "tasks": [], "team_id": None},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            snapshot = await monitor_handler.get_team_snapshot()
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=True,
                payload=snapshot or {"members": [], "tasks": [], "team_id": None},
            )
        except Exception as e:
            logger.warning("[AgentWebSocketServer] team.snapshot failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=True,
                payload={"members": [], "tasks": [], "team_id": None},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_team_members_get(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """Return the live team member list for /join seat validation.

        Unlike ``team.snapshot`` (a full snapshot for frontend restore), this
        is an internal unary RPC consumed only by the gateway's /join handler.
        It calls ``get_member_list()`` (members only, no tasks) and filters
        down to ``role == "human_agent"``, since the caller only needs the
        names of seats a human may claim. Not touching ``get_team_snapshot()``
        keeps /join validation independent of the team task table — a missing
        ``team_task_*`` table must not block member seat validation.
        """
        from jiuwenswarm.agents.harness.team import (
            get_all_team_managers,
            get_team_manager,
        )

        params = request.params if isinstance(request.params, dict) else {}
        session_id = params.get("session_id") or request.session_id or ""
        channel_id = request.channel_id or "web"

        # TeamManager 按 channel_id 隔离，但 session_id 全局唯一：/join 的来源
        # channel（如飞书）可能不同于 team 创建时的 channel（如 web）。因此先按
        # 请求 channel 取快路径，未命中则遍历所有 manager 找持有该 session 的
        # monitor，避免跨 channel /join 时查到空列表而误判"团队未就绪"。
        team_manager = get_team_manager(channel_id)
        monitor_handler = team_manager.get_monitor_handler(session_id)
        if monitor_handler is None and session_id:
            for mgr in get_all_team_managers():
                if mgr is team_manager:
                    continue
                candidate = mgr.get_monitor_handler(session_id)
                if candidate is not None:
                    monitor_handler = candidate
                    break

        if monitor_handler is None or not monitor_handler.is_running:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=True,
                payload={"members": [], "team_id": None},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            members_raw = await monitor_handler.get_member_list()
            members = [
                m for m in (members_raw or [])
                if isinstance(m, dict) and m.get("role") == "human_agent"
            ]
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=True,
                payload={"members": members},
            )
        except Exception as e:
            logger.warning("[AgentWebSocketServer] team.members.get failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=True,
                payload={"members": []},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_workflows(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """Handle command.workflows RPC request — return workflow_run_snapshot."""
        from jiuwenswarm.agents.harness.team import get_team_manager

        session_id = request.session_id or ""
        channel_id = request.channel_id or "web"

        # WF_DBG: 维测日志 — 记录 command.workflows 请求到达
        logger.info(
            "[WF_DBG command_workflows] request received: "
            "channel_id=%s session_id=%s request_id=%s",
            channel_id,
            session_id,
            request.request_id,
        )

        team_manager = get_team_manager(channel_id)
        workflow_handler = team_manager.get_workflow_handler(session_id)

        if workflow_handler is None:
            # No live handler (runtime not active / torn down by cancel-stop).
            # The snapshot is a read-only pull and must not depend on runtime
            # liveness — fall back to the persisted checkpoint so historical /
            # terminal workflow runs remain queryable after the team session
            # is cancelled or stopped.
            try:
                from jiuwenswarm.server.runtime.agent_adapter.team_helpers import (
                    restore_workflow_runs,
                )

                restored = restore_workflow_runs(session_id)
                workflows = (
                    [run.to_workflow_run_dict() for run in restored.values()]
                    if restored
                    else []
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[WF_DBG command_workflows] checkpoint restore failed: "
                    "channel_id=%s session_id=%s error=%s",
                    channel_id,
                    session_id,
                    exc,
                )
                workflows = []
            logger.info(
                "[WF_DBG command_workflows] no live handler, restored from checkpoint: "
                "channel_id=%s session_id=%s workflows_count=%d",
                channel_id,
                session_id,
                len(workflows),
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=True,
                payload=_build_workflow_snapshot_payload(workflows, session_id=session_id),
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            snapshot = workflow_handler.get_workflow_snapshot()
            # WF_DBG: 维测日志 — 记录返回的快照内容摘要
            wf_names = [wf.get("name", "?") for wf in snapshot]
            wf_statuses = [wf.get("status", "?") for wf in snapshot]
            logger.info(
                "[WF_DBG command_workflows] snapshot returned: "
                "channel_id=%s session_id=%s workflows_count=%d "
                "names=%s statuses=%s",
                channel_id,
                session_id,
                len(snapshot),
                wf_names,
                wf_statuses,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=True,
                payload=_build_workflow_snapshot_payload(snapshot, session_id=session_id),
            )
        except Exception as e:
            logger.warning(
                "[WF_DBG command_workflows] exception: "
                "channel_id=%s session_id=%s error=%s → returning empty snapshot",
                channel_id,
                session_id,
                e,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=True,
                payload=_build_workflow_snapshot_payload([], session_id=session_id),
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_team_history_get(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """返回 team 模式历史记录的分页，避免与 history.get 并发竞争。

        支持可选 member_name 参数：传入时仅返回与该 member 相关的记录
        （p2p 消息 / @all 广播 / teammate 输出）。
        """
        params = request.params if isinstance(request.params, dict) else {}
        session_id = params.get("session_id")
        member_name = params.get("member_name")
        channel_id = request.channel_id or "web"

        if not isinstance(session_id, str) or not session_id.strip():
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=channel_id,
                ok=False,
                payload={"error": "session_id is required"},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        session_id = session_id.strip()
        try:
            if member_name and isinstance(member_name, str) and member_name.strip():
                records = await asyncio.to_thread(
                    read_member_history_records, session_id, str(member_name).strip()
                )
            else:
                records = await asyncio.to_thread(read_team_history_records, session_id)
        except Exception as exc:  # noqa: BLE001
            logger.warning("[team.history.get] read failed: session_id=%s error=%s", session_id, exc)
            records = []

        sanitized_records = [
            _sanitize_history_record_for_wire(record)
            for record in records
            if isinstance(record, dict)
        ]
        total = len(sanitized_records)
        cursor = _coerce_int(
            params.get("cursor", params.get("offset", 0)),
            default=0,
            minimum=0,
            maximum=max(0, total),
        )
        limit = _coerce_int(
            params.get("limit"),
            default=_TEAM_HISTORY_DEFAULT_LIMIT,
            minimum=1,
            maximum=_TEAM_HISTORY_MAX_LIMIT,
        )
        max_bytes = _coerce_int(
            params.get("max_bytes"),
            default=_TEAM_HISTORY_DEFAULT_MAX_BYTES,
            minimum=_TEAM_HISTORY_MIN_MAX_BYTES,
            maximum=_TEAM_HISTORY_MAX_MAX_BYTES,
        )
        page_records, next_cursor = _select_history_record_page(
            sanitized_records,
            cursor=cursor,
            limit=limit,
            max_bytes=max_bytes,
            session_id=session_id,
        )
        logger.debug(
            "[team.history.get] session_id=%s member=%s total=%d cursor=%d returned=%d next_cursor=%d max_bytes=%d",
            session_id, str(member_name or ""), total, cursor,
            len(page_records), next_cursor, max_bytes,
        )

        resp = AgentResponse(
            request_id=request.request_id,
            channel_id=channel_id,
            ok=True,
            payload={
                "records": page_records,
                "session_id": session_id,
                "cursor": cursor,
                "next_cursor": next_cursor,
                "has_more": next_cursor < total,
                "total": total,
            },
        )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_history_get_stream(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        session_id = params.get("session_id")
        page_idx = params.get("page_idx")
        data = self.get_conversation_history(session_id=session_id, page_idx=page_idx)
        if data is None:
            err_chunk = AgentResponseChunk(
                request_id=request.request_id,
                channel_id=request.channel_id,
                payload={
                    "event_type": "chat.error",
                    "error": "invalid page_idx or session history not found",
                },
                is_complete=True,
            )
            wire = encode_agent_chunk_for_wire(
                err_chunk,
                response_id=request.request_id,
                sequence=0,
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        messages = data.get("messages", [])
        total_pages = data.get("total_pages")
        page = data.get("page_idx")
        if isinstance(messages, list):
            for seq, item in enumerate(messages):
                chunk = AgentResponseChunk(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    payload={
                        "event_type": "history.message",
                        "message": item,
                        "session_id": str(session_id or ""),
                        "total_pages": total_pages,
                        "page_idx": page,
                    },
                    is_complete=False,
                )
                wire = encode_agent_chunk_for_wire(
                    chunk,
                    response_id=request.request_id,
                    sequence=seq,
                )
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))

        done_chunk = AgentResponseChunk(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={
                "event_type": "history.message",
                "status": "done",
                "session_id": str(session_id or ""),
                "total_pages": total_pages,
                "page_idx": page,
            },
            is_complete=True,
        )
        done_seq = len(messages) if isinstance(messages, list) else 0
        wire_done = encode_agent_chunk_for_wire(
            done_chunk,
            response_id=request.request_id,
            sequence=done_seq,
        )
        async with send_lock:
            await ws.send(json.dumps(wire_done, ensure_ascii=False))

    async def _handle_command_add_dir(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            directory_path = params.get("path")
            remember = params.get("remember", False)
            persist: dict[str, Any]
            if directory_path is None or (
                    isinstance(directory_path, str) and not directory_path.strip()
            ):
                persist = {"ok": False, "error": "path is required"}
            else:
                persist = persist_cli_trusted_directory(str(directory_path))
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=bool(persist.get("ok", False)),
                payload={
                    "path": directory_path,
                    "remember": remember,
                    "persist": persist,
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.add_dir failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_chrome(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.chrome failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_compact(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            session_id = request.session_id or "default"
            params = request.params or {}

            channel_id = request.channel_id or "default"
            mode, sub_mode, _ = resolve_agent_request_mode(params.get("mode", "agent.plan"))
            agent_mode = "agent" if mode == "auto_harness" else mode
            agent = await self._agent_manager.get_agent(
                channel_id=channel_id,
                mode=agent_mode,
                project_dir=resolve_request_project_dir(request),
                sub_mode=sub_mode,
            )

            if agent is None:
                raise ValueError("Failed to get agent")

            result_data = await agent.compress_context(session_id=session_id, return_state=True)

            result = result_data.get("result")
            stats = result_data.get("stats")
            state = result_data.get("state") if isinstance(result_data.get("state"), dict) else {}
            summary = str(
                result_data.get("compact_summary")
                or state.get("compact_summary")
                or result_data.get("summary")
                or ""
            ).strip()

            if result == "compressed" and stats:
                before_tokens = stats.get("raw_total_tokens", 0)
                after_tokens = stats.get("total_tokens", 0)
                if before_tokens > 0:
                    rate = round((before_tokens - after_tokens) / before_tokens * 100, 1)
                else:
                    rate = 0
                stats_summary = (
                    f"\u2713 Context compacted: {after_tokens / 1000:.1f}K/"
                    f"{before_tokens / 1000:.1f}K tokens ({rate:.1f}% saved)"
                )

                await self.send_push({
                    "channel_id": channel_id,
                    "session_id": session_id,
                    "payload": {
                        "event_type": "context.compressed",
                        "rate": rate,
                        "beforeCompressed": before_tokens,
                        "afterCompressed": after_tokens,
                    },
                })
                if summary:
                    append_compact_history_records(
                        session_id=session_id,
                        request_id=request.request_id,
                        channel_id=channel_id,
                        summary=summary,
                        timestamp=_dt.datetime.now().timestamp(),
                        trigger="manual",
                        stats=stats,
                        mode=params.get("mode", "agent.plan"),
                    )
                    compression_state_payload: dict[str, Any] = {
                        **state,
                        "event_type": "context.compression_state",
                        "status": state.get("status") or "compressed",
                        "phase": state.get("phase") or "active_compress",
                        "processor": state.get("processor") or _extract_compact_summary_processor(summary),
                        "before": state.get("before") or {"tokens": before_tokens},
                        "after": state.get("after") or {"tokens": after_tokens},
                        "saved": state.get("saved") or {
                            "tokens": before_tokens - after_tokens,
                            "percent": rate,
                        },
                        "summary": stats_summary,
                        "compact_summary": summary,
                    }
                    await self.send_push({
                        "channel_id": channel_id,
                        "session_id": session_id,
                        "payload": compression_state_payload,
                    })

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "result": result,
                    "stats": stats,
                    **({"summary": summary} if summary else {}),
                    **({"compact_summary": summary} if summary else {}),
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.compact failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_compact_partial(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            session_id = request.session_id or "default"
            params = request.params or {}
            turn_index = int(params.get("turn_index", 0))
            direction = str(params.get("direction") or "from").strip()

            channel_id = request.channel_id or "default"
            mode, sub_mode, _ = resolve_agent_request_mode(params.get("mode", "agent.plan"))
            agent_mode = "agent" if mode == "auto_harness" else mode
            agent = await self._agent_manager.get_agent(
                channel_id=channel_id,
                mode=agent_mode,
                project_dir=resolve_request_project_dir(request),
                sub_mode=sub_mode,
            )

            if agent is None:
                raise ValueError("Failed to get agent")

            result_data = await agent.compact_partial(
                session_id=session_id,
                turn_index=turn_index,
                direction=direction,
            )

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=result_data,
            )
        except BaseException as e:
            if isinstance(e, (KeyboardInterrupt, asyncio.CancelledError)):
                raise
            logger.exception("[AgentWebSocketServer] command.compact_partial failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "status": "failed",
                    "error": str(e),
                },
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_context(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            session_id = request.session_id or "default"
            params = request.params or {}

            channel_id = request.channel_id or "default"
            mode, sub_mode, _ = resolve_agent_request_mode(params.get("mode", "agent.plan"))
            agent_mode = "agent" if mode == "auto_harness" else mode
            agent = await self._agent_manager.get_agent(
                channel_id=channel_id,
                mode=agent_mode,
                project_dir=resolve_request_project_dir(request),
                sub_mode=sub_mode,
            )

            if agent is None:
                raise ValueError("Failed to get agent")

            result_data = await agent.get_context_usage(session_id=session_id)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=result_data,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.context failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_recap(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 /recap 命令：生成会话快速回顾（read-only，不修改历史）"""
        try:
            session_id = request.session_id or "default"
            params = request.params or {}
            channel_id = request.channel_id or "default"
            mode, sub_mode, _ = resolve_agent_request_mode(params.get("mode", "agent.plan"))
            agent_mode = "agent" if mode == "auto_harness" else mode

            agent = await self._agent_manager.get_agent(
                channel_id=channel_id,
                mode=agent_mode,
                project_dir=resolve_request_project_dir(request),
                sub_mode=sub_mode,
            )

            if agent is None:
                raise ValueError("Failed to get agent")

            result_data = await agent.generate_recap(session_id=session_id)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=result_data,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.recap failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "status": "failed",
                    "error": str(e),
                },
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_btw(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 /btw 命令：独立、无工具、单轮 LLM 侧问题查询。

        - 获取当前会话上下文（最近消息）
        - 用隔离的 LLM 查询回答问题
        - 不修改对话历史
        - 不使用任何工具（纯文本回答）
        - 仅单轮（无后续 token 消耗）
        """
        try:
            session_id = request.session_id or "default"
            params = request.params or {}
            channel_id = request.channel_id or "default"
            question = (params.get("question") or "").strip()

            logger.info(
                "[AgentWebSocketServer] command.btw received: session_id=%s question=%s",
                session_id,
                question[:100] if question else "",
            )

            if not question:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"status": "failed", "error": "Question is required"},
                )
                wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
                async with send_lock:
                    await ws.send(json.dumps(wire, ensure_ascii=False))
                return

            mode, sub_mode, _ = resolve_agent_request_mode(params.get("mode", "agent.plan"))
            agent_mode = "agent" if mode == "auto_harness" else mode

            agent = await self._agent_manager.get_agent(
                channel_id=channel_id,
                mode=agent_mode,
                project_dir=resolve_request_project_dir(request),
                sub_mode=sub_mode,
            )

            if agent is None:
                raise ValueError("Failed to get agent")

            result_data = await agent.generate_btw_answer(
                session_id=session_id,
                question=question,
            )

            logger.info(
                "[AgentWebSocketServer] command.btw result: status=%s",
                result_data.get("status"),
            )

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=result_data,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.btw failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={
                    "status": "failed",
                    "error": str(e),
                },
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_diff(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from jiuwenswarm.server.utils.diff_service import get_diff_service

        try:
            session_id = request.session_id or "default"
            project_dir = resolve_request_project_dir(request)
            diff_service = get_diff_service()
            turns, git_diff = await asyncio.gather(
                asyncio.to_thread(diff_service.get_turn_diffs, session_id, project_dir),
                asyncio.to_thread(diff_service.get_git_diff, project_dir),
            )

            logger.info(
                "[AgentWebSocketServer] command.diff response: session_id=%s turns=%s git_diff=%s project_dir=%s",
                session_id,
                len(turns),
                git_diff is not None,
                project_dir,
            )

            payload: dict[str, Any] = {
                "type": "list",
                "turns": turns,
            }
            if git_diff is not None:
                payload["gitDiff"] = git_diff

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] command.diff failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_simplify(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """处理 /simplify 命令：组装代码精简审查 prompt 并返回（由前端作为消息发送给 Agent）。

        prompt 指导 Agent 分三阶段完成
        1) 识别改动（git diff）
        2) 三维度审查（复用 / 质量 / 效率）—— 子 Agent 并行审查为可选优化手段
        3) 聚合发现并直接修复
        """
        try:
            params = request.params or {}
            target = str(params.get("target", "")).strip()

            prompt = _build_simplify_prompt(target)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"prompt": prompt},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.simplify failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_model(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            action = params.get("action")

            if action == "add_model":
                target = str(params.get("target", "")).strip()
                logger.info("[command.model] add_model: target=%s", target)
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"type": "model_added", "name": target},
                )

            elif action == "switch_model":
                target = str(params.get("model", "")).strip()
                env_updates = params.get("env_updates", {})
                logger.info(
                    "[command.model] switch_model: target=%s, env_updates=%s",
                    target,
                    {k: (v if k != "API_KEY" else "***") for k, v in env_updates.items()},
                )

                if not env_updates:
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=False,
                        payload={"error": "No env_updates provided"},
                    )
                elif _is_env_api_base_placeholder(env_updates):
                    api_base_val = str(env_updates.get("API_BASE", ""))
                    logger.warning(
                        "[command.model] switch_model rejected: API_BASE is a placeholder domain: %s",
                        api_base_val,
                    )
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=False,
                        payload={
                            "error": f"API_BASE '{api_base_val}' 指向占位域名，无法实际提供服务，请配置有效的 API 地址",
                        },
                    )
                else:
                    for k, v in env_updates.items():
                        os.environ[k] = v
                    logger.info("[command.model] os.environ 已更新, MODEL_NAME=%s", os.getenv("MODEL_NAME", "unknown"))

                    try:
                        from jiuwenswarm.agents.harness.common.memory.config import clear_config_cache
                        clear_config_cache()
                        logger.info("[command.model] config cache 已清除")
                    except Exception as e:
                        logger.debug("[command.model] clear_config_cache skipped: %s", e)

                    try:
                        await self._agent_manager.reload_agents_config(None, env_updates)
                        logger.info("[command.model] agent config 已重载")
                    except Exception as e:
                        logger.debug("[command.model] reload_agents_config skipped: %s", e)

                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=True,
                        payload={
                            "current": os.getenv("MODEL_NAME", "unknown"),
                            "requested": target,
                            "type": "switched",
                            "applied": True,
                        },
                    )
                    logger.info("[command.model] 切换完成: current=%s", os.getenv("MODEL_NAME", "unknown"))

            else:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"current": os.getenv("MODEL_NAME", "unknown"), "available": ["default-model"]},
                )

        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.model failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    @staticmethod
    def _mask_sensitive_fields(payload: Any) -> Any:
        if isinstance(payload, dict):
            masked: dict[str, Any] = {}
            for key, value in payload.items():
                key_text = str(key).lower()
                value_text = value.lower() if isinstance(value, str) else ""
                key_sensitive = any(
                    token in key_text for token in ("api_key", "token", "authorization", "secret")
                )
                value_sensitive = any(token in value_text for token in ("bearer ", "api-key ", "secret-"))
                if key_sensitive or value_sensitive:
                    masked[key] = "***"
                else:
                    masked[key] = AgentWebSocketServer._mask_sensitive_fields(value)
            return masked
        if isinstance(payload, list):
            return [AgentWebSocketServer._mask_sensitive_fields(item) for item in payload]
        return payload

    @staticmethod
    async def _pre_check_mcp_server(server_payload: dict[str, Any]) -> tuple[bool, str]:
        """Try a temporary connection to verify the MCP server is reachable.

        Uses ``logging.disable(CRITICAL)`` to silence the SDK's verbose
        "Failed to parse JSONRPC message" tracebacks and wraps everything
        in tight timeouts so a broken server cannot block the caller.

        Returns ``(ok, message)``.
        """
        import logging as _logging
        from openjiuwen.core.foundation.tool import McpServerConfig
        from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr

        name = server_payload.get("name", "")
        transport = server_payload.get("transport", "")

        # Build McpServerConfig (same logic as _fetch_mcp_tools_from_config)
        payload: dict[str, Any] = {"server_name": name, "client_type": transport}
        if transport == "stdio":
            command = server_payload.get("command", "")
            if not command:
                return True, "skipped: no command"
            params: dict[str, Any] = {"command": command}
            if isinstance(server_payload.get("args"), list):
                params["args"] = [str(x) for x in server_payload["args"]]
            if isinstance(server_payload.get("cwd"), str) and server_payload["cwd"].strip():
                params["cwd"] = server_payload["cwd"].strip()
            if isinstance(server_payload.get("env"), dict):
                params["env"] = {str(k): str(v) for k, v in server_payload["env"].items()}
            payload["server_path"] = f"stdio://{name}"
            payload["params"] = params
        else:
            url = server_payload.get("url", "")
            if not url:
                return True, "skipped: no url"
            payload["server_path"] = url
            params = {}
            if isinstance(server_payload.get("headers"), dict):
                params["headers"] = {str(k): str(v) for k, v in server_payload["headers"].items()}
            if params:
                payload["params"] = params

        cfg = McpServerConfig(**payload)
        client = ToolMgr._create_client(cfg)
        _logging.disable(_logging.CRITICAL)
        try:
            connected = await asyncio.wait_for(client.connect(), timeout=15.0)
            if not connected:
                return False, f"{name} ({transport}) pre-check failed: connection refused"
            return True, f"{name} ({transport}) pre-check passed"
        except asyncio.TimeoutError:
            return False, f"{name} ({transport}) pre-check failed: connection timed out"
        except Exception as exc:
            return False, f"{name} ({transport}) pre-check failed: {exc}"
        finally:
            _logging.disable(_logging.NOTSET)
            try:
                await asyncio.wait_for(client.disconnect(), timeout=5.0)
            except (asyncio.TimeoutError, Exception):
                pass

    @staticmethod
    async def _fetch_mcp_tools_from_config(entry: dict[str, Any]) -> list[dict[str, Any]]:
        """Create a temporary MCP connection from config entry and list tools."""
        from openjiuwen.core.foundation.tool import McpServerConfig
        from openjiuwen.core.runner.resources_manager.tool_manager import ToolMgr

        name = str(entry.get("name", "")).strip()
        transport = str(entry.get("transport", "")).strip().lower()
        if not name or transport not in {"stdio", "sse", "http", "streamable-http", "streamable_http"}:
            logger.warning("[command.mcp] _fetch skipped: name=%r transport=%r", name, transport)
            return []

        # Build McpServerConfig same as interface_deep._build_mcp_server_config
        payload: dict[str, Any] = {"server_name": name, "client_type": transport}
        if transport == "stdio":
            command = str(entry.get("command", "")).strip()
            if not command:
                logger.warning("[command.mcp] _fetch skipped: no command for stdio")
                return []
            params: dict[str, Any] = {"command": command}
            if isinstance(entry.get("args"), list):
                params["args"] = [str(x) for x in entry["args"]]
            if isinstance(entry.get("cwd"), str) and entry["cwd"].strip():
                params["cwd"] = entry["cwd"].strip()
            if isinstance(entry.get("env"), dict):
                params["env"] = {str(k): str(v) for k, v in entry["env"].items()}
            payload["server_path"] = f"stdio://{name}"
            payload["params"] = params
        else:
            url = str(entry.get("url", "")).strip()
            if not url:
                logger.warning("[command.mcp] _fetch skipped: no url for sse")
                return []
            payload["server_path"] = url
            params = {}
            if isinstance(entry.get("headers"), dict):
                params["headers"] = {str(k): str(v) for k, v in entry["headers"].items()}
            if params:
                payload["params"] = params

        cfg = McpServerConfig(**payload)
        client = ToolMgr._create_client(cfg)
        try:
            connected = await client.connect()
            if not connected:
                return []
            cards = await client.list_tools()
            tools_info = []
            for card in (cards or []):
                params_schema = card.input_params if hasattr(card, "input_params") else {}
                if hasattr(params_schema, "model_dump"):
                    params_schema = params_schema.model_dump()
                tools_info.append({
                    "id": card.id,
                    "name": card.name,
                    "description": card.description or "",
                    "parameters": params_schema,
                    "server_name": name,
                })
            return tools_info
        finally:
            try:
                await client.disconnect()
            except Exception as exc:
                logger.warning("[command.mcp] _fetch disconnect failed: %s", exc)

    @staticmethod
    def _normalize_mcp_payload(
            params: dict[str, Any], current: dict[str, Any] | None = None
    ) -> dict[str, Any]:
        merged = dict(current or {})
        merged.update(params)
        name = str(merged.get("name", "")).strip()
        transport = str(merged.get("transport", "")).strip().lower()
        if not name:
            raise ValueError("MCP server name is required")
        if transport not in {"stdio", "sse", "http", "streamable-http", "streamable_http"}:
            raise ValueError("transport must be one of stdio|sse|http")

        payload: dict[str, Any] = {
            "name": name,
            "enabled": bool(merged.get("enabled", True)),
            "transport": transport,
        }
        if transport == "stdio":
            command = str(merged.get("command", "")).strip()
            if not command:
                raise ValueError("stdio transport requires command")
            payload["command"] = command
            args = merged.get("args")
            if isinstance(args, list):
                payload["args"] = [str(item) for item in args]
            cwd = merged.get("cwd")
            if isinstance(cwd, str) and cwd.strip():
                payload["cwd"] = cwd.strip()
            env = merged.get("env")
            if isinstance(env, dict):
                payload["env"] = {str(k): str(v) for k, v in env.items()}
        else:
            url = str(merged.get("url", "")).strip()
            if not url:
                raise ValueError(f"{transport} transport requires url")
            payload["url"] = url
            headers = merged.get("headers")
            if isinstance(headers, dict):
                payload["headers"] = {str(k): str(v) for k, v in headers.items()}
            timeout_s = merged.get("timeout_s")
            if isinstance(timeout_s, (int, float)):
                payload["timeout_s"] = int(timeout_s)
        return payload

    def _normalize_mcp_add_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        return self._normalize_mcp_payload(params)

    def _normalize_mcp_update_payload(self, params: dict[str, Any]) -> dict[str, Any]:
        name = str(params.get("name", "")).strip()
        if not name:
            raise ValueError("MCP server name is required")
        current = get_mcp_server_config(name)
        if current is None:
            raise KeyError(f"MCP server '{name}' not found")
        return self._normalize_mcp_payload(params, current=current)

    async def _handle_command_mcp(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            action = str(params.get("action", "list")).strip().lower()

            if action == "list":
                items = [self._mask_sensitive_fields(item) for item in get_mcp_servers()]
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"type": "list", "items": items},
                )
            elif action == "show":
                name = str(params.get("name", "")).strip()
                if name:
                    item = get_mcp_server_config(name)
                    if item is None:
                        raise KeyError(f"MCP server '{name}' not found")
                    masked = self._mask_sensitive_fields(item)
                    # Enrich with tool count
                    tool_count = 0
                    try:
                        from openjiuwen.core.runner import Runner
                        resource_registry = getattr(Runner.resource_mgr, "_resource_registry", None)
                        if resource_registry is not None:
                            tool_mgr = resource_registry.tool()
                            server_ids = tool_mgr.get_mcp_server_ids(name)
                            if not server_ids:
                                for sid, res in getattr(tool_mgr, "_mcp_server_resources", {}).items():
                                    if getattr(res.config, "server_name", "") == name:
                                        server_ids.append(sid)
                            _seen: set[str] = set()
                            for sid in server_ids:
                                for _tid in tool_mgr.get_mcp_tool_ids(sid):
                                    _t = getattr(tool_mgr, "_tools", {}).get(_tid)
                                    if _t is not None and hasattr(_t, "card"):
                                        _n = _t.card.name
                                        if _n not in _seen:
                                            _seen.add(_n)
                                            tool_count += 1
                    except Exception as exc:
                        logger.debug("[command.mcp] show tool_count from ToolMgr failed: %s", exc)
                    # If ToolMgr has no data, try temporary connection
                    if tool_count == 0 and bool(item.get("enabled", True)):
                        try:
                            tools = await self._fetch_mcp_tools_from_config(item)
                            tool_count = len(tools)
                        except Exception as exc:
                            logger.warning("[command.mcp] show tool_count from temp connection failed: %s", exc)
                    masked["tool_count"] = tool_count
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=True,
                        payload={"type": "detail", "item": masked},
                    )
                else:
                    enabled_items = [
                        self._mask_sensitive_fields(item)
                        for item in get_mcp_servers()
                        if bool(item.get("enabled", True))
                    ]
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=True,
                        payload={"type": "list", "items": enabled_items},
                    )
            elif action == "add":
                server_payload = self._normalize_mcp_add_payload(params)

                # Pre-check: only validate when stdio args contain a local file
                # path (e.g. "node /path/to/server.js").  Skip for package
                # managers like npx which may need to download first.
                pre_check_failed = False
                if bool(server_payload.get("enabled", True)):
                    _need_pre_check = False
                    if server_payload.get("transport") == "stdio":
                        _args = server_payload.get("args")
                        if isinstance(_args, list):
                            _need_pre_check = any(
                                isinstance(a, str)
                                and (a.startswith(("/", "./", "../"))
                                     or a.endswith((".js", ".mjs", ".json", ".py")))
                                for a in _args
                            )
                    if _need_pre_check:
                        check_ok, check_msg = await self._pre_check_mcp_server(server_payload)
                        if not check_ok:
                            logger.warning("[command.mcp] add pre-check failed: %s", check_msg)
                            resp = AgentResponse(
                                request_id=request.request_id,
                                channel_id=request.channel_id,
                                ok=False,
                                payload={
                                    "type": "add_failed",
                                    "name": server_payload["name"],
                                    "error": check_msg,
                                },
                            )
                            pre_check_failed = True
                        else:
                            logger.info("[command.mcp] add pre-check ok: %s", check_msg)

                if not pre_check_failed:
                    # 对于 update，先读旧配置，判断是否真有变化
                    name = server_payload.get("name", "")
                    old_item = get_mcp_server_config(name) if name else None

                    _, created = upsert_mcp_server_in_config(server_payload)
                    applied = True
                    error_message = ""

                    # 判断是否需要 reload: 新增必然需要；更新时做完整比较，
                    # 配置完全一致才跳过（dict 比较成本极低，避免漏字段导致改了不生效）。
                    config_changed = created
                    if not created and old_item is not None:
                        config_changed = (dict(old_item) != dict(server_payload))
                        if not config_changed:
                            logger.info(
                                "[command.mcp] add/update skipped reload: '%s' config unchanged", name
                            )

                    if config_changed:
                        try:
                            await self._agent_manager.reload_agents_config(get_config(), None)
                        except Exception as reload_exc:  # noqa: BLE001
                            applied = False
                            error_message = str(reload_exc)
                            logger.warning("[command.mcp] reload after add failed: %s", reload_exc)

                    resp_payload: dict[str, Any] = {
                        "type": "added" if created else "updated",
                        "name": server_payload["name"],
                        "applied": applied,
                    }
                    if error_message:
                        resp_payload["error"] = error_message
                    resp = AgentResponse(
                        request_id=request.request_id,
                        channel_id=request.channel_id,
                        ok=True,
                        payload=resp_payload,
                    )
            elif action in {"enable", "disable"}:
                name = str(params.get("name", "")).strip()
                if not name:
                    raise ValueError("MCP server name is required")
                enabled = action == "enable"

                # 读取旧状态以判断 enabled 是否真的变化（容忍读取失败/不存在，
                # 此时回退为"按变化处理"，由 set_mcp_server_enabled_in_config 自己
                # 校验存在性并在缺失时抛 KeyError 交外层统一处理）。
                old_enabled = None
                try:
                    old_item = get_mcp_server_config(name)
                    if old_item is not None:
                        old_enabled = bool(old_item.get("enabled", True))
                except Exception:  # noqa: BLE001
                    old_enabled = None

                # set_mcp_server_enabled_in_config 在 server 不存在时抛 KeyError，
                # 由外层统一返回 MCP_NOT_FOUND。
                item = set_mcp_server_enabled_in_config(name, enabled)

                # 只有 enabled 状态真的改变才需要 reload；无法判断旧状态时保守 reload。
                config_changed = (old_enabled is None) or (old_enabled != enabled)
                if not config_changed:
                    logger.info(
                        "[command.mcp] %s skipped reload: '%s' already %s",
                        action, name, "enabled" if enabled else "disabled",
                    )

                applied = True
                error_message = ""
                if config_changed:
                    try:
                        await self._agent_manager.reload_agents_config(get_config(), None)
                    except Exception as reload_exc:  # noqa: BLE001
                        applied = False
                        error_message = str(reload_exc)
                        logger.warning("[command.mcp] reload after %s failed: %s", action, reload_exc)

                payload = {
                    "type": "enabled" if enabled else "disabled",
                    "name": name,
                    "applied": applied,
                    "item": self._mask_sensitive_fields(item),
                }
                if error_message:
                    payload["error"] = error_message
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload=payload,
                )
            elif action in {"remove", "delete"}:
                name = str(params.get("name", "")).strip()
                if not name:
                    raise ValueError("MCP server name is required")
                # remove_mcp_server_in_config 在 server 不存在时抛 KeyError，
                # 由外层统一返回 MCP_NOT_FOUND，且不会触发 reload（删除不存在 = 无变化）。
                removed = remove_mcp_server_in_config(name)
                applied = True
                error_message = ""
                try:
                    await self._agent_manager.reload_agents_config(get_config(), None)
                except Exception as reload_exc:  # noqa: BLE001
                    applied = False
                    error_message = str(reload_exc)
                    logger.warning("[command.mcp] reload after remove failed: %s", reload_exc)
                payload = {
                    "type": "removed",
                    "name": name,
                    "applied": applied,
                    "item": self._mask_sensitive_fields(removed),
                }
                if error_message:
                    payload["error"] = error_message
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload=payload,
                )
            elif action == "update":
                normalized = self._normalize_mcp_update_payload(params)
                _, _created = upsert_mcp_server_in_config(normalized)
                applied = True
                error_message = ""
                try:
                    await self._agent_manager.reload_agents_config(get_config(), None)
                except Exception as reload_exc:  # noqa: BLE001
                    applied = False
                    error_message = str(reload_exc)
                    logger.warning("[command.mcp] reload after update failed: %s", reload_exc)
                payload = {
                    "type": "updated",
                    "name": normalized["name"],
                    "applied": applied,
                    "item": self._mask_sensitive_fields(normalized),
                }
                if error_message:
                    payload["error"] = error_message
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload=payload,
                )
            elif action == "list_tools":
                name = str(params.get("name", "")).strip()
                if not name:
                    raise ValueError("MCP server name is required")
                tools_info: list[dict[str, Any]] = []
                # 1) Try from ToolMgr (already registered)
                try:
                    from openjiuwen.core.runner import Runner
                    resource_registry = getattr(Runner.resource_mgr, "_resource_registry", None)
                    if resource_registry is not None:
                        tool_mgr = resource_registry.tool()
                        server_ids = list(tool_mgr.get_mcp_server_ids(name))
                        if not server_ids:
                            for sid, res in getattr(tool_mgr, "_mcp_server_resources", {}).items():
                                if getattr(res.config, "server_name", "") == name:
                                    server_ids.append(sid)
                        seen_tool_names: set[str] = set()
                        for sid in server_ids:
                            tool_ids = tool_mgr.get_mcp_tool_ids(sid)
                            for tid in tool_ids:
                                tool = getattr(tool_mgr, "_tools", {}).get(tid)
                                if tool is not None and hasattr(tool, "card"):
                                    card = tool.card
                                    if card.name in seen_tool_names:
                                        continue
                                    seen_tool_names.add(card.name)
                                    params_schema = card.input_params if hasattr(card, "input_params") else {}
                                    if hasattr(params_schema, "model_dump"):
                                        params_schema = params_schema.model_dump()
                                    tools_info.append({
                                        "id": card.id,
                                        "name": card.name,
                                        "description": card.description or "",
                                        "parameters": params_schema,
                                        "server_name": name,
                                    })
                except Exception as exc:
                    logger.debug("[command.mcp] list_tools from ToolMgr failed: %s", exc)
                # 2) If no tools found, try temporary MCP connection from config
                if not tools_info:
                    try:
                        config_entry = get_mcp_server_config(name)
                        if config_entry and bool(config_entry.get("enabled", True)):
                            tools_info = await self._fetch_mcp_tools_from_config(config_entry)
                    except Exception as exc:
                        logger.warning("[command.mcp] list_tools from temp connection failed: %s", exc)
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"type": "tools", "tools": tools_info, "server_name": name},
                )
            else:
                raise ValueError("Unsupported action, must be one of " \
                                 "list|show|add|update|enable|disable|remove|list_tools")
        except KeyError as exc:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "MCP_NOT_FOUND"},
            )
        except ValueError as exc:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "MCP_BAD_REQUEST"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.mcp failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "MCP_INTERNAL"},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_sandbox(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理 ``/sandbox`` 命令.

        子命令通过 ``params["sub"]`` 路由:
        - ``status`` / ``enable`` / ``disable``
        - ``exclude.add`` / ``exclude.remove`` / ``exclude.list``
        - ``files.allow`` / ``files.deny`` / ``files.list``

        ``enable``/``disable`` 走 ``agent_manager.recreate_agent`` (重建 sys_operation 类型);
        其他写动作通过 ``adapter.apply_sandbox_runtime_patch()`` 立即热更,
        不重建 agent.
        """
        params = request.params or {}
        sub = str(params.get("sub", "status")).strip().lower()
        channel_id = request.channel_id or "default"
        try:
            # 平台守卫: ``/sandbox`` 全家桶仅在 Linux 上可用。 放在 try 内部是
            # 故意的, 让 ValueError 命中下方 ``except ValueError`` 分支转成
            # ``SANDBOX_BAD_REQUEST`` 回执, 跟其它入参校验失败的处理一致。
            _require_sandbox_supported()
            validate_sandbox_files_runtime(get_sandbox_runtime().get("files"))
            if sub == "status":
                payload = {"runtime": get_sandbox_runtime()}
            elif sub == "enable":
                payload = await self._handle_sandbox_enable(channel_id)
            elif sub == "disable":
                payload = await self._handle_sandbox_disable(channel_id)
            elif sub == "exclude.add":
                payload = await self._handle_sandbox_exclude_add(channel_id, params)
            elif sub == "exclude.remove":
                payload = await self._handle_sandbox_exclude_remove(channel_id, params)
            elif sub == "exclude.list":
                payload = {"excluded_commands": list(get_sandbox_runtime().get("excluded_commands") or [])}
            elif sub == "files.allow":
                payload = await self._handle_sandbox_files_set(channel_id, params, bucket="allow")
            elif sub == "files.deny":
                payload = await self._handle_sandbox_files_set(channel_id, params, bucket="deny")
            elif sub == "files.remove":
                payload = await self._handle_sandbox_files_remove(channel_id, params)
            elif sub == "files.list":
                payload = {"files": dict(get_sandbox_runtime().get("files") or {})}
            else:
                raise ValueError(f"unknown sub: {sub!r}")
            self._attach_effective_sandbox_files(payload, channel_id, params)
            await self._attach_landlock_status(payload)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except ValueError as exc:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "SANDBOX_BAD_REQUEST"},
            )
        except Exception as exc:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.sandbox failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "SANDBOX_INTERNAL"},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_sandbox_enable(self, channel_id: str) -> dict[str, Any]:
        # 1. 解析 sandbox endpoint: 优先 config.yaml::sandbox.url/type, 缺省走本地 jiuwenbox.
        # ``get_sandbox_endpoint`` 已经把 startup_mode / policy_file 的归一化值一并返回:
        # - startup_mode 缺省/非法 → "internal"
        # - policy_file 缺省 → "" (此处再回落到 DEFAULT_SANDBOX_POLICY_FILE)
        endpoint = get_sandbox_endpoint()
        url = endpoint.get("url") or "http://127.0.0.1:8321"
        sandbox_type = endpoint.get("type") or "jiuwenbox"

        # startup_mode:
        # - internal: agent-server 通过 JiuwenBoxRunner 拉起 jiuwenbox (默认行为);
        # - external: 用户自己启动 jiuwenbox (例如需要 sudo + network.mode: isolated),
        #   本侧只做健康检查, 不可达直接报错并提示如何手动启动。
        startup_mode = endpoint.get("startup_mode") or DEFAULT_SANDBOX_STARTUP_MODE

        # policy_file:
        # - 仅文件名 → 在 jiuwenbox/configs 下查找; 含路径 / 绝对路径 → 整路径使用;
        # - 未配置 → 回落到 DEFAULT_SANDBOX_POLICY_FILE (即 code-agent-policy.yaml),
        #   并在下方与 url/type 一起写回 config.yaml, 让重启后无需再走 fallback 路径。
        raw_policy = endpoint.get("policy_file") or ""
        effective_policy_file = raw_policy or DEFAULT_SANDBOX_POLICY_FILE
        policy_path = resolve_sandbox_policy_path(effective_policy_file)
        if policy_path is None:
            raise RuntimeError(
                f"sandbox.policy_file={effective_policy_file!r} 无法解析: "
                f"仅给出文件名时需能定位到 jiuwenbox/configs 目录, "
                f"否则请在 config.yaml::sandbox.policy_file 里配置绝对路径。",
            )
        if not policy_path.is_file():
            raise RuntimeError(
                f"sandbox policy 文件不存在: {policy_path} "
                f"(原始配置 sandbox.policy_file="
                f"{raw_policy or f'<default:{DEFAULT_SANDBOX_POLICY_FILE}>'!r})",
            )

        # 2. 解析 host:port 并 (internal 模式下) 完成端口分配。
        # external 模式: 直接用配置里的 url, 由用户保证 jiuwenbox 监听在此处。
        # internal 模式: 期望端口被占就换一个随机空闲端口, 不去探测占用方是谁。
        host, preferred_port = self._parse_sandbox_host_port(url)
        if startup_mode == "internal":
            port = self._allocate_internal_jiuwenbox_port(host, preferred_port)
            if port != preferred_port:
                # 端口换过, 同步刷新 url 以便后续落盘 / 透传给前端
                url = f"http://{host}:{port}"
                logger.info(
                    "[command.sandbox] jiuwenbox effective url changed to %s "
                    "(preferred port %d was busy)",
                    url,
                    preferred_port,
                )
        else:
            port = preferred_port

        # 3. 启动 / 健康检查本地 jiuwenbox; 失败直接报错
        ok = await self._jiuwenbox_runner.ensure_running(
            host=host,
            port=port,
            startup_mode=startup_mode,
            policy_path=policy_path,
        )
        if not ok:
            if startup_mode == "external":
                raise RuntimeError(
                    f"jiuwenbox 未在 {host}:{port} 监听 (sandbox.startup_mode=external); "
                    f"请在另一终端先启动 jiuwenbox-server, 例如:\n"
                    f"  sudo -E .venv/bin/python -m uvicorn jiuwenbox.server.app:app "
                    f"--host {host} --port {port}\n"
                    f"  (JIUWENBOX_POLICY_PATH={policy_path})"
                )
            stderr_tail = self._jiuwenbox_runner.get_stderr_tail(20)
            hint = "\n--- jiuwenbox stderr (tail) ---\n" + stderr_tail if stderr_tail else (
                " (no stderr captured; jiuwenbox / uvicorn 可能未安装)"
            )
            raise RuntimeError(
                f"jiuwenbox 启动或健康检查失败 ({host}:{port}){hint}"
            )

        # 4. 把 endpoint 写回 config.yaml, 保证 agent 重建 / agent-server 重启后能直接读到。
        # url 此时已是端口分配后的最终值; startup_mode / policy_file / preserve_file_sharing_mode 一并落盘。
        preserve_mode = resolve_preserve_file_sharing_mode_default()
        try:
            update_sandbox_endpoint(
                url,
                sandbox_type,
                startup_mode=startup_mode,
                policy_file=effective_policy_file,
                preserve_file_sharing_mode=preserve_mode,
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[command.sandbox] persist sandbox endpoint failed: %s", exc)

        runtime = update_sandbox_runtime({"enabled": True})
        await self._agent_manager.recreate_agent(channel_id, immediate=True)

        return {
            "runtime": runtime,
            "endpoint": {
                "url": url,
                "type": sandbox_type,
                "preserve_file_sharing_mode": preserve_mode,
                "startup_mode": startup_mode,
                "policy_file": effective_policy_file,
            },
            "jiuwenbox": {
                "host": host,
                "port": port,
                "ready": True,
                "startup_mode": startup_mode,
                "policy_path": str(policy_path),
            },
            "agent_recreated": True
        }

    async def _handle_sandbox_disable(self, channel_id: str) -> dict[str, Any]:
        runtime = update_sandbox_runtime({"enabled": False})
        await self._agent_manager.recreate_agent(channel_id, immediate=True)

        # 记录关闭前的端点用于回执 (external 模式下 runner 没拥有进程, 会是 None)。
        owned_endpoint = self._jiuwenbox_runner.get_owned_endpoint()
        jiuwenbox_stopped = False
        if owned_endpoint is not None:
            try:
                await self._jiuwenbox_runner.stop()
                jiuwenbox_stopped = True
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "[AgentWebSocketServer] /sandbox disable: jiuwenbox stop failed: %s",
                    exc,
                )
        else:
            logger.debug(
                "[AgentWebSocketServer] /sandbox disable: no owned jiuwenbox to stop "
                "(external startup_mode or never started)"
            )

        payload: dict[str, Any] = {
            "runtime": runtime,
            "agent_recreated": True,
            "jiuwenbox_stopped": jiuwenbox_stopped,
        }
        if owned_endpoint is not None:
            host, port = owned_endpoint
            payload["jiuwenbox"] = {"host": host, "port": port, "ready": False}
        return payload

    async def _handle_sandbox_exclude_add(
        self, channel_id: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        pattern = str(params.get("pattern") or "").strip()
        if not pattern:
            raise ValueError("pattern is required")
        current = get_sandbox_runtime()
        patterns = list(current.get("excluded_commands") or [])
        if pattern in patterns:
            raise ValueError(
                f"excluded_commands already contains {pattern!r}; "
                "use a different pattern or remove it first"
            )
        patterns.append(pattern)
        runtime = update_sandbox_runtime({"excluded_commands": patterns})
        await self._apply_sandbox_runtime_patch(channel_id, runtime, files_changed=False)
        return {"runtime": runtime}

    async def _handle_sandbox_exclude_remove(
        self, channel_id: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        pattern = str(params.get("pattern") or "").strip()
        if not pattern:
            raise ValueError("pattern is required")
        current = get_sandbox_runtime()
        existing = list(current.get("excluded_commands") or [])
        if pattern not in existing:
            raise ValueError(
                f"excluded_commands does not contain {pattern!r}; "
                "nothing to remove"
            )
        patterns = [p for p in existing if p != pattern]
        runtime = update_sandbox_runtime({"excluded_commands": patterns})
        await self._apply_sandbox_runtime_patch(channel_id, runtime, files_changed=False)
        return {"runtime": runtime}

    def _dry_run_files_policy(
        self,
        channel_id: str,
        params: dict[str, Any],
        files: dict[str, Any],
    ) -> None:
        project_dir = self._resolve_active_project_dir(channel_id, params)
        is_code_agent = self._resolve_active_is_code_agent(channel_id)
        try:
            build_filesystem_policy(
                files,
                project_dir=project_dir,
                is_code_agent=is_code_agent,
                startup_mode=get_sandbox_startup_mode(),
            )
        except FileNotFoundError as exc:
            raise ValueError(str(exc)) from exc

    async def _handle_sandbox_files_set(
        self, channel_id: str, params: dict[str, Any], *, bucket: str
    ) -> dict[str, Any]:
        _reject_extra_sandbox_files_params(params)
        path = str(params.get("path") or "").strip()
        if not path:
            raise ValueError("path is required")
        # 把 path 展开成 absolute resolved 形式, 让 ``./foo`` / ``~/data`` /
        # 含 ``..`` 之类写法在入口就被归一化到稳定路径, 避免后续 stat / 入库
        # / 比较行为依赖 jiuwenswarm server 当前 cwd; 见
        # :func:`_canonicalize_sandbox_files_path` 的文档说明。
        canonical = _canonicalize_sandbox_files_path(path)
        if canonical != path:
            logger.info(
                "[sandbox] files %s: canonicalize path %r -> %r",
                bucket, path, canonical,
            )
            path = canonical
        # 拒绝把"自动配置且不可变"的路径 (intrinsic AGENT.md / HEARTBEAT.md /...
        # / daily_memory / 项目目录 / jiuwenswarm config.yaml) 再次写进
        # config.yaml::sandbox.files。 它们由 sysop_builder 在每次
        # build_filesystem_policy 时按需重建; 让用户能 add 只会污染配置, 而且
        # 若一个路径同时在 auto-allow 和用户-deny 里 (反之亦然), 实际行为难以
        # 预期, 不如直接在入口阻断。``params`` 透传给 ``_resolve_active_
        # project_dir`` 以便 TUI 通过 ``trusted_dirs`` / ``cwd`` 显式声明的
        # 项目目录也参与 auto 路径的判定。
        project_dir = self._resolve_active_project_dir(channel_id, params)
        is_code_agent = self._resolve_active_is_code_agent(channel_id)
        match = find_auto_managed_match(
            path,
            project_dir=project_dir,
            is_code_agent=is_code_agent,
            startup_mode=get_sandbox_startup_mode(),
        )
        if match is not None:
            matched_bucket, canonical = match
            raise ValueError(
                f"path is auto-managed (always in {matched_bucket}): {canonical}; "
                f"cannot add via /sandbox files {bucket}"
            )
        current = get_sandbox_runtime()
        files = dict(current.get("files") or {})
        files.setdefault("allow", [])
        files.setdefault("deny", [])
        # 1) 同 bucket 内已经存在等价条目 → 直接报错, 不做 "先删后加" 的隐式覆盖。
        target_list: list[Any] = list(files.get(bucket) or [])
        for existing in target_list:
            if _file_entry_matches_path(existing, path):
                raise ValueError(
                    f"sandbox.files.{bucket} already contains {path!r}; "
                    f"use `/sandbox files remove {path}` first if you want to change it"
                )
        # 2) 反方向 bucket 已经登记了同一条 → allow / deny 在 Landlock 层语义直接
        #    冲突, 拒绝。 用户得先把它从对侧 ``remove`` 掉再加, 显式表达 "我要
        #    切换权限方向" 的意图。
        opposite_bucket = "deny" if bucket == "allow" else "allow"
        for existing in files.get(opposite_bucket) or []:
            if _file_entry_matches_path(existing, path):
                raise ValueError(
                    f"sandbox.files.{opposite_bucket} already contains {path!r}; "
                    f"cannot add the same path to {bucket}. "
                    f"`/sandbox files remove {path}` first if you want to flip it"
                )
        nested_error = find_nested_files_conflict(path, bucket, files)
        if nested_error is not None:
            raise ValueError(nested_error)
        entry: dict[str, Any] = {"path": path}
        target_list.append(entry)
        files[bucket] = target_list
        # 在写盘前做一次 dry-run, 防止后续 build_filesystem_policy 抛错时,
        # yaml 已经被更新成一份永远 build 不出 policy 的中间态 (见
        # :meth:`_dry_run_files_policy` 的文档说明)。
        self._dry_run_files_policy(channel_id, params, files)
        runtime = update_sandbox_runtime({"files": files})
        await self._apply_sandbox_runtime_patch(channel_id, runtime, files_changed=True)
        return {"runtime": runtime}

    async def _handle_sandbox_files_remove(
        self, channel_id: str, params: dict[str, Any]
    ) -> dict[str, Any]:
        _reject_extra_sandbox_files_params(params)
        path = str(params.get("path") or "").strip()
        if not path:
            raise ValueError("path is required")
        # 与 _handle_sandbox_files_set 保持同一份 canonicalize, 让 ``remove
        # ./foo`` 能命中以 absolute 形式入库的 entry; 兼容旧 yaml 残留写法的
        # 兜底由 :func:`_file_entry_matches_path` 双侧 canonicalize 比较负责。
        canonical = _canonicalize_sandbox_files_path(path)
        if canonical != path:
            logger.info(
                "[sandbox] files remove: canonicalize path %r -> %r",
                path, canonical,
            )
            path = canonical
        # 同 _handle_sandbox_files_set: auto-managed 条目由 sysop_builder 在
        # 每次 build_filesystem_policy 时重建, 用户不能也不必通过 /sandbox 删除
        # 它们。如果旧版本 config.yaml 里残留了这些路径, 提示用户直接改 yaml,
        # 而不是让 /sandbox 默默地把同一个 auto-managed 名字从用户配置里抹掉
        # ——后者会让用户误以为他/她真的把 sandbox 自动条目摘掉了。
        project_dir = self._resolve_active_project_dir(channel_id, params)
        is_code_agent = self._resolve_active_is_code_agent(channel_id)
        match = find_auto_managed_match(
            path,
            project_dir=project_dir,
            is_code_agent=is_code_agent,
            startup_mode=get_sandbox_startup_mode(),
        )
        if match is not None:
            matched_bucket, canonical = match
            raise ValueError(
                f"path is auto-managed (always in {matched_bucket}): {canonical}; "
                f"cannot remove via /sandbox files remove"
            )
        current = get_sandbox_runtime()
        files = dict(current.get("files") or {})
        files.setdefault("allow", [])
        files.setdefault("deny", [])
        matched_buckets: list[str] = []
        for bucket in ("allow", "deny"):
            kept: list[Any] = []
            removed = False
            for entry in files.get(bucket) or []:
                if _file_entry_matches_path(entry, path):
                    removed = True
                    continue
                kept.append(entry)
            if removed:
                matched_buckets.append(bucket)
                files[bucket] = kept
        if not matched_buckets:
            raise ValueError(
                f"sandbox.files has no entry for {path!r}; nothing to remove"
            )
        # 与 _handle_sandbox_files_set 对齐: 在写盘前 dry-run, 避免 build 失败
        # 时 yaml 已被写成 build 不出 policy 的死局 (见 :meth:`_dry_run_files
        # _policy` 的文档说明)。
        self._dry_run_files_policy(channel_id, params, files)
        runtime = update_sandbox_runtime({"files": files})
        await self._apply_sandbox_runtime_patch(channel_id, runtime, files_changed=True)
        return {"runtime": runtime}

    def _resolve_active_project_dir(
        self, channel_id: str, params: dict[str, Any] | None = None
    ) -> str | None:
        """Resolve the user project dir for the current ``/sandbox`` view.

        Lookup order, falling through on empty/missing:

        1. ``params["project_dir"]`` -- stable client project identity.
        2. ``adapter._project_dir`` / ``adapter._instance_overrides``.
        3. ``params["cwd"]`` -- legacy/dynamic fallback.
        4. ``params["trusted_dirs"][0]`` -- final compatibility fallback.

        Returns ``None`` only when none of the above yield a usable path; we
        deliberately do NOT fall back to ``Path.cwd()`` of the agent-server
        process because that's typically ``~/.jiuwenswarm`` and would
        mislabel the displayed ``files.allow_write`` entry.
        """
        if isinstance(params, dict):
            project_dir = params.get("project_dir")
            if isinstance(project_dir, str) and project_dir.strip():
                return project_dir.strip()
        try:
            agent = self._agent_manager.get_agent_nowait(channel_id)
        except Exception as exc:
            logger.info("[command.sandbox] get_agent_nowait failed: %s", exc)
            return None
        adapter = self._resolve_adapter(agent)
        if adapter is None:
            return None
        direct = getattr(adapter, "_project_dir", None)
        if direct:
            return str(direct)
        overrides = getattr(adapter, "_instance_overrides", None)
        if isinstance(overrides, dict):
            value = overrides.get("project_dir")
            if value:
                return str(value)
        if isinstance(params, dict):
            cwd_value = params.get("cwd")
            if isinstance(cwd_value, str) and cwd_value.strip():
                return cwd_value.strip()
            trusted_dirs = params.get("trusted_dirs")
            if isinstance(trusted_dirs, (list, tuple)) and trusted_dirs:
                first = str(trusted_dirs[0]).strip()
                if first:
                    return first
        return None

    def _resolve_active_is_code_agent(self, channel_id: str) -> bool:
        """Look up whether ``channel_id``'s adapter is the code-agent flavor.

        Mirrors :meth:`_resolve_active_project_dir`'s adapter lookup so the
        three sandbox call sites (``_dry_run_files_policy``,
        ``_handle_sandbox_files_set`` / ``_remove``'s ``find_auto_managed_
        match``, ``_attach_effective_sandbox_files``'s
        ``list_effective_sandbox_files``) all hand the same flag into
        ``sysop_builder``. Without this, the dry-run / display side would
        always assume non-code-agent and mismatch the actual mount layout
        a Code adapter produces at sandbox-start time (project_dir vs
        ``get_agent_workspace_dir``).

        Returns ``False`` on any failure path (no agent, no adapter, attr
        absent) — that matches the base class default and keeps the dry-run
        / display strictly aligned with what :class:`JiuWenSwarmDeepAdapter`
        emits when ``_is_code_agent`` was never set.
        """
        try:
            agent = self._agent_manager.get_agent_nowait(channel_id)
        except Exception as exc:  # noqa: BLE001
            logger.debug("[command.sandbox] is_code_agent lookup: get_agent_nowait failed: %s", exc)
            return False
        adapter = self._resolve_adapter(agent)
        if adapter is None:
            return False
        return bool(getattr(adapter, "_is_code_agent", False))

    @staticmethod
    def _effective_files_from_adapter(adapter: Any) -> dict[str, list[dict[str, str]]] | None:
        """Read effective sandbox file mounts from the adapter's active sysop card."""
        card = getattr(adapter, "_sys_operation_card", None)
        if card is None:
            return None
        gateway_config = getattr(card, "gateway_config", None)
        launcher = getattr(gateway_config, "launcher_config", None) if gateway_config else None
        extra_params = getattr(launcher, "extra_params", None) if launcher else None
        if not isinstance(extra_params, dict):
            return None
        policy = extra_params.get("policy")
        if not isinstance(policy, dict):
            return None
        return effective_files_from_policy(policy)

    def _attach_effective_sandbox_files(
        self,
        payload: dict[str, Any],
        channel_id: str,
        params: dict[str, Any] | None = None,
    ) -> None:
        """Inject ``effective_files`` into the ``/sandbox`` response payload.

        Prefer the filesystem policy cached on the active adapter's sysop card
        (same payload jiuwenbox uses at exec time). Fall back to a fresh build
        when no matching agent/sysop exists yet.
        """
        try:
            project_dir = self._resolve_active_project_dir(channel_id, params)
            adapter = None
            try:
                agent = self._agent_manager.get_agent_nowait(
                    channel_id,
                    project_dir=project_dir,
                )
            except Exception as exc:  # noqa: BLE001
                logger.debug("[command.sandbox] get_agent_nowait failed: %s", exc)
                agent = None
            if agent is not None:
                adapter = self._resolve_adapter(agent)
            if adapter is not None:
                adapter_project_dir = getattr(adapter, "_project_dir", None)
                if (
                    project_dir
                    and adapter_project_dir
                    and str(adapter_project_dir) != str(project_dir)
                ):
                    logger.warning(
                        "[command.sandbox] project_dir mismatch for effective_files: "
                        "client=%r adapter=%r",
                        project_dir,
                        adapter_project_dir,
                    )
                cached = self._effective_files_from_adapter(adapter)
                if cached is not None:
                    payload["effective_files"] = cached
                    return

            files_runtime: dict[str, Any] | None = None
            runtime = payload.get("runtime")
            if isinstance(runtime, dict):
                rt_files = runtime.get("files")
                if isinstance(rt_files, dict):
                    files_runtime = rt_files
            if files_runtime is None:
                files_in_payload = payload.get("files")
                if isinstance(files_in_payload, dict):
                    files_runtime = files_in_payload
            if files_runtime is None:
                files_runtime = get_sandbox_runtime().get("files") or {}
            is_code_agent = self._resolve_active_is_code_agent(channel_id)
            payload["effective_files"] = list_effective_sandbox_files(
                files_runtime,
                project_dir=project_dir,
                is_code_agent=is_code_agent,
                startup_mode=get_sandbox_startup_mode(),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("[command.sandbox] attach effective_files failed: %s", exc)

    @staticmethod
    def _read_landlock_compatibility(policy_path: Path | None) -> str:
        if policy_path is None or not policy_path.is_file():
            return "best_effort"
        try:
            import yaml

            data = yaml.safe_load(policy_path.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                landlock = data.get("landlock")
                if isinstance(landlock, dict):
                    compat = landlock.get("compatibility")
                    if isinstance(compat, str) and compat.strip():
                        return compat.strip()
        except Exception as exc:
            logger.debug("[command.sandbox] read landlock compatibility failed: %s", exc)
        return "best_effort"

    async def _attach_landlock_status(self, payload: dict[str, Any]) -> None:
        """Attach jiuwenbox Landlock capability summary to sandbox responses."""
        try:
            endpoint = get_sandbox_endpoint()
            jb = payload.get("jiuwenbox")
            if isinstance(jb, dict) and jb.get("host") and jb.get("port"):
                host = str(jb["host"])
                port = int(jb["port"])
            else:
                url = endpoint.get("url") or "http://127.0.0.1:8321"
                host, port = self._parse_sandbox_host_port(url)

            health = await self._jiuwenbox_runner.fetch_health(host, port)
            landlock_supported = bool(health.get("landlock_supported")) if health else False

            policy_file = endpoint.get("policy_file") or DEFAULT_SANDBOX_POLICY_FILE
            policy_path = resolve_sandbox_policy_path(policy_file)
            compatibility = self._read_landlock_compatibility(policy_path)

            payload["landlock"] = {
                "supported": landlock_supported,
                "compatibility": compatibility,
            }
        except Exception as exc:
            logger.warning("[command.sandbox] attach landlock status failed: %s", exc)

    async def _apply_sandbox_runtime_patch(
        self, channel_id: str, runtime: dict[str, Any], *, files_changed: bool
    ) -> None:
        agent = self._agent_manager.get_agent_nowait(channel_id)
        adapter = self._resolve_adapter(agent)
        if adapter is None or not hasattr(adapter, "apply_sandbox_runtime_patch"):
            return
        try:
            await adapter.apply_sandbox_runtime_patch(runtime, files_changed=files_changed)
        except (FileNotFoundError, ValueError) as exc:
            raise ValueError(str(exc)) from exc
        except Exception as exc:
            logger.warning("[command.sandbox] apply_sandbox_runtime_patch failed: %s", exc)

    @staticmethod
    def _resolve_adapter(agent: Any) -> Any:
        """从 JiuwenSwarm 中提取底层 Deep/Code Adapter (持 _sys_operation_card 的实例)."""
        if agent is None:
            return None
        for attr in ("_adapter", "adapter", "_active_adapter"):
            inner = getattr(agent, attr, None)
            if inner is not None and hasattr(inner, "apply_sandbox_runtime_patch"):
                return inner
        # 兜底: agent 本身有相关方法
        if hasattr(agent, "apply_sandbox_runtime_patch"):
            return agent
        return None

    @staticmethod
    def resolve_adapter(agent: Any) -> Any:
        """Public wrapper for :meth:`_resolve_adapter` (避开 protected-access)."""
        return AgentWebSocketServer._resolve_adapter(agent)

    @staticmethod
    def _parse_sandbox_host_port(url: str) -> tuple[str, int]:
        """从 sandbox url 解析 host:port; 默认 127.0.0.1:8321."""
        from urllib.parse import urlparse

        try:
            parsed = urlparse(url)
            host = parsed.hostname or "127.0.0.1"
            port = parsed.port or 8321
        except Exception:
            host, port = "127.0.0.1", 8321
        return host, int(port)

    @staticmethod
    def _is_tcp_port_bindable(host: str, port: int) -> bool:
        """``True`` 表示当前能在 ``host:port`` 上 ``bind`` 成功 (即没有被占用)。

        不去探测 ``/health`` 之类应用层信息——只看四层占用情况, 谁占着、占着的
        是不是 jiuwenbox 都不关心。
        """
        import socket

        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            try:
                sock.bind((host, port))
            except OSError:
                return False
            return True
        finally:
            sock.close()

    @staticmethod
    def _pick_free_tcp_port(host: str) -> int:
        """让内核挑一个空闲端口 (``bind`` 到 0); 仅用于绑定测试, 不会真正监听。

        存在 TOCTOU 风险 (返回后端口可能立即被别人抢), 但接下来 uvicorn 起来
        通常足够快; 即便撞上, uvicorn 自己会因 EADDRINUSE 失败, 上游再报错。
        """
        import socket

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
            sock.bind((host, 0))
            return int(sock.getsockname()[1])

    def _allocate_internal_jiuwenbox_port(
        self,
        host: str,
        preferred_port: int,
    ) -> int:
        """internal 模式下确定 jiuwenbox 实际监听端口。

        - 若本 runner 已经在 ``host:preferred_port`` 上拥有一个仍在跑的 jiuwenbox,
          直接复用 (避免重复 spawn);
        - 否则若 ``preferred_port`` 当前无人占用, 用之;
        - 再否则让内核挑一个空闲端口返回。
        """
        if self._jiuwenbox_runner.is_owned_listener(host, preferred_port):
            return preferred_port
        if self._is_tcp_port_bindable(host, preferred_port):
            return preferred_port
        new_port = self._pick_free_tcp_port(host)
        logger.warning(
            "[command.sandbox] preferred port %s:%d is busy; "
            "allocating fresh port %d for new jiuwenbox instance",
            host,
            preferred_port,
            new_port,
        )
        return new_port

    async def _handle_command_resume(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            query = params.get("query")
            session_id = query if isinstance(query, str) and query.strip() else "sess_mock_resume"
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "session_id": session_id,
                    "query": query if isinstance(query, str) else "",
                    "resumed": True,
                    "preview": "Mock resumed conversation",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.resume failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_session(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            session_id = request.session_id or "sess_mock"
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "session_id": session_id,
                    "remote_url": f"https://example.com/session/{session_id}",
                    "qr_text": f"session:{session_id}",
                },
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.session failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_command_status(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            action = str(params.get("action", "overview")).strip().lower()

            if action == "usage":
                sessions, total = get_all_sessions_metadata(limit=500, offset=0)
                messages_total = sum(s.get("message_count", 0) for s in sessions)
                model_counts: dict[str, int] = {}
                for s in sessions:
                    mode = str(s.get("mode", "unknown"))
                    model_counts[mode] = model_counts.get(mode, 0) + 1
                active_days_set: set[str] = set()
                longest_hours = 0.0
                for s in sessions:
                    created = s.get("created_at", 0)
                    last = s.get("last_message_at", 0)
                    if created:
                        try:
                            day_str = _dt.datetime.fromtimestamp(
                                created, tz=_dt.timezone.utc
                            ).strftime("%Y-%m-%d")
                            active_days_set.add(day_str)
                        except Exception:  # noqa: BLE001
                            pass
                    if created and last:
                        longest_hours = max(longest_hours, (last - created) / 3600)

                models_used = [{"name": k, "count": v} for k, v in sorted(model_counts.items(), key=lambda x: -x[1])]
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={
                        "sessions_total": total,
                        "messages_total": messages_total,
                        "models_used": models_used,
                        "active_days": len(active_days_set),
                        "longest_session_hours": round(longest_hours, 1),
                    },
                )
            elif action == "config":
                config_path = str(get_config_file())
                settings_sources: list[str] = []
                config_dir = os.getenv("JIUWENSWARM_CONFIG_DIR")
                if config_dir:
                    settings_sources.append(f"env:JIUWENSWARM_CONFIG_DIR={config_dir}")
                settings_sources.append(config_path)
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={
                        "config_path": config_path,
                        "settings_sources": settings_sources,
                    },
                )
            else:
                # overview (default)
                config = get_config()
                session_id = request.session_id or ""
                default_models = get_default_models(config)
                active_entry = default_models[0] if default_models else {}
                mcc = active_entry.get("model_client_config", {})
                model_name = str(mcc.get("model_name", "") or config.get("model", ""))
                provider = str(mcc.get("client_provider", "") or config.get("model_provider", ""))
                api_base = str(mcc.get("api_base", "") or config.get("api_base", ""))

                mcp_servers = get_mcp_servers()
                mcp_summary = [
                    {
                        "name": str(s.get("name", "unknown")),
                        "enabled": bool(s.get("enabled", True)),
                        "transport": str(s.get("transport", "unknown")),
                    }
                    for s in mcp_servers
                    if isinstance(s, dict)
                ]

                config_path = str(get_config_file())
                settings_sources: list[str] = []
                config_dir = os.getenv("JIUWENSWARM_CONFIG_DIR")
                if config_dir:
                    settings_sources.append(f"env:JIUWENSWARM_CONFIG_DIR={config_dir}")
                settings_sources.append(config_path)

                # Memory diagnostics — use the actual workspace dir (trusted_dir or cwd),
                # same as ProjectMemoryRail, so we detect JIUWESWARM.md where /init creates it.
                params = request.params or {}
                workspace_dir = str(params.get("cwd", "") or os.getcwd())
                trusted_dirs = params.get("trusted_dirs")
                if isinstance(trusted_dirs, list) and trusted_dirs:
                    workspace_dir = str(trusted_dirs[0])
                try:
                    from jiuwenswarm.agents.harness.common.rails.project_memory import (
                        clear_project_memory_cache,
                        discover_and_load_memory_files,
                        get_large_memory_files,
                    )
                    clear_project_memory_cache(workspace_dir)
                    project_files = discover_and_load_memory_files(
                        workspace=workspace_dir, target_path=workspace_dir,
                    )
                    memory_warnings = get_large_memory_files(project_files)
                    logger.info(
                        "[AgentWebSocketServer] memory diagnostics: "
                        "workspace_dir=%s, files=%d, warnings=%d",
                        workspace_dir, len(project_files), len(memory_warnings),
                    )
                except Exception as exc:
                    logger.warning(
                        "[AgentWebSocketServer] memory diagnostics failed: "
                        "workspace_dir=%s, error=%s",
                        workspace_dir, exc,
                    )
                    memory_warnings = []

                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={
                        "version": __version__,
                        "session_id": session_id,
                        "cwd": str(params.get("cwd", "") or os.getcwd()),
                        "model": model_name,
                        "provider": provider,
                        "api_base": api_base,
                        "connection_status": "connected",
                        "mcp_servers": mcp_summary,
                        "config_path": config_path,
                        "settings_sources": settings_sources,
                        "memory_warnings": memory_warnings,
                    },
                )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] command.status failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_browser_start(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """启动浏览器并返回执行结果（returncode）。"""
        try:
            from jiuwenswarm.agents.harness.common.tools.browser_start_client import start_browser

            config_path = str(get_config_file())
            returncode = start_browser(dry_run=False, config_file=config_path)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"returncode": returncode},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] browser.start failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_browser_runtime_restart(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            from openjiuwen.harness.tools.browser_move import restart_local_browser_runtime_server

            result = restart_local_browser_runtime_server()
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"result": result},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] browser.runtime_restart failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_agents_list(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from dataclasses import asdict as dataclass_asdict
        from jiuwenswarm.server.runtime.agent_config_service import AgentConfigService

        try:
            workspace_dir = request.params.get("workspace_dir") if request.params else None
            service = AgentConfigService(workspace_dir)
            agents = service.list_agents()
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"agents": [dataclass_asdict(a) for a in agents]},
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] agents.list failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_agents_get(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from dataclasses import asdict as dataclass_asdict
        from jiuwenswarm.server.runtime.agent_config_service import AgentConfigService

        try:
            params = request.params or {}
            name = params.get("name", "")
            workspace_dir = params.get("workspace_dir")
            service = AgentConfigService(workspace_dir)
            agent = service.get_agent(name)
            if agent is None:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=False,
                    payload={"error": f"Agent 不存在: {name}"},
                )
            else:
                resp = AgentResponse(
                    request_id=request.request_id,
                    channel_id=request.channel_id,
                    ok=True,
                    payload={"agent": dataclass_asdict(agent)},
                )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] agents.get failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _generate_agent_with_llm(
        self, name: str, description: str
    ) -> tuple[str, str] | None:
        """调用 LLM 生成 agent 的 whenToUse 和 systemPrompt。

        Returns:
            (when_to_use, system_prompt) 或 None（生成失败时回退到模板）
        """
        model = self._resolve_model(None)
        if model is None:
            logger.warning("[agents.create] no model available for LLM generation")
            return None

        from openjiuwen.core.foundation.llm.schema.message import UserMessage

        full_prompt = f"""{_AGENT_CREATION_SYSTEM_PROMPT}

---
请为以下 agent 生成配置：

名称: {name}
描述: {description}

返回 JSON 对象，包含 whenToUse 和 systemPrompt 两个字段。不要返回其他内容。"""

        try:
            result = await model.invoke(
                [UserMessage(content=full_prompt)],
                max_tokens=2000,
                temperature=0.3,
            )
            text = getattr(result, "content", None) or str(result)
        except Exception:
            logger.exception("[agents.create] LLM generation failed")
            return None

        # 解析 JSON 响应
        import re as _re

        import json as _json
        try:
            data = _json.loads(text.strip())
        except _json.JSONDecodeError:
            match = _re.search(r"\{[\s\S]*\}", text)
            if not match:
                logger.warning("[agents.create] no JSON found in LLM response: %s", text[:200])
                return None
            try:
                data = _json.loads(match.group(0))
            except _json.JSONDecodeError:
                logger.warning("[agents.create] JSON parse failed: %s", text[:200])
                return None

        when_to_use = (data.get("whenToUse") or "").strip()
        system_prompt = (data.get("systemPrompt") or "").strip()

        if not when_to_use or not system_prompt:
            logger.warning("[agents.create] incomplete LLM response: %s", data)
            return None

        return when_to_use, system_prompt

    async def _handle_agents_create(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from dataclasses import asdict as dataclass_asdict
        from jiuwenswarm.server.runtime.agent_config_service import AgentConfigService, CreateAgentParams

        try:
            params = dict(request.params or {})
            workspace_dir = params.pop("workspace_dir", None)
            generate = params.pop("generate", True)

            # LLM 生成 when_to_use 和 prompt（失败时回退到请求中的模板值）
            generated = False
            if generate:
                name = params.get("name", "")
                description = params.get("description", "")
                if name and description:
                    llm_result = await self._generate_agent_with_llm(name, description)
                    if llm_result:
                        params["when_to_use"] = llm_result[0]
                        params["prompt"] = llm_result[1]
                        generated = True

            p = CreateAgentParams(**{k: v for k, v in params.items()
                                      if k in CreateAgentParams.__dataclass_fields__})
            service = AgentConfigService(workspace_dir)
            agent = service.create_agent(p)
            # 自动在 config.yaml 中启用新创建的 agent
            applied = True
            reload_error = ""
            try:
                upsert_subagent_in_config(agent.name, enabled=True)
                await self._agent_manager.reload_agents_config(get_config(), None)
            except Exception as reload_exc:
                applied = False
                reload_error = str(reload_exc)
                logger.warning("[AgentWebSocketServer] agents.create reload failed: %s", reload_exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "agent": dataclass_asdict(agent),
                    "generated": generated,
                    "applied": applied,
                    "reload_error": reload_error or None,
                },
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] agents.create failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_agents_update(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from dataclasses import asdict as dataclass_asdict
        from jiuwenswarm.server.runtime.agent_config_service import AgentConfigService, UpdateAgentParams

        try:
            params = dict(request.params or {})
            name = params.pop("name", "")
            workspace_dir = params.pop("workspace_dir", None)
            generate = params.pop("generate", False)

            # LLM 生成 when_to_use 和 prompt（默认不生成，需显式 --generate）
            generated = False
            if generate and name and params.get("description"):
                llm_result = await self._generate_agent_with_llm(name, params["description"])
                if llm_result:
                    params["when_to_use"] = llm_result[0]
                    params["prompt"] = llm_result[1]
                    generated = True

            p = UpdateAgentParams(**{k: v for k, v in params.items()
                                      if k in UpdateAgentParams.__dataclass_fields__})
            service = AgentConfigService(workspace_dir)
            agent = service.update_agent(name, p)

            # 更新后热加载（对齐 create/delete 的模式）
            applied = True
            reload_error = ""
            try:
                await self._agent_manager.reload_agents_config(get_config(), None)
            except Exception as reload_exc:
                applied = False
                reload_error = str(reload_exc)
                logger.warning("[AgentWebSocketServer] agents.update reload failed: %s", reload_exc)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "agent": dataclass_asdict(agent),
                    "generated": generated,
                    "applied": applied,
                    "reload_error": reload_error or None,
                },
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] agents.update failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_agents_delete(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from jiuwenswarm.server.runtime.agent_config_service import AgentConfigService

        try:
            params = request.params or {}
            name = params.get("name", "")
            workspace_dir = params.get("workspace_dir")
            service = AgentConfigService(workspace_dir)
            ok = service.delete_agent(name)
            # 自动从 config.yaml 中移除被删除的 agent
            applied = True
            reload_error = ""
            try:
                remove_subagent_from_config(name)
                await self._agent_manager.reload_agents_config(get_config(), None)
            except Exception as reload_exc:
                applied = False
                reload_error = str(reload_exc)
                logger.warning("[AgentWebSocketServer] agents.delete reload failed: %s", reload_exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"ok": ok, "applied": applied, "reload_error": reload_error or None},
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] agents.delete failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_agents_set_enabled(
        self,
        ws: Any,
        request: AgentRequest,
        send_lock: asyncio.Lock,
        enabled: bool
    ) -> None:
        from jiuwenswarm.server.runtime.agent_config_service import AgentConfigService

        action = "enable" if enabled else "disable"
        try:
            params = request.params or {}
            name = str(params.get("name", "")).strip()
            if not name:
                raise ValueError("agent name is required")
            workspace_dir = params.get("workspace_dir")
            service = AgentConfigService(workspace_dir)
            agent = service.get_agent(name)
            if agent is None:
                raise ValueError(f"Agent 不存在: {name}")
            if agent.source == "builtin":
                raise ValueError(f"不能启用/禁用内置 agent: {name}")

            upsert_subagent_in_config(name, enabled=enabled)
            applied = True
            reload_error = ""
            try:
                await self._agent_manager.reload_agents_config(get_config(), None)
            except Exception as reload_exc:
                applied = False
                reload_error = str(reload_exc)
                logger.warning("[AgentWebSocketServer] agents.%s reload failed: %s", action, reload_exc)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "name": name,
                    "enabled": enabled,
                    "applied": applied,
                    "reload_error": reload_error or None,
                },
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] agents.%s failed: %s", action, e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_agents_tools_list(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        from jiuwenswarm.server.runtime.agent_config_service import AgentConfigService

        try:
            params = request.params or {}
            workspace_dir = params.get("workspace_dir")
            service = AgentConfigService(workspace_dir)
            result = service.list_available_tools()
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=result,
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] agents.tools_list failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_config_cache_clear(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            from jiuwenswarm.agents.harness.common.memory.config import clear_config_cache

            clear_config_cache()
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"cleared": True},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] config.cache_clear failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_agent_reload_config(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        try:
            params = request.params or {}
            config_payload = params.get("config")
            env_overrides = params.get("env")
            target_channel_id = str(params.get("target_channel_id") or "").strip() or None
            target_session_id = str(params.get("target_session_id") or "").strip() or None
            raw_reload_scopes = params.get("reload_scopes")
            reload_scopes = {
                str(scope)
                for scope in raw_reload_scopes
                if isinstance(scope, str) and scope
            } if isinstance(raw_reload_scopes, list) else set()

            reload_kwargs = {}
            if target_channel_id:
                reload_kwargs["target_channel_id"] = target_channel_id
            if target_session_id:
                reload_kwargs["target_session_id"] = target_session_id
            agent_reload_scopes = {"model", "team", "permissions", "agent_runtime"}
            should_reload_agents = not reload_scopes or bool(reload_scopes & agent_reload_scopes)
            if should_reload_agents:
                await self._agent_manager.reload_agents_config(
                    config_payload,
                    env_overrides,
                    **reload_kwargs,
                )

            # Hot-reload ProactiveEngine config if available
            should_reload_proactive = not reload_scopes or bool(reload_scopes & {"model", "proactive", "agent_runtime"})
            if self._proactive_engine is not None and should_reload_proactive:
                cfg = get_config()
                proactive_cfg = cfg.get("proactive_recommendation", {})
                self._proactive_engine.reload_config(proactive_cfg)
                # 重建 proactive agent——它启动时建一次，模型配置固化在实例里。
                # 用户改模型后主 agent 会热更新，但 proactive agent 不在主 agent
                # 链路里，不重建会继续用旧模型（可能已失效/欠费）。
                try:
                    from jiuwenswarm.server.runtime.proactive_adapter import build_proactive_agent
                    self._proactive_engine.rebuild_proactive_agent(build_proactive_agent)
                except Exception as exc:
                    logger.warning("[AgentWebSocketServer] proactive agent rebuild failed: %s", exc)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"reloaded": True},
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] agent.reload_config failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_list(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """获取所有 Rail 扩展列表."""
        try:
            manager = get_rail_manager()
            extensions = manager.list_extensions()

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"extensions": extensions},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.list failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_import(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """导入新的 Rail 扩展（文件夹结构）."""
        try:
            params = request.params or {}
            folder_path = params.get("folder_path")

            if not folder_path:
                raise ValueError("缺少 folder_path 参数")

            source_path = Path(folder_path)
            if not source_path.exists() or not source_path.is_dir():
                raise ValueError(f"文件夹不存在或不是目录: {folder_path}")

            manager = get_rail_manager()
            extension = manager.import_extension(folder_path)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=extension,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.import failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_delete(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """删除 Rail 扩展."""
        try:
            params = request.params or {}
            name = params.get("name")

            if not name:
                raise ValueError("缺少 name 参数")

            manager = get_rail_manager()
            manager.delete_extension(name)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"deleted": True, "name": name},
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.delete failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_extensions_toggle(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """切换 Rail 扩展的启用状态，并触发热更新."""
        try:
            params = request.params or {}
            name = params.get("name")
            enabled = params.get("enabled", False)

            if name is None:
                raise ValueError("缺少 name 参数")
            if enabled is None:
                raise ValueError("缺少 enabled 参数")

            manager = get_rail_manager()

            # 1. 确保 agent 实例已设置（用于热更新）
            agent = self._agent_manager.get_agent_nowait()
            if agent is not None:
                agent_instance = agent.get_instance()
                if agent_instance is not None:
                    manager.set_agent_instance(agent_instance)

            # 2. 更新配置文件中的启用状态
            extension = manager.toggle_extension(name, enabled)

            # 3. 触发热更新：根据 enabled 状态注册或注销 rail
            await manager.hot_reload_rail(name, enabled)

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=extension,
            )
        except Exception as e:  # noqa: BLE001
            logger.exception("[AgentWebSocketServer] extensions.toggle failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_hooks_list(self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock) -> None:
        """获取当前 hooks 配置（供 TUI /hooks 命令浏览）."""
        try:
            config_base = get_config()
            hooks_config = load_hooks_config(config_base)
            summary = hooks_config.get_event_summary()

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "events": summary,
                    "disable_all_hooks": hooks_config.disable_all_hooks,
                    "source": "config.yaml",
                },
            )
        except Exception as e:
            logger.exception("[AgentWebSocketServer] hooks.list failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def send_push(self, msg) -> None:
        """AgentServer 主动向 Gateway 推送消息。

        payload 格式与 AgentResponse.payload 一致，
        可含 event_type 等字段供 Gateway 转为 Message 派发到 Channel。
        """
        if self._current_ws is None or self._current_send_lock is None:
            logger.warning(
                "[AgentWebSocketServer] send_push 失败: 无活跃 Gateway 连接"
            )
            return

        try:
            wire = build_server_push_wire(msg)
            async with self._current_send_lock:
                await self._current_ws.send(json.dumps(wire, ensure_ascii=False))
            response_kind = str(msg.get("response_kind") or "").strip()
            if response_kind:
                logger.info(
                    "[AgentWebSocketServer] send_push response_kind wire sent: channel_id=%s kind=%s",
                    msg.get("channel_id", ""),
                    response_kind,
                )
            else:
                logger.info(
                    "[AgentWebSocketServer] send_push 已发送(E2A wire): channel_id=%s",
                    msg.get("channel_id", ""),
                )
        except Exception as e:
            logger.warning("[AgentWebSocketServer] send_push 失败: %s", e)

    def get_agent(self):
        """获取 default agent 实例（向后兼容）."""
        return self._agent_manager.get_agent_nowait()

    def get_agent_manager(self) -> AgentManager:
        """获取 AgentManager 实例."""
        return self._agent_manager

    @staticmethod
    def get_conversation_history(session_id: str, page_idx: int) -> dict[str, Any] | None:
        # 按照 session_id 和分页消息获取历史记录
        if not isinstance(session_id, str) or not session_id.strip():
            return None
        if not isinstance(page_idx, int) or page_idx <= 0:
            return None

        normalized_session_id = session_id.strip()
        if not history_exists(normalized_session_id):
            return None
        try:
            raw = load_history_records(normalized_session_id)
        except Exception:
            return None
        if not isinstance(raw, list):
            return None

        page_size = _HISTORY_PAGE_SIZE
        restorable = [
            item for item in raw
            if _is_restorable_history_record(item)
        ]
        total = len(restorable)
        total_pages = max(1, math.ceil(total / page_size))
        if page_idx > total_pages:
            return None

        ordered = list(reversed(restorable))
        start = (page_idx - 1) * page_size
        end = start + page_size
        page_messages = [
            _sanitize_history_record_for_wire(item)
            for item in ordered[start:end]
        ]
        logger.debug(
            "[history.get] session_id=%s page_idx=%s raw_total=%s restorable_total=%s total_pages=%s returned=%s",
            normalized_session_id,
            page_idx,
            len(raw),
            total,
            total_pages,
            len(page_messages),
        )
        return {
            "messages": page_messages,
            "total_pages": total_pages,
            "page_idx": page_idx,
        }

    async def _handle_initialize(
            self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理 initialize 方法（非流式）.

        调用 AgentManager.initialize 完成初始化，返回 capabilities。

        Args:
            ws: WebSocket 连接
            request: AgentRequest
            send_lock: 发送锁
        """
        logger.info("[AgentServer] initialize: request_id=%s channel_id=%s", request.request_id, request.channel_id)

        try:
            params = request.params if isinstance(request.params, dict) else {}
            client_capabilities = params.get("clientCapabilities", {})
            logger.info(
                "[AgentServer] initialize clientCapabilities: %s",
                client_capabilities,
            )

            extra_config = {
                "protocol_version": params.get("protocolVersion", "0.1.0"),
                "client_capabilities": client_capabilities,
            }
            if request.channel_id == "acp":
                self._set_ws_acp_client_capabilities(ws, client_capabilities)

            channel_id = request.channel_id or "default"
            capabilities = await self._agent_manager.initialize(
                channel_id=channel_id,
                extra_config=extra_config,
            )
            if capabilities is None:
                capabilities = ACP_DEFAULT_CAPABILITIES.copy()

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=capabilities,
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

            logger.info("[AgentServer] initialize completed: capabilities=%s", capabilities)

        except Exception as e:
            logger.exception("[AgentServer] initialize failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_create(
            self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """处理 session.create 方法.

        调用 AgentManager.create_session 创建会话，返回 session_id。

        Args:
            ws: WebSocket 连接
            request: AgentRequest
            send_lock: 发送锁
        """
        logger.info("[AgentServer] session.create: request_id=%s", request.request_id)

        try:
            channel_id = request.channel_id or "default"
            params = request.params if isinstance(request.params, dict) else {}
            mode, _, _ = resolve_agent_request_mode(params.get("mode", "agent.plan"))
            explicit_session_id = params.get("session_id")
            session_id = await self._agent_manager.create_session(
                channel_id=channel_id,
                session_id=str(explicit_session_id).strip() if isinstance(explicit_session_id, str) else None,
            )

            if mode == "team":
                from jiuwenswarm.agents.harness.team import get_team_manager

                team_manager = get_team_manager(channel_id)
                logger.info(
                    "[AgentServer] session.create preparing team switch: channel_id=%s "
                    "target_session_id=%s mode=%s",
                    channel_id,
                    session_id,
                    mode,
                )
                await team_manager.prepare_session_switch(
                    session_id,
                    reason="session.create switch: ",
                )

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"sessionId": session_id},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

            logger.info("[AgentServer] session.create completed: session_id=%s", session_id)

        except Exception as e:
            logger.exception("[AgentServer] session.create failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_session_fork(
            self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle session.fork: filesystem copy + in-memory context copy.

        Args:
            ws: WebSocket connection.
            request: AgentRequest with source_session_id, target_session_id, title.
            send_lock: Send lock.
        """
        from jiuwenswarm.agents.harness.common.session_ops_service import (
            copy_session_context,
            copy_session_state,
            fork_session,
        )

        logger.info(
            "[AgentServer] session.fork: request_id=%s", request.request_id
        )

        try:
            params = request.params if isinstance(request.params, dict) else {}
            source = str(params.get("source_session_id") or "").strip()
            target = str(params.get("target_session_id") or "").strip()
            fork_title = str(params.get("title") or "").strip()
            channel_id = request.channel_id or "default"

            if not source:
                raise ValueError("source_session_id is required")
            if not target:
                raise ValueError("target_session_id is required")

            # 1. Filesystem fork (copies history.json, writes metadata)
            result = fork_session(
                source_session_id=source,
                target_session_id=target,
                title=fork_title,
                channel_id=channel_id,
            )

            # 2. Copy in-memory context (LLM conversation history)
            agent = self._agent_manager.get_agent_nowait(channel_id)
            deep_agent = None
            if agent is not None:
                deep_agent = agent.get_instance()
                await copy_session_context(deep_agent, source, target)
            else:
                logger.warning(
                    "[AgentServer] session.fork: no agent for channel %s, "
                    "in-memory context copy skipped",
                    channel_id,
                )

            # 3. Copy DeepAgentState (task_plan, plan_mode, etc.)
            from openjiuwen.core.single_agent.schema.agent_card import AgentCard

            await copy_session_state(
                source_session_id=source,
                target_session_id=target,
                card=deep_agent.card if deep_agent is not None else AgentCard(id="jiuwenswarm", name="jiuwenswarm"),
                deep_agent=deep_agent,
            )

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=result,
            )
            wire = encode_agent_response_for_wire(
                resp, response_id=request.request_id
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

            logger.info(
                "[AgentServer] session.fork completed: source=%s target=%s title=%s",
                source, target, result.get("title", ""),
            )

        except ValueError as e:
            logger.warning("[AgentServer] session.fork ValueError: %s", e)
            code = (
                "NOT_FOUND" if "not found" in str(e)
                else "ALREADY_EXISTS" if "already exists" in str(e)
                else "BAD_REQUEST"
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e), "code": code},
            )
            wire = encode_agent_response_for_wire(
                resp, response_id=request.request_id
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
        except Exception as e:
            logger.exception("[AgentServer] session.fork failed: %s", e)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(e)},
            )
            wire = encode_agent_response_for_wire(
                resp, response_id=request.request_id
            )
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_acp_tool_response(
            self,
            ws: Any,
            request: AgentRequest,
            send_lock: asyncio.Lock,
    ) -> None:
        params = request.params if isinstance(request.params, dict) else {}
        jsonrpc_id = params.get("jsonrpc_id")
        response_payload = params.get("response")
        if not isinstance(response_payload, dict):
            response_payload = {}

        if get_acp_output_manager().complete_jsonrpc_response(jsonrpc_id, response_payload):
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={"accepted": True},
            )
        else:
            logger.info(
                "[AgentServer] ignore unknown/late acp tool response: jsonrpc_id=%s request_id=%s",
                jsonrpc_id,
                request.request_id,
            )
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload={
                    "accepted": False,
                    "ignored": True,
                    "reason": "unknown_or_late_response",
                    "jsonrpc_id": jsonrpc_id,
                },
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def handle_acp_tool_response_for_test(
            self,
            ws: Any,
            request: AgentRequest,
            send_lock: asyncio.Lock,
    ) -> None:
        """Public test helper that delegates to ACP tool-response handling."""
        await self._handle_acp_tool_response(ws, request, send_lock)

    async def _handle_harness_packages_get(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle harness.packages.get request - retrieve packages info."""
        try:
            service = AutoHarnessService(rail=None, agent=None)
            payload = await asyncio.to_thread(service.get_packages_info)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except Exception as exc:
            logger.exception("[AgentServer] harness.packages.get failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_harness_packages_scan(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle harness.packages.scan request - scan runtime extensions."""
        try:
            service = AutoHarnessService(rail=None, agent=None)
            payload = await asyncio.to_thread(service.scan_runtime_extensions)
            await asyncio.to_thread(service.save_packages, payload)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except Exception as exc:
            logger.exception("[AgentServer] harness.packages.scan failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_harness_packages_activate(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle harness.packages.activate request - activate a harness package."""
        params = request.params if isinstance(request.params, dict) else {}
        package_id = params.get("package_id")

        if not package_id:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "missing package_id", "code": "BAD_REQUEST"},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            # Get or create the agent instance (auto-create if not exists)
            mode, sub_mode = _apply_resolved_mode_to_request(request)
            agent_mode = "agent" if mode == "auto_harness" else mode
            channel_id = request.channel_id or "web"
            agent = await self._agent_manager.get_agent(
                channel_id=channel_id,
                mode=agent_mode,
                project_dir=resolve_request_project_dir(request),
                sub_mode=sub_mode
            )
            agent_instance = None
            if agent is not None:
                agent_instance = agent.get_instance()
                logger.info(
                    "[AgentServer] harness.packages.activate: agent_instance type=%s, has_load_harness_config=%s",
                    type(agent_instance).__name__ if agent_instance else None,
                    hasattr(agent_instance, "load_harness_config") if agent_instance else False,
                )

            service = AutoHarnessService(
                rail=None,
                agent=agent_instance,
                agent_manager=self._agent_manager,
            )
            payload = await service.activate_package(package_id, channel_id=channel_id)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except ValueError as exc:
            logger.warning("[AgentServer] harness.packages.activate validation error: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": _harness_error_code(exc)},
            )
        except Exception as exc:
            logger.exception("[AgentServer] harness.packages.activate failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "INTERNAL_ERROR"},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_harness_packages_deactivate(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle harness.packages.deactivate request - deactivate a harness package."""
        params = request.params if isinstance(request.params, dict) else {}
        package_id = params.get("package_id")

        if not package_id:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "missing package_id", "code": "BAD_REQUEST"},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            # Get or create the agent instance (auto-create if not exists)
            channel_id = request.channel_id or "web"
            mode, sub_mode = _apply_resolved_mode_to_request(request)
            agent_mode = "agent" if mode == "auto_harness" else mode
            agent = await self._agent_manager.get_agent(
                channel_id=channel_id,
                project_dir=resolve_request_project_dir(request),
                mode=agent_mode,
                sub_mode=sub_mode
            )
            agent_instance = None
            if agent is not None:
                agent_instance = agent.get_instance()

            service = AutoHarnessService(
                rail=None,
                agent=agent_instance,
                agent_manager=self._agent_manager,
            )
            payload = await service.deactivate_package(package_id, channel_id=channel_id)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except ValueError as exc:
            logger.warning("[AgentServer] harness.packages.deactivate validation error: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": _harness_error_code(exc)},
            )
        except Exception as exc:
            logger.exception("[AgentServer] harness.packages.deactivate failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "INTERNAL_ERROR"},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    async def _handle_harness_packages_delete(
        self, ws: Any, request: AgentRequest, send_lock: asyncio.Lock
    ) -> None:
        """Handle harness.packages.delete request - delete a harness package."""
        params = request.params if isinstance(request.params, dict) else {}
        package_id = params.get("package_id")

        if not package_id:
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "missing package_id", "code": "BAD_REQUEST"},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        if package_id == "native":
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": "Cannot delete native agent version", "code": "BAD_REQUEST"},
            )
            wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
            async with send_lock:
                await ws.send(json.dumps(wire, ensure_ascii=False))
            return

        try:
            mode, sub_mode = _apply_resolved_mode_to_request(request)
            agent_mode = "agent" if mode == "auto_harness" else mode
            agent = await self._agent_manager.get_agent(
                channel_id=request.channel_id,
                project_dir=resolve_request_project_dir(request),
                mode=agent_mode,
                sub_mode=sub_mode
            )
            agent_instance = None
            if agent is not None:
                agent_instance = agent.get_instance()

            service = AutoHarnessService(
                rail=None,
                agent=agent_instance,
                agent_manager=self._agent_manager,
            )
            payload = await service.delete_package(package_id, channel_id=request.channel_id)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
        except ValueError as exc:
            logger.warning("[AgentServer] harness.packages.delete validation error: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": _harness_error_code(exc)},
            )
        except Exception as exc:
            logger.exception("[AgentServer] harness.packages.delete failed: %s", exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc), "code": "INTERNAL_ERROR"},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))

    def _resolve_model(self, model_name: Optional[str] = None) -> Optional[Any]:
        """Resolve model from jiuwenswarm config.

        Args:
            model_name: Requested model name, falls back to default if None or not found

        Returns:
            Model instance or None if config cannot be loaded
        """
        # Build model cache if not already done
        if not self._model_cache:
            self._build_model_cache()

        # Resolve by name or use default
        if model_name and model_name in self._model_cache:
            return self._model_cache[model_name]
        return self._default_model

    def _build_model_cache(self) -> None:
        """Build model cache from jiuwenswarm config.yaml (reuse interface_deep logic)."""
        from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter

        config = get_config()

        # Use the same model building method as interface_deep
        build_model_from_entry = getattr(JiuWenSwarmDeepAdapter, '_build_model_from_entry')

        # Build from models.defaults list
        for entry in get_default_models(config):
            mcc = entry.get("model_client_config") or {}
            model_name = mcc.get("model_name")
            if not model_name:
                continue
            mco = entry.get("model_config_obj") or {}
            self._model_cache[model_name] = build_model_from_entry(mcc, mco)

        # Fallback to legacy format if needed (same as interface_deep._build_model_cache_legacy)
        if not self._model_cache:
            default_model_config = config.get("models", {}).get("default", {})
            react_config = config.get("react", {})
            mcc = dict(
                default_model_config.get("model_client_config")
                or react_config.get("model_client_config")
                or {}
            )
            model_name = mcc.get("model_name") or react_config.get("model_name") or "gpt-4"
            if "model_name" not in mcc:
                mcc["model_name"] = model_name
            mco = (
                default_model_config.get("model_config_obj")
                or react_config.get("model_config_obj")
                or {}
            )
            self._model_cache[model_name] = build_model_from_entry(mcc, mco)

        # Set default model (first one)
        if self._model_cache:
            first_name = next(iter(self._model_cache))
            self._default_model = self._model_cache[first_name]
            logger.info(
                "[AgentServer] Built model cache with %d models, default=%s",
                len(self._model_cache), first_name
            )

    async def _handle_schedule_request(
        self,
        ws: Any,
        request: AgentRequest,
        send_lock: asyncio.Lock,
        action: str,
    ) -> None:
        """Handle schedule.* requests - schedule task management."""
        logger.info(
            "[AgentServer] schedule.%s request received: request_id=%s channel_id=%s",
            action, request.request_id, request.channel_id,
        )
        try:
            # Lazy initialization: create scheduler service on first request
            if self._scheduler_service is None:
                logger.info("[AgentServer] Initializing scheduler service on first request")
                self._scheduler_service = AutoHarnessService(None, agent=None)
                # Start the scheduler loop
                await self._scheduler_service.start_scheduler()

            params = request.params or {}
            payload: dict[str, Any] = {}

            # For actions that need agent: get agent and set on service (similar to _handle_command_compact)
            needs_agent = action in ("create", "run", "cancel", "delete", "issue_watch_once")
            if needs_agent:
                mode, sub_mode = _apply_resolved_mode_to_request(request)
                agent_mode = "agent" if mode == "auto_harness" else mode
                agent = await self._agent_manager.get_agent(
                    channel_id=request.channel_id or "tui",
                    mode=agent_mode,
                    project_dir=resolve_request_project_dir(request),
                    sub_mode=sub_mode

                )
                if agent is None:
                    raise ValueError("Failed to get agent for schedule request")
                # Set agent on service (service will use it for execution)
                self._scheduler_service.update_agent_instance(agent)
                logger.info("[AgentServer] Set agent for schedule action %s: %s", action, agent is not None)

            if action == "check_config":
                payload = self._scheduler_service.check_schedule_config()

            elif action == "update_config":
                fields = params.get("fields", {})
                payload = self._scheduler_service.update_schedule_config(fields)

            elif action == "create":
                query = params.get("query", "")
                interval_hours = params.get("interval_hours", 4)
                run_immediately = params.get("run_immediately", False)
                model_name = params.get("model_name")
                pipeline = params.get("pipeline")  # Pipeline preference
                # Resolve model from jiuwenswarm config
                model = self._resolve_model(model_name)
                payload = await self._scheduler_service.create_scheduled_task(
                    query, interval_hours, run_immediately, model, pipeline
                )

            elif action == "run":
                query = params.get("query", "")
                model_name = params.get("model_name")
                pipeline = params.get("pipeline")  # Pipeline preference
                # Resolve model from jiuwenswarm config
                model = self._resolve_model(model_name)
                payload = await self._scheduler_service.run_task(query, model, pipeline)

            elif action == "list":
                tasks = await self._scheduler_service.list_scheduled_tasks()
                payload = {"tasks": tasks}

            elif action == "status":
                task_id = params.get("task_id", "")
                task = await self._scheduler_service.get_scheduled_task_status(task_id)
                payload = task if task else {"error": "任务不存在", "task_id": task_id}

            elif action == "logs":
                task_id = params.get("task_id", "")
                log_type = params.get("log_type", "current")
                history_index = params.get("history_index", -1)
                offset = params.get("offset", 0)
                limit = params.get("limit", 500)
                payload = await self._scheduler_service.get_scheduled_task_logs(
                    task_id, log_type, history_index, offset, limit
                )

            elif action == "cancel":
                task_id = params.get("task_id", "")
                payload = await self._scheduler_service.cancel_scheduled_task(task_id)

            elif action == "delete":
                task_id = params.get("task_id", "")
                payload = await self._scheduler_service.delete_scheduled_task(task_id)

            elif action == "issue_watch_once":
                model_name = params.get("model_name")
                model = self._resolve_model(model_name)
                payload = await self._scheduler_service.watch_gitcode_issues_once(params, model)

            elif action == "issue_state_list":
                payload = await self._scheduler_service.list_gitcode_issue_states()

            elif action == "issue_delete":
                payload = await self._scheduler_service.delete_issue_states(params)

            elif action == "issue_matrix":
                payload = await self._scheduler_service.refresh_issue_matrix(params)

            else:
                payload = {"error": f"未知的调度操作: {action}"}

            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=True,
                payload=payload,
            )
            logger.info(
                "[AgentServer] schedule.%s response prepared: request_id=%s channel_id=%s ok=%s payload_keys=%s",
                action, resp.request_id, resp.channel_id, resp.ok, list(payload.keys())[:10],
            )
        except Exception as exc:
            logger.exception("[AgentServer] schedule.%s failed: %s", action, exc)
            resp = AgentResponse(
                request_id=request.request_id,
                channel_id=request.channel_id,
                ok=False,
                payload={"error": str(exc)},
            )

        wire = encode_agent_response_for_wire(resp, response_id=request.request_id)
        logger.info(
            "[AgentServer] schedule.%s sending response wire: request_id=%s wire_keys=%s",
            action, request.request_id, list(wire.keys())[:10],
        )
        async with send_lock:
            await ws.send(json.dumps(wire, ensure_ascii=False))
