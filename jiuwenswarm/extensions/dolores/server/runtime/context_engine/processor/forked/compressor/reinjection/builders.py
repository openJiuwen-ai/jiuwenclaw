from __future__ import annotations

import ast
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jiuwenswarm.extensions.dolores.server.runtime.context_engine.context.session_memory_manager import (
    group_completed_api_rounds as group_completed_api_round_ranges,
)
from jiuwenswarm.extensions.dolores.server.runtime.context_engine.processor.forked.compressor.reinjection.reinjector import ReinjectContext
from openjiuwen.core.foundation.llm import AssistantMessage, BaseMessage, ToolMessage, UserMessage


@dataclass(frozen=True)
class ReadFileSnapshot:
    file_path: str
    content: str
    line_count: int | None
    partial: bool


def build_plan_reinjected_content(ctx: ReinjectContext) -> str:
    plan_mode = _get_plan_mode(ctx.session_state)
    plan_slug = plan_mode.get("plan_slug") if isinstance(plan_mode, dict) else None
    if not plan_slug:
        return ""
    plan_path = _resolve_plan_file_path(ctx, str(plan_slug))
    if plan_path is None:
        return ""
    try:
        plan_text = plan_path.read_text(encoding="utf-8")
    except Exception:
        return ""
    return ctx.truncate(f"Current plan file: {plan_path}\n\n{plan_text}")


def _resolve_plan_file_path(ctx: ReinjectContext, plan_slug: str) -> Path | None:
    candidate_paths: list[Path] = []
    if ctx.workspace_root:
        candidate_paths.append(Path(ctx.workspace_root) / ".plans" / f"{plan_slug}.md")
    for path in _iter_enter_plan_mode_paths(ctx.source_messages, plan_slug):
        candidate_paths.append(path)
    for path in candidate_paths:
        if path.exists():
            return path
    return None


def _iter_enter_plan_mode_paths(messages: list[BaseMessage], plan_slug: str):
    for tool_call in iter_tool_calls(messages, name="enter_plan_mode"):
        result_text = find_tool_result_text(messages, getattr(tool_call, "id", None))
        match = re.search(r"Plan file created at:\s*(?P<path>.+?\.md)(?:\r?\n|$)", result_text)
        if not match:
            continue
        plan_path = Path(match.group("path").strip())
        if plan_path.stem == plan_slug:
            yield plan_path


def build_skill_reinjected_content(ctx: ReinjectContext) -> list[UserMessage]:
    keep_signatures = {message_signature(message) for message in ctx.messages_to_keep}
    selected_rounds: list[list[BaseMessage]] = []
    seen_round_signatures: set[tuple[str, ...]] = set()
    max_skills = int(getattr(ctx.config, "reinject_recent_skills", 3) or 0)
    if max_skills <= 0:
        return []

    for round_messages in reversed(group_completed_api_rounds(ctx.source_messages)):
        round_signatures = tuple(message_signature(message) for message in round_messages)
        if round_signatures in seen_round_signatures:
            continue
        if any(signature in keep_signatures for signature in round_signatures):
            continue
        snapshot = extract_skill_tool_snapshot(round_messages) or extract_read_file_skill_snapshot(round_messages)
        if not snapshot:
            continue
        selected_rounds.append([UserMessage(content=f"{ctx.state_marker}\n[SKILLS]\n{ctx.truncate(snapshot)}")])
        seen_round_signatures.add(round_signatures)
        if len(selected_rounds) >= max_skills:
            break

    selected_rounds.reverse()
    return [message for messages in selected_rounds for message in messages]


