# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Retain DeepResearch's completed stage snapshot as harness todos."""

from __future__ import annotations

import json
import os
import tempfile
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.tools.deepresearch.stream_router import (
    DEEPRESEARCH_STAGES,
)
from jiuwenclaw.utils import resolve_tenant_agent_workspace_dir

_TODO_WRITE_LOCK = threading.Lock()
_TODO_STATUSES = frozenset({"pending", "in_progress", "completed", "cancelled"})


def deepresearch_todo_path(
    *,
    session_id: str,
    service_id: str,
    agent_id: str,
) -> Path:
    """Return the standard harness todo.json path for one tenant session."""
    workspace = resolve_tenant_agent_workspace_dir(service_id, agent_id)
    return workspace / "todo" / session_id / "todo.json"


def _existing_created_at(todo_path: Path) -> dict[str, str]:
    try:
        data = json.loads(todo_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, list):
        return {}
    return {
        str(item.get("id")): item["createdAt"]
        for item in data
        if (
            isinstance(item, dict)
            and isinstance(item.get("id"), str)
            and isinstance(item.get("createdAt"), str)
        )
    }


def _deepresearch_tasks(payload: dict[str, Any]) -> list[dict[str, str]] | None:
    if payload.get("event_type") != "task.update":
        return None
    tasks = payload.get("tasks")
    if not isinstance(tasks, list) or len(tasks) != len(DEEPRESEARCH_STAGES):
        return None

    normalized = []
    for index, title in enumerate(DEEPRESEARCH_STAGES, start=1):
        item = tasks[index - 1]
        expected_id = f"deepresearch_stage_{index}"
        if not isinstance(item, dict):
            return None
        if item.get("task_id") != expected_id or item.get("task_content") != title:
            return None
        status = item.get("status")
        if status not in _TODO_STATUSES:
            return None
        normalized.append({
            "id": expected_id,
            "content": title,
            "activeForm": title,
            "status": status,
        })
    return normalized


def persist_deepresearch_task_update(
    payload: dict[str, Any],
    *,
    todo_path: Path,
) -> bool:
    """Retain a completed canonical task.update in the standard todo file."""
    tasks = _deepresearch_tasks(payload)
    if tasks is None or any(task["status"] != "completed" for task in tasks):
        return False

    todo_path = Path(todo_path)
    with _TODO_WRITE_LOCK:
        created_at = _existing_created_at(todo_path)
        now = datetime.now(timezone.utc).isoformat()
        items = [
            {
                **task,
                "createdAt": created_at.get(task["id"], now),
                "updatedAt": now,
            }
            for task in tasks
        ]

        todo_path.parent.mkdir(parents=True, exist_ok=True)
        temp_path: Path | None = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                encoding="utf-8",
                dir=todo_path.parent,
                prefix=".deepresearch-todo-",
                suffix=".json",
                delete=False,
            ) as temp_file:
                json.dump(items, temp_file, ensure_ascii=False, indent=2)
                temp_file.write("\n")
                temp_path = Path(temp_file.name)
            os.replace(temp_path, todo_path)
        finally:
            if temp_path is not None and temp_path.exists():
                temp_path.unlink()
    return True
