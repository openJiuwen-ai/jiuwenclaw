from __future__ import annotations

import json
import logging
import re
import shutil
from pathlib import Path
from typing import Any, TYPE_CHECKING

from jiuwenclaw.common.utils import get_agent_sessions_dir

if TYPE_CHECKING:
    from openjiuwen.harness import DeepAgent

logger = logging.getLogger(__name__)


def _derive_first_prompt(history: list[dict[str, Any]]) -> str:
    for record in history:
        if record.get("role") != "user":
            continue
        content = record.get("content", "")
        if not isinstance(content, str) or not content.strip():
            continue
        text = re.sub(r"\s+", " ", content).strip()
        return text[:100] if text else "Branched conversation"
    return "Branched conversation"


def _get_unique_fork_name(base_name: str, existing_titles: set[str]) -> str:
    """Generate a unique fork title.

    With custom name:  "custom-name (Branch)"
    Without name:      "(Branch)" or "(Branch N)"
    """
    candidate = f"{base_name} (Branch)" if base_name else "(Branch)"
    if candidate not in existing_titles:
        return candidate
    pattern = re.compile(
        r"^" + re.escape(base_name) + r" \(Branch(?: (\d+))?\)$"
        if base_name
        else r"^\(Branch(?: (\d+))?\)$"
    )
    used_numbers: set[int] = {1}
    for title in existing_titles:
        m = pattern.match(title)
        if m:
            num = int(m.group(1)) if m.group(1) else 1
            used_numbers.add(num)
    next_number = 2
    while next_number in used_numbers:
        next_number += 1
    return f"{base_name} (Branch {next_number})" if base_name else f"(Branch {next_number})"


def fork_session(
    *,
    source_session_id: str,
    target_session_id: str,
    title: str = "",
    channel_id: str = "tui",
) -> dict[str, Any]:
    sessions_dir = get_agent_sessions_dir()
    source_dir = sessions_dir / source_session_id
    target_dir = sessions_dir / target_session_id

    if not source_dir.exists():
        raise ValueError("source session not found")
    if target_dir.exists():
        raise ValueError("target session already exists")

    target_dir.mkdir(parents=True, exist_ok=True)

    source_history = source_dir / "history.json"
    target_history = target_dir / "history.json"
    history_data: list[dict[str, Any]] = []
    if source_history.exists():
        shutil.copy2(source_history, target_history)
        try:
            data = json.loads(target_history.read_text(encoding="utf-8"))
            if isinstance(data, list):
                history_data = data
                for record in data:
                    record["forked_from"] = {
                        "session_id": source_session_id,
                        "original_id": record.get("id", ""),
                    }
                target_history.write_text(
                    json.dumps(data, ensure_ascii=False, indent=2),
                    encoding="utf-8",
                )
        except Exception as exc:
            logger.warning("fork: failed to add forked_from to history: %s", exc)

    from jiuwenclaw.server.runtime.session.session_metadata import (
        _current_timestamp,
        _enqueue_write,
        get_all_sessions_metadata,
        get_session_metadata,
    )

    source_meta = get_session_metadata(source_session_id)

    if title:
        base_name = title
    elif source_meta.get("title"):
        base_name = source_meta["title"]
    else:
        # Don't derive from first prompt — "(Branch)" alone is cleaner
        # for the status bar. First prompt like "hi" makes an ugly title.
        base_name = ""

    existing_titles: set[str] = set()
    try:
        all_sessions = get_all_sessions_metadata(limit=500, offset=0)
        if isinstance(all_sessions, list):
            for s in all_sessions:
                t = s.get("title", "")
                if t:
                    existing_titles.add(t)
    except Exception as exc:
        logger.debug("fork_session: failed to get existing titles: %s", exc)

    final_title = _get_unique_fork_name(base_name, existing_titles)
    source_mode = source_meta.get("mode", "code.normal")

    metadata = {
        "session_id": target_session_id,
        "channel_id": channel_id,
        "user_id": source_meta.get("user_id", ""),
        "created_at": _current_timestamp(),
        "last_message_at": source_meta.get("last_message_at", 0),
        "title": final_title,
        "message_count": source_meta.get("message_count", 0),
        "mode": source_mode,
        "forked_from": source_session_id,
    }
    _enqueue_write(target_session_id, metadata)

    return {
        "session_id": target_session_id,
        "source_session_id": source_session_id,
        "title": final_title,
    }