def build_file_reinjected_content(ctx: ReinjectContext) -> str:
    preserved_paths = collect_read_file_paths(ctx.messages_to_keep)
    max_files = int(getattr(ctx.config, "reinject_read_file_max_files", 5) or 0)
    per_file_chars = int(getattr(ctx.config, "reinject_read_file_max_chars_per_file", 15000) or 15000)
    total_chars = int(getattr(ctx.config, "reinject_read_file_total_chars", 50000) or 50000)
    if max_files <= 0 or total_chars <= 0:
        return ""

    snapshots: list[ReadFileSnapshot] = []
    for round_messages in reversed(group_completed_api_rounds(ctx.source_messages)):
        for tool_call in iter_tool_calls(round_messages, name="read_file"):
            result_text = find_tool_result_text(round_messages, getattr(tool_call, "id", None))
            snapshot = parse_read_file_result(tool_call, result_text)
            if snapshot is None:
                continue
            if snapshot.file_path in preserved_paths:
                continue
            if is_excluded_from_reinject(snapshot.file_path):
                continue
            snapshots.append(snapshot)

    selected: list[ReadFileSnapshot] = []
    seen_paths: set[str] = set()
    remaining = total_chars
    for snapshot in snapshots:
        if snapshot.file_path in seen_paths:
            continue
        seen_paths.add(snapshot.file_path)
        selected.append(snapshot)
        remaining -= min(len(snapshot.content), per_file_chars)
        if len(selected) >= max_files or remaining <= 0:
            break
    selected.reverse()
    return render_read_file_snapshots(selected, ctx.truncate, per_file_chars)


TEAM_TOOL_CALL_NAMES = ("send_message", "view_task", "claim_task", "member_complete_task")


def build_task_status_reinjected_content(ctx: ReinjectContext) -> str:
    lines: list[str] = []

    team_status = ctx.session_state.get("team_task_status")
    if isinstance(team_status, dict):
        lines.extend(_render_team_tool_calls_section(ctx, team_status))
    else:
        # Normal mode: reinject background (sub-agent) status from session state.
        lines.extend(_render_background_tasks_section(ctx))

    return ctx.truncate("\n".join(lines))


def _render_background_tasks_section(ctx: ReinjectContext) -> list[str]:
    lines: list[str] = []
    background_tasks = ctx.session_state.get("background_tasks") or []
    if isinstance(background_tasks, list):
        for task in background_tasks:
            if not isinstance(task, dict):
                continue
            description = task.get("description") or "background task"
            task_id = task.get("task_id") or "unknown"
            status = task.get("status") or "unknown"
            if status == "running":
                lines.append(
                    f'Background agent "{description}" ({task_id}) is still running. Do NOT spawn a duplicate.'
                )
            elif status in {"completed", "error", "canceled"}:
                lines.append(
                    f'Background agent "{description}" ({task_id}) status={status}. '
                    "Check the stored result/error before spawning another task."
                )
    return lines


def _render_team_tool_calls_section(ctx: ReinjectContext, team_status: dict) -> list[str]:
    """Team mode: render the static team board plus recovered collaboration calls.

    Two sources, both emitted under one section so the team's collaboration
    history survives compaction:

    1. Static snapshot from ``team_status`` (active members, open tasks,
       unread-messages flag) — always present when the board is non-empty.
    2. Collaboration tool calls recovered from the compacted dialogue by
       scanning ``ctx.source_messages`` (the full pre-compaction active
       window): who messaged whom, which tasks were claimed/completed, and
       what ``view_task`` returned. Calls whose round already survives in
       ``messages_to_keep`` are de-duplicated (still verbatim in the
       post-compaction context).
    """
    if not getattr(ctx.config, "reinject_team_tool_calls", True):
        return []

    keep_signatures = {message_signature(message) for message in ctx.messages_to_keep}
    max_calls = int(getattr(ctx.config, "reinject_team_tool_call_max", 20) or 0)

    lines: list[str] = ["Team collaboration state:", f'- Team: {team_status.get("team_name", "unknown")}']

    # Static snapshot from session state: active members, open tasks, unread
    # flag. This survives regardless of whether the dialogue carried team tool
    # calls, so the team's current board is never lost to compaction.
    lines.extend(_render_team_static_snapshot(team_status))

    # Recovered collaboration tool calls: re-surface who messaged whom, which
    # tasks were claimed/completed, and what view_task returned, by scanning
    # ctx.source_messages (the full pre-compaction active window). Calls whose
    # round already survives in messages_to_keep are de-duplicated (still
    # verbatim in the post-compaction context).
    collected = _collect_team_tool_calls(ctx.source_messages, keep_signatures, max_calls)
    if collected:
        if lines and lines[-1]:
            lines.append("")
        lines.append("Recovered collaboration history:")
        for tool_call, owner_message, result_text in collected:
            rendered = _render_team_tool_call_line(tool_call, result_text)
            if rendered:
                lines.append(rendered)

    if len(lines) == 2:
        # Only the heading and team name — no board state and no recoverable calls.
        # Drop it rather than emit an empty section.
        return []
    return lines


