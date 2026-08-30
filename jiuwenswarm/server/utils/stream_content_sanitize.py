# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
流式内容剥离工具：去除模型/网关写入 delta.content 的内联工具协议串。

已知泄漏格式（以实际样本为准）：
  <tool_calls_begin><tool_call_begin>function<tool_sep>tool_name{"key": "val"}</tool_call_end></tool_calls_end>

若闭合标签不出现（流式截断），则按「平衡括号+EOF」策略处理。
供 stream_utils.py / interface_deep.py 复用。
"""
from __future__ import annotations

import os
import re
from typing import Optional

# ---------------------------------------------------------------------------
# 可配置常量
# ---------------------------------------------------------------------------

_OPEN_TAGS: tuple[str, ...] = (
    "<tool_calls_begin>",
    "<tool_call_begin>",
)
_CLOSE_TAGS: tuple[str, ...] = (
    "</tool_calls_end>",
    "</tool_call_end>",
)

_FUNC_SEP = "function<tool_sep>"

_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_]+")

_DEFAULT_TOOL_WHITELIST: Optional[frozenset[str]] = frozenset(
    [
        "todo_create",
        "todo_complete",
        "todo_insert",
        "todo_remove",
        "todo_list",
    ]
)


def _build_whitelist() -> Optional[frozenset[str]]:
    mode = os.environ.get("STREAM_STRIP_INLINE_TOOLS", "whitelist").strip().lower()
    if mode == "all":
        return None
    return _DEFAULT_TOOL_WHITELIST


_TOOL_WHITELIST: Optional[frozenset[str]] = _build_whitelist()

_PROTOCOL_INCOMPLETE = -1
_NOT_INLINE_TOOL_PROTOCOL = -2


def _find_balanced_json_end(text: str, start: int) -> int:
    depth = 0
    in_string = False
    escape_next = False
    i = start
    n = len(text)
    while i < n:
        ch = text[i]
        if escape_next:
            escape_next = False
        elif in_string:
            if ch == "\\":
                escape_next = True
            elif ch == '"':
                in_string = False
        else:
            if ch == '"':
                in_string = True
            elif ch in "{[":
                depth += 1
            elif ch in "}]":
                depth -= 1
                if depth == 0:
                    return i + 1
        i += 1
    return -1


def _tool_name_allowed(name: str) -> bool:
    if _TOOL_WHITELIST is None:
        return True
    return name in _TOOL_WHITELIST


def _tool_name_may_be_incomplete_prefix(name: str) -> bool:
    if _TOOL_WHITELIST is None:
        return False
    return any(tool.startswith(name) and tool != name for tool in _TOOL_WHITELIST)


def _find_earliest_open_tag(text: str, from_pos: int = 0) -> tuple[int, str]:
    best_pos = -1
    best_tag = ""
    for tag in _OPEN_TAGS:
        pos = text.find(tag, from_pos)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos
            best_tag = tag
    return best_pos, best_tag


def _find_func_sep(text: str, from_pos: int = 0) -> int:
    return text.find(_FUNC_SEP, from_pos)


def strip_inline_tool_protocol(text: str) -> str:
    """对完整文本，循环剥离所有已闭合或截断的内联工具协议段。"""
    if not text:
        return text

    result = text
    changed = True
    while changed:
        changed = False
        new_result = _strip_one_pass(result)
        if new_result != result:
            result = new_result
            changed = True
    return result


def _strip_one_pass(text: str) -> str:
    pos = 0
    n = len(text)

    while pos < n:
        tag_pos, tag = _find_earliest_open_tag(text, pos)
        func_pos = _find_func_sep(text, pos)

        if tag_pos == -1 and func_pos == -1:
            break

        if tag_pos != -1 and (func_pos == -1 or tag_pos <= func_pos):
            anchor = tag_pos
            inner_func = _find_func_sep(text, anchor)
            if inner_func == -1:
                close_end = _find_close_tags(text, anchor + len(tag))
                if close_end != -1:
                    text = text[:anchor] + text[close_end:]
                else:
                    text = text[:anchor]
                return text

            name_start = inner_func + len(_FUNC_SEP)
            m = _TOOL_NAME_RE.match(text, name_start)
            if not m:
                pos = inner_func + 1
                continue
            tool_name = m.group(0)
            if not _tool_name_allowed(tool_name):
                if _tool_name_may_be_incomplete_prefix(tool_name):
                    text = text[:anchor]
                    return text
                pos = inner_func + 1
                continue

            json_start = m.end()
            if json_start >= n or text[json_start] != "{":
                pos = json_start
                continue

            json_end = _find_balanced_json_end(text, json_start)
            if json_end == -1:
                text = text[:anchor]
                return text

            close_end = _find_close_tags(text, json_end)
            seg_end = close_end if close_end != -1 else json_end
            text = text[:anchor] + text[seg_end:]
            return text

        else:
            anchor = func_pos
            name_start = anchor + len(_FUNC_SEP)
            m = _TOOL_NAME_RE.match(text, name_start)
            if not m:
                pos = anchor + 1
                continue
            tool_name = m.group(0)
            if not _tool_name_allowed(tool_name):
                if _tool_name_may_be_incomplete_prefix(tool_name):
                    text = text[:anchor]
                    return text
                pos = anchor + 1
                continue

            json_start = m.end()
            if json_start >= n or text[json_start] != "{":
                pos = json_start
                continue

            json_end = _find_balanced_json_end(text, json_start)
            if json_end == -1:
                text = text[:anchor]
                return text

            text = text[:anchor] + text[json_end:]
            return text

    return text


def _find_close_tags(text: str, from_pos: int) -> int:
    pos = from_pos
    found_any = False
    while True:
        best_idx = -1
        best_tag = ""
        for tag in _CLOSE_TAGS:
            idx = text.find(tag, pos)
            if idx != -1 and (best_idx == -1 or idx < best_idx):
                best_idx = idx
                best_tag = tag
        if best_idx == -1:
            break
        gap = text[pos:best_idx]
        if found_any and gap.strip():
            break
        pos = best_idx + len(best_tag)
        found_any = True
    return pos if found_any else -1


class StreamProtocolBuffer:
    """在流式累积文本时缓冲可能是协议开头的尾部，只暴露确认安全的前缀。"""

    def __init__(self) -> None:
        self._pending: str = ""

    def feed(self, new_text: str) -> str:
        combined = self._pending + new_text
        safe, self._pending = _split_safe_and_pending(combined)
        return safe

    def flush(self) -> str:
        if not self._pending:
            return ""
        result = strip_inline_tool_protocol(self._pending)
        self._pending = ""
        return result


def _find_complete_protocol_end(text: str, anchor: int) -> int:
    n = len(text)

    matched_open = next((t for t in _OPEN_TAGS if text.startswith(t, anchor)), "")
    if matched_open:
        inner_func = _find_func_sep(text, anchor)
        if inner_func == -1:
            close_end = _find_close_tags(text, anchor + len(matched_open))
            return close_end if close_end != -1 else _PROTOCOL_INCOMPLETE
        name_start = inner_func + len(_FUNC_SEP)
        m = _TOOL_NAME_RE.match(text, name_start)
        if not m:
            return _PROTOCOL_INCOMPLETE if name_start >= n else _NOT_INLINE_TOOL_PROTOCOL
        tool_name = m.group(0)
        if not _tool_name_allowed(tool_name):
            return (
                _PROTOCOL_INCOMPLETE
                if _tool_name_may_be_incomplete_prefix(tool_name)
                else _NOT_INLINE_TOOL_PROTOCOL
            )
        json_start = m.end()
        if json_start >= n:
            return _PROTOCOL_INCOMPLETE
        if text[json_start] != "{":
            return _NOT_INLINE_TOOL_PROTOCOL
        json_end = _find_balanced_json_end(text, json_start)
        if json_end == -1:
            return _PROTOCOL_INCOMPLETE
        close_end = _find_close_tags(text, json_end)
        return close_end if close_end != -1 else json_end

    if text.startswith(_FUNC_SEP, anchor):
        name_start = anchor + len(_FUNC_SEP)
        m = _TOOL_NAME_RE.match(text, name_start)
        if not m:
            return _PROTOCOL_INCOMPLETE if name_start >= n else _NOT_INLINE_TOOL_PROTOCOL
        tool_name = m.group(0)
        if not _tool_name_allowed(tool_name):
            return (
                _PROTOCOL_INCOMPLETE
                if _tool_name_may_be_incomplete_prefix(tool_name)
                else _NOT_INLINE_TOOL_PROTOCOL
            )
        json_start = m.end()
        if json_start >= n:
            return _PROTOCOL_INCOMPLETE
        if text[json_start] != "{":
            return _NOT_INLINE_TOOL_PROTOCOL
        json_end = _find_balanced_json_end(text, json_start)
        return json_end if json_end != -1 else _PROTOCOL_INCOMPLETE

    return _NOT_INLINE_TOOL_PROTOCOL


def _split_safe_and_pending(text: str) -> tuple[str, str]:
    safe_parts: list[str] = []
    pos = 0
    n = len(text)

    while pos < n:
        tag_pos, _ = _find_earliest_open_tag(text, pos)
        func_pos = _find_func_sep(text, pos)

        anchors = [p for p in (tag_pos, func_pos) if p != -1]
        if not anchors:
            safe_parts.append(text[pos:])
            return "".join(safe_parts), ""

        anchor = min(anchors)
        safe_parts.append(text[pos:anchor])

        end = _find_complete_protocol_end(text, anchor)
        if end == _PROTOCOL_INCOMPLETE:
            return "".join(safe_parts), text[anchor:]
        if end == _NOT_INLINE_TOOL_PROTOCOL:
            safe_parts.append(text[anchor:anchor + 1])
            pos = anchor + 1
            continue
        pos = end

    return "".join(safe_parts), ""
