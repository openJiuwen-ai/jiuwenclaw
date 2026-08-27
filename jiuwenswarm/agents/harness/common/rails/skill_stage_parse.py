# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Parse numbered SKILL.md stage headings into todo items.

Stage count and labels come from the loaded skill body, not a hardcoded list.
Headings like ``## 阶段 1：…`` / ``## Stage 2: …`` are the source of truth.
Unnumbered sections such as ``## 阶段规划`` are ignored.
"""

from __future__ import annotations

import json
import re
from typing import Any

_STAGE_HEADING_RE = re.compile(
    r"^#{1,3}\s+(阶段|Stage)\s+(\d+)\s*[:：]\s*(.+?)\s*$",
    re.MULTILINE | re.IGNORECASE,
)
_SKILL_STAGE_ID_PREFIX = "skill_stage_"
_SKILL_CONTENT_IN_REPR_RE = re.compile(
    r"['\"]skill_content['\"]\s*:\s*['\"](.*?)['\"]"
    r"(?:\s*,\s*['\"]directory_tree|\s*\})",
    re.DOTALL,
)


def parse_skill_tool_args(tool_args: Any) -> tuple[str, str]:
    if isinstance(tool_args, str):
        try:
            tool_args = json.loads(tool_args)
        except (TypeError, ValueError):
            return "", ""
    if not isinstance(tool_args, dict):
        return "", ""
    skill_name = str(tool_args.get("skill_name") or "").strip()
    relative_path = str(tool_args.get("relative_file_path") or "").strip()
    return skill_name, relative_path


def is_top_level_skill_body(relative_file_path: str) -> bool:
    path = relative_file_path.replace("\\", "/").strip().lstrip("./")
    return path.lower() in ("", "skill.md")


def extract_skill_markdown(tool_result: Any, tool_msg: Any = None) -> str:
    meta = getattr(tool_msg, "metadata", None) or {}
    if isinstance(meta, dict):
        for key in ("skill_content", "skill_markdown"):
            value = meta.get(key)
            if isinstance(value, str) and value.strip():
                return value
    found = _find_skill_content(tool_result)
    if found:
        return found
    if isinstance(tool_result, str):
        match = _SKILL_CONTENT_IN_REPR_RE.search(tool_result)
        if match:
            return _unescape_repr_string(match.group(1))
    return ""


def parse_skill_stage_headings(markdown: str) -> list[tuple[int, str]]:
    """Return ``(stage_number, display_label)`` in heading order."""
    if not markdown:
        return []
    by_number: dict[int, str] = {}
    for match in _STAGE_HEADING_RE.finditer(markdown):
        keyword = match.group(1).strip()
        number = int(match.group(2))
        title = match.group(3).strip()
        if not title:
            continue
        prefix = "Stage" if keyword.lower() == "stage" else "阶段"
        by_number[number] = f"{prefix} {number}：{title}" if prefix == "阶段" else (
            f"Stage {number}: {title}"
        )
    return [(number, by_number[number]) for number in sorted(by_number)]


def build_todos_from_skill_stages(
    stages: list[tuple[int, str]],
) -> list[dict[str, Any]]:
    items: list[dict[str, Any]] = []
    for index, (number, label) in enumerate(stages):
        items.append(
            {
                "id": f"{_SKILL_STAGE_ID_PREFIX}{number}",
                "content": label,
                "activeForm": f"正在执行：{label}",
                "description": label,
                "status": "in_progress" if index == 0 else "pending",
            }
        )
    return items


def is_owned_skill_stage_id(task_id: str) -> bool:
    return str(task_id or "").startswith(_SKILL_STAGE_ID_PREFIX)


def format_seeded_stage_notice(items: list[dict[str, Any]]) -> str:
    lines = [
        "",
        f"[SYSTEM] 已按本 SKILL.md 的阶段标题创建 todo 列表（{len(items)} 项）。"
        "禁止再调用 todo_create 覆盖。请用 todo_modify 按下列 id 标记 completed：",
    ]
    for item in items:
        lines.append(f"- {item['id']}: {item['content']}")
    return "\n".join(lines)


def _find_skill_content(value: Any) -> str:
    if isinstance(value, dict):
        direct = value.get("skill_content")
        if isinstance(direct, str) and direct.strip():
            return direct
        for key in ("data", "raw_output", "rawOutput", "result"):
            nested = _find_skill_content(value.get(key))
            if nested:
                return nested
        return ""
    data = getattr(value, "data", None)
    if data is not None and data is not value:
        return _find_skill_content(data)
    return ""


def _unescape_repr_string(text: str) -> str:
    return (
        text.replace("\\\\", "\0")
        .replace("\\n", "\n")
        .replace("\\t", "\t")
        .replace("\\'", "'")
        .replace('\\"', '"')
        .replace("\0", "\\")
    )