def _render_team_static_snapshot(team_status: dict) -> list[str]:
    """Render the current team board in a scan-friendly shape."""
    lines: list[str] = []
    members = team_status.get("members") or []
    if members:
        lines.extend(["", "Current members:"])
        for member in members:
            if isinstance(member, dict):
                lines.append(
                    f'- {member.get("member_name", "unknown")}: '
                    f'role={member.get("role", "")}, status={member.get("status", "unknown")}'
                )
    open_tasks = team_status.get("open_tasks") or []
    if open_tasks:
        lines.extend(["", "Open tasks:"])
        for task in open_tasks:
            if isinstance(task, dict):
                assignee = task.get("assignee") or "unassigned"
                lines.append(
                    f'- #{task.get("task_id", "unknown")} [{task.get("status", "unknown")}] '
                    f'{task.get("title", "")} (assignee: {assignee})'
                )
    if team_status.get("has_unread_messages"):
        lines.extend(["", "Team signals:", "- Unread team messages exist."])
    return lines


def _collect_team_tool_calls(
    messages: list[BaseMessage],
    keep_signatures: set[str],
    max_calls: int,
) -> list[tuple[Any, AssistantMessage, str]]:
    """Walk messages in order, collect team tool calls not already retained.

    Returns (tool_call, owner_assistant_message, result_text) triples in
    first-appearance order, de-duplicated against ``keep_signatures`` and
    capped at ``max_calls``.
    """
    collected: list[tuple[Any, AssistantMessage, str]] = []
    if max_calls <= 0:
        return collected
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        if message_signature(message) in keep_signatures:
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            tool_name = getattr(tool_call, "name", "") or ""
            if tool_name not in TEAM_TOOL_CALL_NAMES:
                continue
            call_id = getattr(tool_call, "id", None)
            result_text = find_tool_result_text(messages, call_id)
            collected.append((tool_call, message, result_text))
            if len(collected) >= max_calls:
                return collected
    return collected


def _render_team_tool_call_line(tool_call: Any, result_text: str) -> str:
    tool_name = getattr(tool_call, "name", "") or ""
    arguments_text = getattr(tool_call, "arguments", "") or ""
    args = parse_tool_arguments(arguments_text)

    action_desc = _describe_team_tool_action(tool_name, args)
    line = f"- {action_desc} [{tool_name}]".rstrip()

    result_text = (result_text or "").strip()
    if result_text:
        # Collapse multi-line tool results (e.g. view_task task lists) into a
        # compact single-line preview so the section stays scannable.
        compacted = " ".join(part.strip() for part in result_text.splitlines() if part.strip())
        if len(compacted) > 200:
            compacted = compacted[:200] + "..."
        line += f"\n  Result: {compacted}"
    return line


def _describe_team_tool_action(tool_name: str, args: dict) -> str:
    """Describe recovered team tool calls in concise English."""
    if tool_name == "send_message":
        to_raw = args.get("to")
        if isinstance(to_raw, list):
            targets = ", ".join(str(item) for item in to_raw if item)
            base = f"Sent message to {targets}" if targets else "Sent message"
        elif to_raw == "*":
            base = "Broadcast message to the team"
        elif to_raw:
            base = f"Sent message to {to_raw}"
        else:
            base = "Sent message"
        summary = str(args.get("summary") or "").strip()
        if not summary:
            content = str(args.get("content") or "")
            summary = (content[:80] + "...") if len(content) > 80 else content
        return f"{base}: {summary}" if summary else base

    if tool_name == "view_task":
        action = args.get("action") or "list"
        if action == "get":
            task_id = args.get("task_id")
            return f"Viewed task #{task_id}" if task_id else "Viewed task details"
        if action == "claimable":
            return "Listed claimable tasks"
        status = args.get("status")
        return f"Listed tasks with status={status}" if status else "Listed tasks"

    if tool_name == "claim_task":
        task_id = args.get("task_id") or "?"
        status = args.get("status")
        if status == "completed":
            return f"Marked task #{task_id} as completed"
        if status == "claimed":
            return f"Claimed task #{task_id}"
        return f"Updated task #{task_id} with status={status or '?'}"

    if tool_name == "member_complete_task":
        task_id = args.get("task_id") or "?"
        note = str(args.get("note") or "").strip()
        note_short = (note[:80] + "...") if len(note) > 80 else note
        if note_short:
            return f"Completed own task #{task_id}: {note_short}"
        return f"Completed own task #{task_id}"

    return f"Called {tool_name}"


