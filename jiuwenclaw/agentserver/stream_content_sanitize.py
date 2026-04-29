# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""
流式内容剥离工具：去除模型/网关写入 delta.content 的内联工具协议串。

已知泄漏格式（以实际样本为准）：
  <tool_calls_begin><tool_call_begin>function<tool_sep>tool_name{"key": "val"}</tool_call_end></tool_calls_end>

若闭合标签不出现（流式截断），则按「平衡括号+EOF」策略处理。
供 jiuwen_core_patch.py / react_agent.py / interface.py 复用。
"""
from __future__ import annotations

import os
import re
from typing import Optional

# ---------------------------------------------------------------------------
# 可配置常量
# ---------------------------------------------------------------------------

# 外层标签对（顺序无关，以出现的第一个开放标签为锚点）
_OPEN_TAGS: tuple[str, ...] = (
    "<tool_calls_begin>",
    "<tool_call_begin>",
)
_CLOSE_TAGS: tuple[str, ...] = (
    "</tool_calls_end>",
    "</tool_call_end>",
)

# 内层分隔符
_FUNC_SEP = "function<tool_sep>"

# 工具名模式（跟在 function<tool_sep> 后）
_TOOL_NAME_RE = re.compile(r"[A-Za-z0-9_]+")

# 白名单模式：None 表示全量匹配；设置后仅当工具名在白名单内时剥离
# 可通过环境变量 STREAM_STRIP_INLINE_TOOLS=all 切换到全量
_DEFAULT_TOOL_WHITELIST: Optional[frozenset[str]] = frozenset(
    [
        "todo_create",
        "todo_complete",
        "todo_insert",
        "todo_remove",
        "todo_list",
    ]
)


# 环境变量覆盖：all -> None（全量），whitelist（默认）-> _DEFAULT_TOOL_WHITELIST
def _build_whitelist() -> Optional[frozenset[str]]:
    mode = os.environ.get("STREAM_STRIP_INLINE_TOOLS", "whitelist").strip().lower()
    if mode == "all":
        return None
    return _DEFAULT_TOOL_WHITELIST


_TOOL_WHITELIST: Optional[frozenset[str]] = _build_whitelist()

# _find_complete_protocol_end 与 _split_safe_and_pending 使用的状态码
_PROTOCOL_INCOMPLETE = -1
_NOT_INLINE_TOOL_PROTOCOL = -2


# ---------------------------------------------------------------------------
# 底层工具：平衡括号查找
# ---------------------------------------------------------------------------

def _find_balanced_json_end(text: str, start: int) -> int:
    """
    从 text[start] (必须为 '{') 起扫描，返回平衡闭合 '}' 之后的索引位置。
    若到文末仍未平衡，返回 -1。
    """
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
                    return i + 1  # 返回闭合位置之后
        i += 1
    return -1  # 未闭合


# ---------------------------------------------------------------------------
# 核心剥离函数（处理完整文本）
# ---------------------------------------------------------------------------

def _tool_name_allowed(name: str) -> bool:
    if _TOOL_WHITELIST is None:
        return True
    return name in _TOOL_WHITELIST


def _tool_name_may_be_incomplete_prefix(name: str) -> bool:
    """
    在白名单模式下，当前匹配到的工具名前缀是否仍有可能在后续 chunk 中补全为合法工具名。
    """
    if _TOOL_WHITELIST is None:
        return False
    return any(tool.startswith(name) and tool != name for tool in _TOOL_WHITELIST)


def _find_earliest_open_tag(text: str, from_pos: int = 0) -> tuple[int, str]:
    """返回从 from_pos 起最早出现的外层开放标签的 (pos, tag)，无则 (-1, '')。"""
    best_pos = -1
    best_tag = ""
    for tag in _OPEN_TAGS:
        pos = text.find(tag, from_pos)
        if pos != -1 and (best_pos == -1 or pos < best_pos):
            best_pos = pos
            best_tag = tag
    return best_pos, best_tag


def _find_func_sep(text: str, from_pos: int = 0) -> int:
    """返回 function<tool_sep> 在 text[from_pos:] 中的起始位置，无则 -1。"""
    return text.find(_FUNC_SEP, from_pos)


def strip_inline_tool_protocol(text: str) -> str:
    """
    对完整文本（非流式），循环剥离所有已闭合的内联工具协议段。
    未闭合的尾部（EOF 截断）也会自锚点起删至末尾。

    外层标签优先匹配；无外层标签时，回退到只匹配 function<tool_sep>...{...}。

    返回剥离后的干净文本。
    """
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
    """单次扫描，删除最早找到的一段完整或截断协议，返回结果。"""
    pos = 0
    n = len(text)

    while pos < n:
        # 尝试找最早的外层开放标签或内层 func_sep
        tag_pos, tag = _find_earliest_open_tag(text, pos)
        func_pos = _find_func_sep(text, pos)

        # 确定优先锚点
        if tag_pos == -1 and func_pos == -1:
            break  # 无协议，直接返回

        if tag_pos != -1 and (func_pos == -1 or tag_pos <= func_pos):
            # 外层标签路径
            anchor = tag_pos
            # 在锚点之后找 function<tool_sep>
            inner_func = _find_func_sep(text, anchor)
            if inner_func == -1:
                # 有外层标签但无内层；从开标签结束之后扫描闭合标签（不能把开标签本身算进 gap）
                close_end = _find_close_tags(text, anchor + len(tag))
                if close_end != -1:
                    # 整段删掉（开标签到闭标签结束）
                    text = text[:anchor] + text[close_end:]
                else:
                    # 无闭合 → 截断，锚点起删至末尾
                    text = text[:anchor]
                return text

            # 有内层 function<tool_sep>
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
                # JSON 未闭合，从锚点起删至末尾
                text = text[:anchor]
                return text

            # JSON 已闭合；继续找闭合 XML 标签
            close_end = _find_close_tags(text, json_end)
            seg_end = close_end if close_end != -1 else json_end
            text = text[:anchor] + text[seg_end:]
            return text

        else:
            # 纯内层 function<tool_sep> 路径（无外层标签）
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
                # 截断，从锚点删至末尾
                text = text[:anchor]
                return text

            text = text[:anchor] + text[json_end:]
            return text

    return text


def _find_close_tags(text: str, from_pos: int) -> int:
    """
    从 from_pos 起找到最早的闭合标签，其后可贪婪串联更多闭合标签（彼此间仅允许空白）。
    第一段：from_pos 到「第一个闭合标签」之间允许任意内容（外层 blob 或 JSON 尾部）。
    """
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


# ---------------------------------------------------------------------------
# 流式缓冲辅助（用于 _call_llm_stream）
# ---------------------------------------------------------------------------

class StreamProtocolBuffer:
    """
    在流式累积文本时，将「可能是协议开头」的尾部缓冲起来，
    只把确认安全的前缀暴露出去。

    用法::

        buf = StreamProtocolBuffer()
        for chunk in llm_stream:
            content = chunk.content or ""
            safe = buf.feed(content)
            if safe:
                # write safe to stream
        remainder = buf.flush()
        if remainder:
            # write remainder (protocol fully stripped or genuinely safe tail)

    """

    def __init__(self) -> None:
        self._pending: str = ""

    def feed(self, new_text: str) -> str:
        """
        追加 new_text；返回此刻可以安全写出的前缀（协议部分已缓冲/剥离）。
        """
        combined = self._pending + new_text
        safe, self._pending = _split_safe_and_pending(combined)
        return safe

    def flush(self) -> str:
        """
        流结束时调用；对剩余缓冲做 EOF 截断处理（strip 不完整协议）并返回。
        """
        if not self._pending:
            return ""
        result = strip_inline_tool_protocol(self._pending)
        self._pending = ""
        return result


def _find_complete_protocol_end(text: str, anchor: int) -> int:
    """
    如果从 anchor 开始是一段**完整已闭合**的协议，返回该段结束位置（exclusive）。
    如果协议不完整（截断/不匹配），返回 _PROTOCOL_INCOMPLETE。
    如果锚点只是普通文本中恰好出现的协议样式前缀，则返回 _NOT_INLINE_TOOL_PROTOCOL。
    """
    n = len(text)

    matched_open = next((t for t in _OPEN_TAGS if text.startswith(t, anchor)), "")
    if matched_open:
        # 外层标签路径
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
        # 纯内层路径
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
    """
    将 text 分割为 (safe_prefix, pending_suffix)：
    - safe_prefix：已完整且无协议泄漏（完整协议已剥除）的前缀，可直接写入流。
    - pending_suffix：从第一个**不完整**协议锚点起到文末，暂缓输出等待后续 chunk。

    策略：从左至右扫描协议锚点：
    - 完整协议 → 跳过（剥除），继续扫描其后的内容。
    - 不完整协议 → 作为 pending 边界，锚点之前为 safe，锚点之后为 pending。
    """
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
        # 锚点前的文本是安全的
        safe_parts.append(text[pos:anchor])

        end = _find_complete_protocol_end(text, anchor)
        if end == _PROTOCOL_INCOMPLETE:
            # 不完整协议：从 anchor 起作为 pending
            return "".join(safe_parts), text[anchor:]
        if end == _NOT_INLINE_TOOL_PROTOCOL:
            # 普通文本中命中了锚点样式，按普通字符继续透传，避免整段卡在 pending。
            safe_parts.append(text[anchor:anchor + 1])
            pos = anchor + 1
            continue
        # 完整协议：跳过，继续
        pos = end

    return "".join(safe_parts), ""