def rewind_session(
    *,
    session_id: str,
    turn_index: int,
) -> dict[str, Any]:
    if turn_index < 1:
        raise ValueError("turn_index must be >= 1")

    sessions_dir = get_agent_sessions_dir()
    history_path = sessions_dir / session_id / "history.json"
    if not history_path.exists():
        raise ValueError("session history not found")

    from jiuwenclaw.server.runtime.session.session_history import truncate_history_records

    history = json.loads(history_path.read_text(encoding="utf-8"))
    if not isinstance(history, list):
        raise ValueError("invalid history format")

    user_positions = []
    for i, record in enumerate(history):
        if record.get("role") == "user":
            user_positions.append(i)

    total_turns = len(user_positions)
    if total_turns == 0:
        raise ValueError("no user messages in session")
    if turn_index > total_turns:
        raise ValueError(
            f"turn_index {turn_index} exceeds total turns ({total_turns})"
        )

    target_user_index = user_positions[turn_index - 1]
    cut_index = target_user_index

    removed_turn_content = ""
    if 0 <= target_user_index < len(history):
        content = history[target_user_index].get("content", "")
        removed_turn_content = content if isinstance(content, str) else str(content)

    result = truncate_history_records(session_id=session_id, cut_index=cut_index)

    from jiuwenclaw.server.runtime.session.session_metadata import update_session_metadata

    update_session_metadata(
        session_id=session_id,
        set_message_count=result["remaining_records"],
    )

    return {
        "session_id": session_id,
        "turn_index": turn_index,
        "content": removed_turn_content,
        "content_preview": removed_turn_content[:80] if removed_turn_content else "",
        "remaining_records": result["remaining_records"],
        "removed_records": result["removed_records"],
    }


_NON_USER_AUTHORED_TAGS = (
    "<local-command-stdout>",
    "<local-command-stderr>",
    "<bash-stdout>",
    "<bash-stderr>",
    "<task-notification>",
    "<tick>",
    "<teammate-message",
)


def _is_selectable_user_message(content: str) -> bool:
    for tag in _NON_USER_AUTHORED_TAGS:
        if tag in content:
            return False
    return True


def list_session_turns(
    *,
    session_id: str,
) -> dict[str, Any]:
    sessions_dir = get_agent_sessions_dir()
    history_path = sessions_dir / session_id / "history.json"

    if not history_path.exists():
        return {"turns": [], "total": 0}

    try:
        history = json.loads(history_path.read_text(encoding="utf-8"))
    except Exception as exc:
        logger.warning("list_session_turns: failed to read history: %s", exc)
        return {"turns": [], "total": 0}

    if not isinstance(history, list):
        return {"turns": [], "total": 0}

    diff_stats_map: dict[int, dict[str, int]] = {}
    try:
        from jiuwenclaw.server.utils.diff_service import get_diff_service

        diff_service = get_diff_service()
        turn_diffs = diff_service.get_turn_diffs(session_id)
        if isinstance(turn_diffs, list):
            for td in turn_diffs:
                ti = td.get("turnIndex")
                if isinstance(ti, int) and ti > 0:
                    diff_stats_map[ti] = td.get("stats", {})
    except Exception as exc:
        logger.debug("list_session_turns: diff service unavailable: %s", exc)

    turns = []
    user_count = 0
    for record in history:
        if record.get("role") != "user":
            continue
        user_count += 1
        content = record.get("content", "")
        if isinstance(content, str) and not _is_selectable_user_message(content):
            continue
        preview = content[:80] if isinstance(content, str) else ""
        stats = diff_stats_map.get(user_count, {
            "filesChanged": 0,
            "linesAdded": 0,
            "linesRemoved": 0,
        })
        turns.append({
            "turn_index": user_count,
            "content_preview": preview,
            "timestamp": record.get("timestamp", 0),
            "id": record.get("id", ""),
            "request_id": record.get("request_id", ""),
            "stats": stats,
        })

    return {"turns": turns, "total": user_count}