def build_tool_result_hint_reinjected_content(ctx: ReinjectContext) -> str:
    _ = ctx
    return ""


def build_todo_reinjected_content(ctx: ReinjectContext) -> str:
    todos = _read_todo_file(ctx)
    if not todos:
        return ""

    active_statuses = {"pending", "in_progress"}
    counts: dict[str, int] = {}
    active: list[dict[str, Any]] = []
    for todo in todos:
        status = str(todo.get("status") or "pending")
        counts[status] = counts.get(status, 0) + 1
        if status in active_statuses:
            active.append(todo)

    if not active:
        return ""

    lines = ["Active todos:"]
    for todo in active:
        todo_id = str(todo.get("id") or "unknown")
        status = str(todo.get("status") or "pending")
        content = str(todo.get("content") or todo.get("description") or "").strip()
        if not content:
            content = "(no content)"
        active_form = str(todo.get("activeForm") or "").strip()
        suffix = f" ({active_form})" if active_form and active_form != content else ""
        lines.append(f"- [{status}] {todo_id}: {content}{suffix}")

    summary = ", ".join(f"{status}: {count}" for status, count in sorted(counts.items()))
    if summary:
        lines.append(f"Status counts: {summary}")
    return ctx.truncate("\n".join(lines))


def build_plan_mode_reinjected_content(ctx: ReinjectContext) -> str:
    plan_mode = _get_plan_mode(ctx.session_state)
    if not isinstance(plan_mode, dict):
        return ""

    mode = plan_mode.get("mode") or "normal"
    pre_plan_mode = plan_mode.get("pre_plan_mode")
    plan_slug = plan_mode.get("plan_slug")
    if mode == "normal" and not plan_slug and pre_plan_mode in {None, "", "normal"}:
        return ""

    lines = [
        "Current plan-mode status for this session:",
        f"- Active mode: {mode}.",
    ]
    if pre_plan_mode:
        lines.append(f"- Previous mode before entering plan mode: {pre_plan_mode}.")
    if plan_slug:
        lines.append(f"- Active plan identifier: {plan_slug}.")
    if mode == "plan":
        lines.extend(
            [
                "",
                "Plan-mode constraints:",
                "- Only planning is allowed; do not implement the plan yet.",
                "- Do not modify files except the active plan file.",
                "- Use read-only exploration tools unless editing the plan file.",
                "- End planning through exit_plan_mode when the plan is ready for user approval.",
                "- Use ask_user only for clarification or choosing between approaches, not for plan approval.",
            ]
        )
    return ctx.truncate("\n".join(lines))


def _get_plan_mode(session_state: dict[str, Any]) -> dict[str, Any] | None:
    if not isinstance(session_state, dict):
        return None
    plan_mode = session_state.get("plan_mode")
    if isinstance(plan_mode, dict):
        return plan_mode
    for key in ("deepagent", "task_state"):
        nested = session_state.get(key)
        if isinstance(nested, dict) and isinstance(nested.get("plan_mode"), dict):
            return nested["plan_mode"]
    for key in ("global_state", "agent_state", "trace_state"):
        nested_plan_mode = _get_plan_mode(session_state.get(key))
        if isinstance(nested_plan_mode, dict):
            return nested_plan_mode
    return None


def _normalize_todos(value: Any) -> list[dict[str, Any]]:
    if not isinstance(value, list):
        return []
    return [dict(item) for item in value if isinstance(item, dict)]


def _read_todo_file(ctx: ReinjectContext) -> list[dict[str, Any]]:
    workspace_root = ctx.workspace_root
    context = ctx.context
    if not workspace_root and context is not None and hasattr(context, "workspace_dir"):
        try:
            workspace_root = context.workspace_dir()
        except Exception:
            workspace_root = None
    if not workspace_root:
        return []

    session_id = ""
    if context is not None and hasattr(context, "session_id"):
        try:
            session_id = context.session_id()
        except Exception:
            session_id = ""
    if not session_id and context is not None and hasattr(context, "get_session_ref"):
        try:
            session = context.get_session_ref()
            if session is not None and hasattr(session, "get_session_id"):
                session_id = session.get_session_id()
        except Exception:
            session_id = ""
    if not session_id:
        return []

    workspace_path = Path(workspace_root)
    candidate_paths = [
        workspace_path / session_id / "todo.json",
        workspace_path / "todo" / session_id / "todo.json",
    ]
    todo_path = next((path for path in candidate_paths if path.exists()), None)
    if todo_path is None:
        return []
    try:
        data = json.loads(todo_path.read_text(encoding="utf-8"))
    except Exception:
        return []
    return _normalize_todos(data)


def is_skill_file_path(file_path: str) -> bool:
    if not file_path:
        return False
    normalized = file_path.replace("\\", "/").lower()
    return normalized.endswith("/skill.md") or normalized.endswith("skill.md")


def extract_skill_tool_snapshot(messages: list[BaseMessage]) -> str:
    for tool_call in iter_tool_calls(messages, name="skill_tool"):
        args = parse_tool_arguments(getattr(tool_call, "arguments", "") or "")
        relative_path = str(args.get("relative_file_path") or "SKILL.md")
        if relative_path not in {"", "SKILL.md"}:
            continue
        result = parse_jsonish_tool_result(find_tool_result_text(messages, getattr(tool_call, "id", None)))
        content = result.get("skill_content") if isinstance(result, dict) else None
        directory = result.get("skill_directory") if isinstance(result, dict) else None
        skill_name = args.get("skill_name") or Path(str(directory or "")).name
        if isinstance(content, str) and content.strip():
            return f"Skill: {skill_name}\nPath: {directory}/SKILL.md\n\n{content.strip()}"
    return ""


def extract_read_file_skill_snapshot(messages: list[BaseMessage]) -> str:
    for tool_call in iter_tool_calls(messages, name="read_file"):
        args_text = getattr(tool_call, "arguments", "") or ""
        args = parse_tool_arguments(args_text)
        file_path = extract_argument_value(args, args_text, ("file_path", "path"))
        if not is_skill_file_path(file_path):
            continue
        result_text = find_tool_result_text(messages, getattr(tool_call, "id", None))
        result = parse_jsonish_tool_result(result_text)
        content = result.get("content") if isinstance(result, dict) else result_text
        if isinstance(content, str) and content.strip():
            return f"Skill: {Path(file_path).parent.name}\nPath: {file_path}\n\n{content.strip()}"
    return ""


def group_completed_api_rounds(messages: list[BaseMessage]) -> list[list[BaseMessage]]:
    return [list(messages[start:end]) for start, end in group_completed_api_round_ranges(messages)]


def message_signature(message: BaseMessage) -> str:
    tool_call_ids = []
    if isinstance(message, AssistantMessage):
        tool_call_ids = [
            getattr(tool_call, "id", "") or "" for tool_call in (getattr(message, "tool_calls", None) or [])
        ]
    return f"{message.role}|{message_to_text(message)}|{'|'.join(tool_call_ids)}"


def iter_tool_calls(messages: list[BaseMessage], name: str | None = None):
    for message in messages:
        if not isinstance(message, AssistantMessage):
            continue
        for tool_call in getattr(message, "tool_calls", None) or []:
            tool_name = getattr(tool_call, "name", "") or ""
            if name is not None and tool_name != name:
                continue
            yield tool_call