def restore_session_files(
    *,
    session_id: str,
    turn_index: int,
) -> dict[str, Any]:
    """恢复指定 turn 之后所有被修改的文件到目标 turn 开始前的状态.

    基于 DiffService.get_files_to_restore() 确定需要恢复的文件，
    然后将每个文件写回其 old_content（或删除 agent 新建的文件）。

    局限性（底层暂不支持，后续迭代）：
    - bash 命令修改的文件不在 file_ops 日志中，无法恢复
    - 文件删除操作未记录在 file_ops 中，无法恢复被删除的文件
    - 多 session 共享 file_ops 日志，若其他 session 也修改了同一文件，
      时间戳匹配可能不够精确
    """
    from jiuwenclaw.server.utils.diff_service import get_diff_service

    diff_service = get_diff_service()
    files_to_restore = diff_service.get_files_to_restore(session_id, turn_index)

    if not files_to_restore:
        return {
            "session_id": session_id,
            "turn_index": turn_index,
            "restored_files": [],
            "deleted_files": [],
            "errors": [],
        }

    restored: list[str] = []
    deleted: list[str] = []
    errors: list[dict[str, str]] = []

    for file_path, info in files_to_restore.items():
        path = Path(file_path)
        try:
            if info["action"] == "write":
                # 文件在目标 turn 前已有内容，写回 old_content
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(info["restore_content"], encoding="utf-8")
                restored.append(file_path)
            elif info["action"] == "delete":
                # 文件由 agent 在目标 turn 后创建，删除
                if path.exists():
                    path.unlink()
                    deleted.append(file_path)
        except Exception as exc:
            errors.append({"file": file_path, "error": str(exc)})
            logger.warning(
                "restore_session_files: failed to restore %s: %s",
                file_path, exc,
            )

    logger.info(
        "restore_session_files: session=%s turn=%s restored=%d deleted=%d errors=%d",
        session_id, turn_index, len(restored), len(deleted), len(errors),
    )

    return {
        "session_id": session_id,
        "turn_index": turn_index,
        "restored_files": restored,
        "deleted_files": deleted,
        "errors": errors,
    }


async def copy_session_context(
    deep_agent: "DeepAgent",
    source_session_id: str,
    target_session_id: str,
) -> bool:
    """Copy in-memory conversation context from source to target session.

    Uses DeepAgent.get_current_context() to read the source session's
    accumulated LLM conversation history (UserMessage, AssistantMessage,
    ToolMessage objects), then calls create_new_context_engine() to
    seed the target session with identical history.

    Returns True on success, False if context copy was skipped or failed.
    """
    try:
        messages = deep_agent.get_current_context(session_id=source_session_id)
    except Exception as exc:
        logger.warning(
            "copy_session_context: cannot read source context: %s", exc
        )
        return False

    if not messages:
        logger.info(
            "copy_session_context: no in-memory messages for %s, skipping",
            source_session_id,
        )
        return False

    try:
        await deep_agent.create_new_context_engine(
            session_id=target_session_id,
            messages=messages,
        )
        logger.info(
            "copy_session_context: copied %d messages from %s to %s",
            len(messages), source_session_id, target_session_id,
        )
        return True
    except Exception as exc:
        logger.warning(
            "copy_session_context: failed to seed target context: %s", exc
        )
        return False