def find_tool_result_text(messages: list[BaseMessage], tool_call_id: str | None) -> str:
    if not tool_call_id:
        return ""
    for message in reversed(messages):
        if isinstance(message, ToolMessage) and getattr(message, "tool_call_id", None) == tool_call_id:
            return message_to_text(message)
    return ""


def collect_read_file_paths(messages: list[BaseMessage]) -> set[str]:
    paths: set[str] = set()
    for message in messages:
        if not isinstance(message, ToolMessage):
            continue
        result = parse_jsonish_tool_result(message_to_text(message))
        if isinstance(result, dict) and isinstance(result.get("file_path"), str):
            paths.add(result["file_path"])
    return paths


def parse_read_file_result(tool_call: Any, result_text: str) -> ReadFileSnapshot | None:
    args_text = getattr(tool_call, "arguments", "") or ""
    args = parse_tool_arguments(args_text)
    result = parse_jsonish_tool_result(result_text)
    file_path = ""
    content: Any = None
    line_count: Any = None
    if isinstance(result, dict):
        file_path = result.get("file_path") or ""
        content = result.get("content")
        line_count = result.get("line_count")
    else:
        content = result_text
    file_path = file_path or extract_argument_value(args, args_text, ("file_path", "path"))
    if not isinstance(content, str) and result_text:
        content = result_text
    if not isinstance(file_path, str) or not isinstance(content, str):
        return None
    if line_count is None:
        line_count = len(content.splitlines())
    offset = args.get("offset")
    limit = args.get("limit")
    partial = offset is not None or limit is not None
    return ReadFileSnapshot(
        file_path=file_path,
        content=content,
        line_count=line_count if isinstance(line_count, int) else None,
        partial=partial,
    )


def is_excluded_from_reinject(file_path: str) -> bool:
    normalized = file_path.replace("\\", "/").lower()
    return any(part in normalized for part in ("/.git/", "/node_modules/", "/__pycache__/"))


def render_read_file_snapshots(
    snapshots: list[ReadFileSnapshot],
    truncate: Any,
    per_file_chars: int,
) -> str:
    blocks: list[str] = []
    for snapshot in snapshots:
        content = snapshot.content
        if len(content) > per_file_chars:
            head = content[: max(per_file_chars // 2, 0)]
            tail = content[-max(per_file_chars - len(head), 0):]
            content = f"{head}\n...[READ_FILE_TRUNCATED]...\n{tail}"
        lines = [
            f"Recently read file: {snapshot.file_path}",
            f"Lines returned: {snapshot.line_count if snapshot.line_count is not None else 'unknown'}",
        ]
        if snapshot.partial:
            lines.append("Note: only part of this file was available.")
        lines.extend(["", content])
        blocks.append(truncate("\n".join(lines)))
    return "\n\n".join(blocks)


def parse_tool_arguments(arguments_text: str) -> dict[str, Any]:
    if not arguments_text:
        return {}
    try:
        parsed = json.loads(arguments_text)
        return parsed if isinstance(parsed, dict) else {}
    except Exception:
        return {}


def parse_jsonish_tool_result(result_text: str) -> Any:
    if not result_text:
        return {}
    try:
        return json.loads(result_text)
    except (TypeError, ValueError):
        return _parse_data_field(result_text)


def _parse_data_field(result_text: str) -> Any:
    match = re.search(r"\bdata=(?P<data>\{.*?\})(?:\s+\w+=|$)", result_text, flags=re.DOTALL)
    if not match:
        return {}
    try:
        parsed = ast.literal_eval(match.group("data"))
    except (SyntaxError, ValueError):
        return {}
    return parsed if isinstance(parsed, dict) else {}


def extract_argument_value(parsed_arguments: dict[str, Any], arguments_text: str, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = parsed_arguments.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in keys:
        match = re.search(rf'"{re.escape(key)}"\s*:\s*"([^"]+)"', arguments_text)
        if match:
            return match.group(1).strip()
    return ""


def message_to_text(message: BaseMessage) -> str:
    content = getattr(message, "content", "")
    if isinstance(content, str):
        return content
    try:
        return json.dumps(content, ensure_ascii=False)
    except TypeError:
        return str(content)
