#!/usr/bin/env python3
# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""OpenAI 兼容 Mock LLM HTTP 服务，供 Enterprise Runtime 压测与 E2E 联调使用。

实现 ``POST /v1/chat/completions``（流式 SSE / 非流式 JSON）、``GET /health``、``GET /v1/models``，
行为与 ``tests/system_tests/enterprise/mock_llm_server.py`` 一致，并增加压测场景常用选项
（``--host``、请求统计、毫秒时间戳日志）。

典型用法（项目根目录）::

    # E2E 快速流式（与 system test 相同参数）
    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/mock_llm_server.py \\
        --port 19999 --stream-token-count 5 --stream-token-interval 0.05

    # 压测：模拟真实 Agent 多工具流程（todo / 长文 / write_file / read_file / send_file）
    uv run python packages/jiuwenclaw-ee/claw_manager/scripts/mock_llm_server.py \\
        --host 0.0.0.0 --port 19999 --profile loadtest --novel-chars 32000

AgentServer / Gateway 侧模型配置示例（经 Runtime ``_agent_env_vars`` 或 model_template）::

    API_BASE=http://127.0.0.1:19999/v1
    API_KEY=mock-key
    MODEL_PROVIDER=OpenAI
    MODEL_NAME=mock-model
    LLM_SSL_VERIFY=false
"""

from __future__ import annotations

import argparse
import asyncio
import contextlib
import json
import logging
import re
import secrets
import signal
import sys
import time
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)

DEFAULT_STREAM_TOKEN_COUNT = 20
DEFAULT_STREAM_TOKEN_INTERVAL_S = 2.0
LOADTEST_STREAM_TOKEN_COUNT = 5
LOADTEST_STREAM_TOKEN_INTERVAL_S = 0.05
LOADTEST_NOVEL_CHARS = 32_000
LOADTEST_STREAM_CHUNK_CHARS = 256
LOADTEST_CHAT_EXCERPT_CHARS = 6_000
LOADTEST_WRITE_MAX_CHARS = 32_000

_LOADTEST_NOVEL_MARKERS = ("旅行的意义", "人生的意义", "十万字", "txt", "小说")
_NOVEL_FILENAME = "旅行的意义_开篇完整版.txt"
_FORBIDDEN_WRITE_PATH = "/__mock_loadtest_forbidden__/novel.txt"
# 不在 config builtin allow 规则内（echo/ls/pwd 等），可稳定触发 bash ASK
_PERMISSION_PROBE_BASH = "python3 -c \"print('mock_loadtest_permission_probe')\""
_INTRO_PERMISSION_BASH = (
    "接下来我需要执行一条 shell 命令来确认 Agent 工作区环境。"
    "这一步会触发 bash 权限审批，请允许后继续。\n"
)
_INTRO_PERMISSION_TODO_MODIFY = (
    "开篇已写入文件。接下来我需要更新任务清单状态，"
    "这一步会触发 todo_modify 权限审批，请允许后继续。\n"
)
_INTRO_PLAN = (
    "我看到你希望我专注于创作小说的开头部分。让我重新规划，"
    "专注于创作一个引人入胜的开篇，而不是立即尝试完成整部十万字的小说。\n"
)
_TODO_TASKS = (
    "《旅行的意义》开篇;"
    "人物详细介绍;"
    "故事背景设定;"
    "故事冲突与悬念设置;"
    "整理并发送开篇文件"
)
_TRAVEL_SCENE_BLOCKS = (
    (
        "1.\n\n雨下得很大。\n\n"
        "陈远站在火车站候车大厅的玻璃窗前，看着雨水在玻璃上划出一道道蜿蜒的痕迹。"
        "窗外的城市在雨幕中变得模糊，霓虹灯的光晕在湿漉漉的地面上晕开，像一幅被水洗过的油画。\n\n"
        "他低头看了看手表：晚上十一点四十七分。距离他辞职已经过去了三十六个小时，"
        "距离火车发车还有十三分钟。背包靠在脚边，里面装着他全部的家当。"
    ),
    (
        "2.\n\n火车驶出城市，进入郊野。雨渐渐小了，窗外是一片漆黑，只有偶尔闪过的零星灯光。\n\n"
        "陈远躺回铺位，闭上眼睛，却睡不着。脑海里反复播放着过去三十六小时的画面："
        "递交辞职信时经理惊讶的表情，母亲电话里带着哭腔的声音，朋友们不解的询问。"
        "也许他真的疯了——放弃年薪五十万的工作，只为了去一个遥远的地方，"
        "寻找一个可能根本不存在的答案。"
    ),
    (
        "3.\n\n凌晨三点，陈远被一阵轻微的啜泣声惊醒。声音来自对面铺位。"
        "苏菲蜷缩在铺位上，肩膀微微颤抖。\"你没事吧？\"陈远轻声问。\n\n"
        "\"只是……想家了。\"苏菲说，\"第一次离家这么远，感觉比出国还远。\""
        "陈远理解这种感觉——一路走来，他做了所有\"正确\"的选择，却离真实的自己越来越远。"
    ),
    (
        "4.\n\n清晨六点，火车停靠在一个小站。窗外是连绵的山峦，笼罩在薄雾中，像一幅水墨画。\n\n"
        "车厢连接处，一位老人正看着窗外的风景。\"您经常去西藏？\"陈远问。"
        "\"每年都去，已经十年了。\"老人说，\"同一个地方，不同的时间，就是不同的世界。\"\n\n"
        "\"您觉得旅行的意义是什么？\"陈远忍不住问。老人沉默了很久："
        "\"也许，意义不在于找到答案，而在于寻找的过程。\""
    ),
    (
        "5.\n\n中午时分，火车停靠在一个较大的车站。远方的天际线上，隐约可以看到雪山的轮廓。\n\n"
        "新乘客林晓背着摄影包上了车。\"旅行作家，兼摄影师。\"她说，"
        "\"不过这次……算是告别之旅吧。\"三个人都沉默了，只是看着窗外的风景。"
        "火车沿着湖岸行驶，阳光洒在湖面上，波光粼粼。\n\n"
        "陈远突然想起一句话：旅行不是为了到达目的地，而是为了学会如何到达。"
        "也许，答案就在路上。也许，问题本身就是答案。也许，旅行就是回家。\n\n"
        "（开篇章节完）"
    ),
)
_CHARACTER_INTRO = (
    "现在让我继续完善人物介绍和背景设定。\n\n"
    "**陈远（35岁）**：前互联网公司技术主管，理性、敏感，因轻度抑郁辞职，"
    "试图在旅途中重新定义\"成功\"与\"幸福\"。\n\n"
    "**苏菲（22岁）**：法学专业休学旅行，聪明而理想主义，在迷茫中寻找独立与热爱。\n\n"
    "**林晓（28岁）**：旅行作家兼摄影师，厌倦\"旁观者\"身份，希望从记录者变成参与者。\n\n"
    "**老张（60岁）**：退休货车司机，为完成对妻子的承诺而每年进藏，将在后续章节登场。\n"
)
_NOVEL_FINAL_MESSAGE = (
    f"完成！我已经为你创作了小说《旅行的意义》的完整开篇部分，"
    f"并保存为 `{_NOVEL_FILENAME}` 发送给你。\n\n"
    "开篇章节约6000字，建立了主要人物、场景氛围与核心主题，"
    "并设置了陈远、苏菲、林晓各自的悬念。如需继续创作后续章节，请告诉我。"
)


@dataclass
class _RequestStats:
    active: int = 0
    total: int = 0
    stream_total: int = 0
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def begin(self, *, stream: bool) -> None:
        async with self.lock:
            self.active += 1
            self.total += 1
            if stream:
                self.stream_total += 1

    async def end(self) -> None:
        async with self.lock:
            self.active = max(0, self.active - 1)

    async def snapshot(self) -> tuple[int, int, int]:
        async with self.lock:
            return self.active, self.total, self.stream_total


async def _read_until(reader: asyncio.StreamReader, marker: bytes, *, limit: int = 1024 * 1024) -> bytes:
    buf = bytearray()
    while marker not in buf:
        chunk = await reader.read(4096)
        if not chunk:
            break
        buf.extend(chunk)
        if len(buf) > limit:
            raise ValueError("HTTP header too large")
    return bytes(buf)


async def _read_chunked_body(reader: asyncio.StreamReader) -> bytes:
    body = bytearray()
    while True:
        size_line = await reader.readline()
        if not size_line:
            break
        size_text = size_line.decode("ascii", errors="replace").strip().split(";", 1)[0]
        if not size_text:
            continue
        size = int(size_text, 16)
        if size == 0:
            await reader.readline()
            break
        body.extend(await reader.readexactly(size))
        await reader.readline()
    return bytes(body)


async def _read_http_request(
    reader: asyncio.StreamReader,
) -> tuple[str, str, dict[str, str], dict[str, Any]]:
    """Read a full HTTP/1.1 request (supports Content-Length and chunked body)."""
    header_blob = await _read_until(reader, b"\r\n\r\n")
    header_text, _, rest = header_blob.partition(b"\r\n\r\n")
    lines = header_text.decode("utf-8", errors="replace").split("\r\n")
    request_line = lines[0] if lines else ""
    parts = request_line.split(" ")
    method = parts[0] if parts else "GET"
    path = parts[1] if len(parts) > 1 else "/"

    headers: dict[str, str] = {}
    for line in lines[1:]:
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        headers[key.strip().lower()] = value.strip()

    body = bytearray(rest)
    content_length = headers.get("content-length")
    transfer_encoding = headers.get("transfer-encoding", "").lower()
    if content_length:
        need = int(content_length) - len(body)
        while need > 0:
            chunk = await reader.read(need)
            if not chunk:
                break
            body.extend(chunk)
            need -= len(chunk)
    elif "chunked" in transfer_encoding:
        if body:
            temp_reader = asyncio.StreamReader()
            temp_reader.feed_data(bytes(body))
            temp_reader.feed_eof()
            body = bytearray(await _read_chunked_body(temp_reader))
        else:
            body = bytearray(await _read_chunked_body(reader))

    payload: dict[str, Any] = {}
    body_text = bytes(body).decode("utf-8", errors="replace").strip()
    if body_text:
        try:
            parsed = json.loads(body_text)
            if isinstance(parsed, dict):
                payload = parsed
        except json.JSONDecodeError:
            logger.warning("Failed to parse JSON body (len=%s)", len(body_text))

    return method, path, headers, payload


def _wants_stream(headers: dict[str, str], payload: dict[str, Any]) -> bool:
    if payload.get("stream") is True:
        return True
    accept = headers.get("accept", "")
    return "text/event-stream" in accept.lower()


def _http_response(status: int, body: str, *, content_type: str = "application/json") -> bytes:
    encoded = body.encode("utf-8")
    return (
        f"HTTP/1.1 {status}\r\n"
        f"Content-Type: {content_type}\r\n"
        f"Content-Length: {len(encoded)}\r\n"
        "Connection: close\r\n"
        "\r\n"
    ).encode("utf-8") + encoded


def _sse_event(data: dict[str, Any] | str) -> bytes:
    if isinstance(data, str):
        payload = data
    else:
        payload = json.dumps(data, ensure_ascii=False)
    return f"data: {payload}\n\n".encode("utf-8")


def _models_payload() -> str:
    return json.dumps(
        {
            "object": "list",
            "data": [{"id": "mock-model", "object": "model", "owned_by": "mock"}],
        },
        ensure_ascii=False,
    )


def _message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
                continue
            if not isinstance(part, dict):
                continue
            if isinstance(part.get("text"), str):
                parts.append(part["text"])
            elif part.get("type") == "text" and isinstance(part.get("text"), str):
                parts.append(part["text"])
        return " ".join(parts)
    if isinstance(content, dict) and isinstance(content.get("text"), str):
        return content["text"]
    for key in ("query", "input", "text"):
        val = message.get(key)
        if isinstance(val, str):
            return val
    return ""


def _payload_contains_novel_markers(payload: dict[str, Any]) -> bool:
    messages = payload.get("messages")
    if isinstance(messages, list):
        for message in messages:
            if isinstance(message, dict):
                text = _message_text(message)
                if any(marker in text for marker in _LOADTEST_NOVEL_MARKERS):
                    return True
    try:
        blob = json.dumps(payload, ensure_ascii=False)
    except TypeError:
        blob = str(payload)
    return any(marker in blob for marker in _LOADTEST_NOVEL_MARKERS)


def _should_use_novel_scenario(profile: str, payload: dict[str, Any]) -> bool:
    """loadtest 压测 profile 默认走小说多轮场景（不依赖 user 消息格式）。"""
    if profile != "loadtest":
        return False
    messages = payload.get("messages")
    if isinstance(messages, list):
        if any(isinstance(m, dict) and m.get("role") == "tool" for m in messages):
            return True
    if _payload_contains_novel_markers(payload):
        return True
    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        return True
    return True


def _agent_flow_stage(messages: list[Any]) -> int:
    """按 tool 消息数量驱动多工具 Agent 场景。

    0 todo_create → 1 聊天区长文 → 2 bash(权限 ASK) → 3 write_file
    → 4 read_file → 5 todo_modify(权限 ASK) → 6 todo_modify
    → 7 send_file_to_user → 8 收尾
    """
    tool_indices = [
        idx
        for idx, message in enumerate(messages)
        if isinstance(message, dict) and message.get("role") == "tool"
    ]
    tool_count = len(tool_indices)
    if tool_count == 0:
        return 0
    last_tool_idx = tool_indices[-1]
    assistants_after = sum(
        1
        for message in messages[last_tool_idx + 1:]
        if isinstance(message, dict) and message.get("role") == "assistant"
    )
    if tool_count == 1:
        return 1 if assistants_after == 0 else 2
    if tool_count == 2:
        return 3
    if tool_count == 3:
        return 4
    if tool_count == 4:
        return 5
    if tool_count == 5:
        return 6
    if tool_count == 6:
        return 7
    return 8


def _build_travel_opening_text(target_chars: int) -> str:
    parts = ["《旅行的意义》开篇\n\n第一章：雨夜的陌生人\n\n"]
    block_idx = 0
    while len("".join(parts)) < target_chars:
        parts.append(_TRAVEL_SCENE_BLOCKS[block_idx % len(_TRAVEL_SCENE_BLOCKS)])
        parts.append("\n\n")
        block_idx += 1
    text = "".join(parts)
    if len(text) > target_chars:
        text = text[:target_chars]
    return text


def _build_travel_novel_file_text(target_chars: int) -> str:
    header = (
        "《旅行的意义》\n"
        "作者：Mock Agent\n"
        "说明：Enterprise Runtime loadtest 自动生成的开篇章节。\n\n"
    )
    body = _build_travel_opening_text(max(500, target_chars - len(header)))
    text = header + body
    if len(text) > target_chars:
        text = text[:target_chars]
    return text


_TODO_ID_RE = re.compile(r"task_id:\s*(\S+)\s*,\s*content:\s*(.+)", re.MULTILINE)
_FILE_PATH_KV_RE = re.compile(r"""['"]file_path['"]\s*:\s*['"]([^'"]+)['"]""")
_ABS_PATH_RE = re.compile(r"([A-Za-z]:\\[^\s\"']+|/[^\s\"']+" + re.escape(_NOVEL_FILENAME) + r")")


def _tool_message_text(message: dict[str, Any]) -> str:
    content = message.get("content")
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
        return "\n".join(parts)
    return str(content or "")


def _is_absolute_path(path: str) -> bool:
    if path.startswith("/"):
        return True
    return len(path) > 2 and path[1] == ":" and path[0].isalpha()


def _parse_abs_path_from_tool_blob(blob: str) -> str | None:
    if _NOVEL_FILENAME not in blob:
        return None
    for match in _FILE_PATH_KV_RE.finditer(blob):
        path = match.group(1).strip()
        if _NOVEL_FILENAME in path and _is_absolute_path(path):
            return path
    abs_match = _ABS_PATH_RE.search(blob)
    if abs_match:
        return abs_match.group(1)
    return None


def _parse_todo_items(messages: list[Any]) -> list[tuple[str, str]]:
    items: list[tuple[str, str]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        for match in _TODO_ID_RE.finditer(content):
            items.append((match.group(1), match.group(2).strip()))
    return items


def _assistant_tool_call_args(messages: list[Any], tool_name: str) -> list[dict[str, Any]]:
    found: list[dict[str, Any]] = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        tool_calls = message.get("tool_calls")
        if not isinstance(tool_calls, list):
            continue
        for call in tool_calls:
            if not isinstance(call, dict):
                continue
            fn = call.get("function")
            if not isinstance(fn, dict) or fn.get("name") != tool_name:
                continue
            raw_args = fn.get("arguments") or "{}"
            try:
                args = json.loads(raw_args) if isinstance(raw_args, str) else dict(raw_args)
            except json.JSONDecodeError:
                continue
            if isinstance(args, dict):
                found.append(args)
    return found


def _is_valid_write_file_path(path: Any) -> bool:
    if not isinstance(path, str) or not path:
        return False
    if _FORBIDDEN_WRITE_PATH in path:
        return False
    return _is_absolute_path(path)


def _resolve_novel_file_path(messages: list[Any]) -> str:
    """从 write_file / read_file 的 tool 结果中提取绝对路径，供 send_file_to_user 使用。"""
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        path = _parse_abs_path_from_tool_blob(_tool_message_text(message))
        if path:
            return path
    for args in reversed(_assistant_tool_call_args(messages, "write_file")):
        path = args.get("file_path") or args.get("path")
        if _is_valid_write_file_path(path):
            return path
    for args in reversed(_assistant_tool_call_args(messages, "read_file")):
        path = args.get("file_path") or args.get("path")
        if isinstance(path, str) and path and _is_absolute_path(path):
            return path
    return _NOVEL_FILENAME


def _todo_modify_complete_args(messages: list[Any], task_index: int) -> dict[str, Any]:
    todos = _parse_todo_items(messages)
    if task_index >= len(todos):
        task_index = max(0, len(todos) - 1)
    if not todos:
        return {
            "action": "update",
            "todos": [
                {
                    "id": "mock-todo-1",
                    "content": "《旅行的意义》开篇",
                    "activeForm": "完成《旅行的意义》开篇",
                    "status": "completed",
                }
            ],
        }
    todo_id, content = todos[task_index]
    return {
        "action": "update",
        "todos": [
            {
                "id": todo_id,
                "content": content,
                "activeForm": f"完成{content}",
                "status": "completed",
            }
        ],
    }


@dataclass
class _AgentPlan:
    kind: str
    text: str | None = None
    tool_name: str | None = None
    tool_args: dict[str, Any] | None = None


def _plan_agent_flow_response(
    payload: dict[str, Any],
    *,
    novel_chars: int,
    excerpt_chars: int,
) -> _AgentPlan:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        messages = []
    stage = _agent_flow_stage(messages)
    opening = _build_travel_opening_text(min(excerpt_chars, novel_chars))
    file_body = _build_travel_novel_file_text(min(novel_chars, LOADTEST_WRITE_MAX_CHARS))

    if stage == 0:
        return _AgentPlan(
            kind="intro_and_tool_call",
            text=_INTRO_PLAN,
            tool_name="todo_create",
            tool_args={"tasks": _TODO_TASKS},
        )
    if stage == 1:
        return _AgentPlan(kind="stream_text", text=opening)
    if stage == 2:
        return _AgentPlan(
            kind="intro_and_tool_call",
            text=_INTRO_PERMISSION_BASH,
            tool_name="bash",
            tool_args={"command": _PERMISSION_PROBE_BASH},
        )
    if stage == 3:
        return _AgentPlan(
            kind="intro_and_tool_call",
            text="权限已确认。现在把开篇写入工作区文件：\n",
            tool_name="write_file",
            tool_args={"file_path": _NOVEL_FILENAME, "content": file_body},
        )
    if stage == 4:
        path = _resolve_novel_file_path(messages)
        return _AgentPlan(
            kind="intro_and_tool_call",
            text="让我检查一下当前文件的内容：\n",
            tool_name="read_file",
            tool_args={"file_path": path},
        )
    if stage == 5:
        return _AgentPlan(
            kind="intro_and_tool_call",
            text=_INTRO_PERMISSION_TODO_MODIFY,
            tool_name="todo_modify",
            tool_args=_todo_modify_complete_args(messages, 0),
        )
    if stage == 6:
        return _AgentPlan(
            kind="intro_and_tool_call",
            text="权限已确认。现在继续更新任务状态并完善人物介绍：\n" + _CHARACTER_INTRO,
            tool_name="todo_modify",
            tool_args=_todo_modify_complete_args(messages, 1),
        )
    if stage == 7:
        path = _resolve_novel_file_path(messages)
        return _AgentPlan(
            kind="intro_and_tool_call",
            text="现在让我完成最后一个任务，将文件发送给你：\n",
            tool_name="send_file_to_user",
            tool_args={"abs_file_path_list": [path]},
        )
    return _AgentPlan(kind="stream_text", text=_NOVEL_FINAL_MESSAGE)


def _plan_novel_response(
    payload: dict[str, Any],
    *,
    novel_chars: int,
    excerpt_chars: int,
) -> _AgentPlan:
    return _plan_agent_flow_response(
        payload,
        novel_chars=novel_chars,
        excerpt_chars=excerpt_chars,
    )


async def _write_sse_headers(writer: asyncio.StreamWriter) -> None:
    headers = (
        "HTTP/1.1 200 OK\r\n"
        "Content-Type: text/event-stream\r\n"
        "Cache-Control: no-cache\r\n"
        "Connection: close\r\n"
        "\r\n"
    )
    writer.write(headers.encode("utf-8"))
    await writer.drain()


async def _write_sse_finish(
    writer: asyncio.StreamWriter,
    model: str,
    *,
    finish_reason: str = "stop",
) -> None:
    final_chunk = {
        "id": "mock-chatcmpl-stream",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {},
                "finish_reason": finish_reason,
            }
        ],
    }
    writer.write(_sse_event(final_chunk))
    writer.write(_sse_event("[DONE]"))
    await writer.drain()


async def _stream_text_content(
    writer: asyncio.StreamWriter,
    model: str,
    text: str,
    *,
    chunk_chars: int,
    token_interval_s: float,
    log_label: str = "content",
) -> None:
    await _write_sse_headers(writer)
    total_chunks = max(1, (len(text) + chunk_chars - 1) // chunk_chars)
    for offset in range(0, len(text), chunk_chars):
        piece = text[offset:offset + chunk_chars]
        chunk = {
            "id": "mock-chatcmpl-stream",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": piece},
                    "finish_reason": None,
                }
            ],
        }
        writer.write(_sse_event(chunk))
        await writer.drain()
        chunk_no = offset // chunk_chars + 1
        if chunk_no == 1 or chunk_no == total_chunks or chunk_no % 40 == 0:
            logger.info(
                "Streamed %s chunk %d/%d (%d chars, total=%d)",
                log_label,
                chunk_no,
                total_chunks,
                len(piece),
                len(text),
            )
        if chunk_no < total_chunks and token_interval_s > 0:
            await asyncio.sleep(token_interval_s)
    await _write_sse_finish(writer, model, finish_reason="stop")


async def _emit_tool_call_sse(
    writer: asyncio.StreamWriter,
    model: str,
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    token_interval_s: float,
) -> str:
    call_id = f"call_mock_{secrets.token_hex(12)}"
    tool_args_json = json.dumps(tool_args, ensure_ascii=False)

    first = {
        "id": "mock-chatcmpl-stream",
        "object": "chat.completion.chunk",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "delta": {
                    "tool_calls": [
                        {
                            "index": 0,
                            "id": call_id,
                            "type": "function",
                            "function": {"name": tool_name, "arguments": ""},
                        }
                    ]
                },
                "finish_reason": None,
            }
        ],
    }
    writer.write(_sse_event(first))
    await writer.drain()

    arg_step = 96
    for offset in range(0, len(tool_args_json), arg_step):
        piece = tool_args_json[offset:offset + arg_step]
        chunk = {
            "id": "mock-chatcmpl-stream",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {
                        "tool_calls": [
                            {
                                "index": 0,
                                "function": {"arguments": piece},
                            }
                        ]
                    },
                    "finish_reason": None,
                }
            ],
        }
        writer.write(_sse_event(chunk))
        await writer.drain()
        if token_interval_s > 0:
            await asyncio.sleep(token_interval_s)

    logger.info(
        "Streamed tool_call name=%s args_len=%d call_id=%s",
        tool_name,
        len(tool_args_json),
        call_id,
    )
    return call_id


async def _stream_tool_call(
    writer: asyncio.StreamWriter,
    model: str,
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    token_interval_s: float,
) -> None:
    await _write_sse_headers(writer)
    await _emit_tool_call_sse(
        writer,
        model,
        tool_name,
        tool_args,
        token_interval_s=token_interval_s,
    )
    await _write_sse_finish(writer, model, finish_reason="tool_calls")


async def _stream_content_and_tool_call(
    writer: asyncio.StreamWriter,
    model: str,
    intro: str,
    tool_name: str,
    tool_args: dict[str, Any],
    *,
    chunk_chars: int,
    token_interval_s: float,
) -> None:
    await _write_sse_headers(writer)
    total_chunks = max(1, (len(intro) + chunk_chars - 1) // chunk_chars)
    for offset in range(0, len(intro), chunk_chars):
        piece = intro[offset:offset + chunk_chars]
        chunk = {
            "id": "mock-chatcmpl-stream",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": piece},
                    "finish_reason": None,
                }
            ],
        }
        writer.write(_sse_event(chunk))
        await writer.drain()
        if offset // chunk_chars + 1 < total_chunks and token_interval_s > 0:
            await asyncio.sleep(token_interval_s)
    await _emit_tool_call_sse(
        writer,
        model,
        tool_name,
        tool_args,
        token_interval_s=token_interval_s,
    )
    await _write_sse_finish(writer, model, finish_reason="tool_calls")


async def _stream_chat_completion(
    writer: asyncio.StreamWriter,
    model: str,
    *,
    token_count: int,
    token_interval_s: float,
    text: str | None = None,
    chunk_chars: int = LOADTEST_STREAM_CHUNK_CHARS,
) -> None:
    if text is not None:
        await _stream_text_content(
            writer,
            model,
            text,
            chunk_chars=max(32, chunk_chars),
            token_interval_s=token_interval_s,
            log_label="novel",
        )
        return

    await _write_sse_headers(writer)
    for i in range(1, token_count + 1):
        token = f"mock token{i}"
        chunk = {
            "id": "mock-chatcmpl-stream",
            "object": "chat.completion.chunk",
            "created": int(time.time()),
            "model": model,
            "choices": [
                {
                    "index": 0,
                    "delta": {"content": token},
                    "finish_reason": None,
                }
            ],
        }
        writer.write(_sse_event(chunk))
        await writer.drain()
        logger.info("Streamed token: %s", token)
        if i < token_count and token_interval_s > 0:
            await asyncio.sleep(token_interval_s)
    await _write_sse_finish(writer, model, finish_reason="stop")


def _non_stream_completion(
    model: str,
    *,
    content: str | None = None,
    tool_call: dict[str, Any] | None = None,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    finish_reason = "stop"
    if tool_call is not None:
        message["tool_calls"] = [tool_call]
        message["content"] = content
        finish_reason = "tool_calls"
    return {
        "id": "mock-chatcmpl-123",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [
            {
                "index": 0,
                "message": message,
                "finish_reason": finish_reason,
            }
        ],
        "usage": {"prompt_tokens": 10, "completion_tokens": max(20, len(content or "") // 4), "total_tokens": 30},
    }


async def _handle_request(
    reader: asyncio.StreamReader,
    writer: asyncio.StreamWriter,
    *,
    token_count: int,
    token_interval_s: float,
    stats: _RequestStats,
    profile: str,
    novel_chars: int,
    chunk_chars: int,
    excerpt_chars: int,
) -> None:
    stream = False
    try:
        method, path, headers, payload = await _read_http_request(reader)
        stream = _wants_stream(headers, payload)
        await stats.begin(stream=stream)
        logger.info(
            "Request: %s %s stream=%s body_bytes=%s accept=%s",
            method,
            path,
            payload.get("stream"),
            headers.get("content-length", "?"),
            headers.get("accept", ""),
        )

        if method == "GET" and path == "/health":
            body = json.dumps({"status": "ok"})
            writer.write(_http_response(200, body))
            await writer.drain()
            return

        if method == "GET" and path.rstrip("/") == "/v1/models":
            writer.write(_http_response(200, _models_payload()))
            await writer.drain()
            return

        if method == "POST" and path.rstrip("/") == "/v1/chat/completions":
            model = str(payload.get("model") or "mock-model")
            use_novel = _should_use_novel_scenario(profile, payload)
            if use_novel:
                plan = _plan_novel_response(
                    payload,
                    novel_chars=novel_chars,
                    excerpt_chars=excerpt_chars,
                )
                logger.info(
                    "Agent loadtest scenario kind=%s stage=%d novel_chars=%d excerpt_chars=%d",
                    plan.kind,
                    _agent_flow_stage(payload.get("messages") or []),
                    novel_chars,
                    excerpt_chars,
                )
                if stream:
                    if (
                        plan.kind == "intro_and_tool_call"
                        and plan.tool_name
                        and plan.tool_args is not None
                    ):
                        await _stream_content_and_tool_call(
                            writer,
                            model,
                            plan.text or "",
                            plan.tool_name,
                            plan.tool_args,
                            chunk_chars=chunk_chars,
                            token_interval_s=token_interval_s,
                        )
                    elif plan.kind == "tool_call" and plan.tool_name and plan.tool_args is not None:
                        await _stream_tool_call(
                            writer,
                            model,
                            plan.tool_name,
                            plan.tool_args,
                            token_interval_s=token_interval_s,
                        )
                    else:
                        await _stream_chat_completion(
                            writer,
                            model,
                            token_count=token_count,
                            token_interval_s=token_interval_s,
                            text=plan.text or _NOVEL_FINAL_MESSAGE,
                            chunk_chars=chunk_chars,
                        )
                    return

                if (
                    plan.kind in {"tool_call", "intro_and_tool_call"}
                    and plan.tool_name
                    and plan.tool_args is not None
                ):
                    tool_call = {
                        "id": f"call_mock_{secrets.token_hex(12)}",
                        "type": "function",
                        "function": {
                            "name": plan.tool_name,
                            "arguments": json.dumps(plan.tool_args, ensure_ascii=False),
                        },
                    }
                    response = _non_stream_completion(model, content=plan.text, tool_call=tool_call)
                else:
                    response = _non_stream_completion(model, content=plan.text or _NOVEL_FINAL_MESSAGE)
                body = json.dumps(response, ensure_ascii=False)
                writer.write(_http_response(200, body))
                await writer.drain()
                return

            if stream:
                logger.info(
                    "Generic mock tokens (profile=%s, use --profile loadtest for novel scenario)",
                    profile,
                )
                await _stream_chat_completion(
                    writer,
                    model,
                    token_count=token_count,
                    token_interval_s=token_interval_s,
                )
                return

            content = " ".join(f"mock token{i}" for i in range(1, token_count + 1))
            logger.info("Non-stream response content: %s", content[:120])
            response = _non_stream_completion(model, content=content)
            body = json.dumps(response, ensure_ascii=False)
            writer.write(_http_response(200, body))
            await writer.drain()
            return

        writer.write(_http_response(404, json.dumps({"error": "not found"})))
        await writer.drain()
    except Exception as exc:
        logger.exception("Error handling request: %s", exc)
        writer.write(_http_response(500, json.dumps({"error": str(exc)})))
        await writer.drain()
    finally:
        await stats.end()
        writer.close()
        await writer.wait_closed()


async def _stats_loop(stats: _RequestStats, interval_s: float) -> None:
    while True:
        await asyncio.sleep(interval_s)
        active, total, stream_total = await stats.snapshot()
        logger.info(
            "[stats] active=%d total=%d stream_total=%d",
            active,
            total,
            stream_total,
        )


def _configure_logging() -> None:
    class _TimestampFormatter(logging.Formatter):
        def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
            from datetime import datetime

            dt = datetime.fromtimestamp(record.created)
            base = dt.strftime(datefmt or "%Y-%m-%d %H:%M:%S")
            return f"{base}.{int(record.msecs):03d}"

    root = logging.getLogger()
    root.handlers.clear()
    root.setLevel(logging.INFO)
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(_TimestampFormatter("%(asctime)s %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    root.addHandler(handler)


def _resolve_stream_params(args: argparse.Namespace) -> tuple[int, float]:
    token_count = args.stream_token_count
    token_interval = args.stream_token_interval
    if args.profile == "loadtest":
        if "--stream-token-count" not in sys.argv:
            token_count = LOADTEST_STREAM_TOKEN_COUNT
        if "--stream-token-interval" not in sys.argv:
            token_interval = LOADTEST_STREAM_TOKEN_INTERVAL_S
    return max(1, token_count), max(0.0, token_interval)


async def main(
    host: str,
    port: int,
    *,
    token_count: int,
    token_interval_s: float,
    stats_interval_s: float,
    profile: str,
    novel_chars: int,
    chunk_chars: int,
    excerpt_chars: int,
) -> None:
    stats = _RequestStats()
    stats_task: asyncio.Task[None] | None = None
    if stats_interval_s > 0:
        stats_task = asyncio.create_task(_stats_loop(stats, stats_interval_s))

    async def _handler(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        await _handle_request(
            reader,
            writer,
            token_count=token_count,
            token_interval_s=token_interval_s,
            stats=stats,
            profile=profile,
            novel_chars=novel_chars,
            chunk_chars=chunk_chars,
            excerpt_chars=excerpt_chars,
        )

    server = await asyncio.start_server(_handler, host, port)
    addr = server.sockets[0].getsockname()
    logger.info(
        "Mock LLM server listening on http://%s:%d (profile=%s tokens=%d interval=%ss novel_chars=%d chunk_chars=%d)",
        addr[0],
        addr[1],
        profile,
        token_count,
        token_interval_s,
        novel_chars,
        chunk_chars,
    )
    if profile == "loadtest":
        logger.info(
            "loadtest profile active: agent-flow scenario "
            "(todo_create -> stream opening -> bash(permission ASK) -> write_file -> "
            "read_file -> todo_modify(permission ASK) -> todo_modify -> "
            "send_file_to_user -> finalize)"
        )
    logger.info("Health: http://%s:%d/health", addr[0], addr[1])

    stop_event = asyncio.Event()

    def _request_stop(*_args: object) -> None:
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (getattr(signal, "SIGINT", None), getattr(signal, "SIGTERM", None)):
        if sig is None:
            continue
        try:
            loop.add_signal_handler(sig, _request_stop)
        except NotImplementedError:
            signal.signal(sig, lambda *_a: _request_stop())

    try:
        async with server:
            serve_task = asyncio.create_task(server.serve_forever())
            await stop_event.wait()
            server.close()
            await server.wait_closed()
            serve_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await serve_task
    finally:
        if stats_task is not None:
            stats_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await stats_task
        active, total, stream_total = await stats.snapshot()
        logger.info(
            "[shutdown] active=%d total=%d stream_total=%d",
            active,
            total,
            stream_total,
        )


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="OpenAI 兼容 Mock LLM（Enterprise Runtime 压测 / E2E）",
    )
    parser.add_argument("--host", default="127.0.0.1", help="监听地址，压测对外服务可用 0.0.0.0")
    parser.add_argument("--port", type=int, default=19999, help="HTTP 端口")
    parser.add_argument(
        "--profile",
        choices=("e2e", "loadtest"),
        default="e2e",
        help="e2e: mock token；loadtest: 模拟 Agent 多工具小说创作（todo/write_file/read_file/send_file）",
    )
    parser.add_argument(
        "--novel-chars",
        type=int,
        default=LOADTEST_NOVEL_CHARS,
        help="loadtest 小说场景正文总字数（默认 32000，模拟长文而非真实十万字）",
    )
    parser.add_argument(
        "--stream-chunk-chars",
        type=int,
        default=LOADTEST_STREAM_CHUNK_CHARS,
        help="流式 SSE 每次输出的字符数（loadtest 小说场景）",
    )
    parser.add_argument(
        "--chat-excerpt-chars",
        type=int,
        default=LOADTEST_CHAT_EXCERPT_CHARS,
        help="loadtest 第 2 轮在聊天区展示的开篇章节字数（默认 6000）",
    )
    parser.add_argument(
        "--stream-token-count",
        type=int,
        default=DEFAULT_STREAM_TOKEN_COUNT,
        help="流式 SSE token 数量",
    )
    parser.add_argument(
        "--stream-token-interval",
        type=float,
        default=DEFAULT_STREAM_TOKEN_INTERVAL_S,
        help="流式 SSE 相邻 token 间隔（秒）",
    )
    parser.add_argument(
        "--stats-interval",
        type=float,
        default=30.0,
        help="周期性打印 [stats] 的间隔（秒）；0 表示关闭",
    )
    return parser.parse_args()


if __name__ == "__main__":
    _configure_logging()
    cli_args = _parse_args()
    count, interval = _resolve_stream_params(cli_args)
    try:
        asyncio.run(
            main(
                cli_args.host,
                cli_args.port,
                token_count=count,
                token_interval_s=interval,
                stats_interval_s=max(0.0, cli_args.stats_interval),
                profile=cli_args.profile,
                novel_chars=max(1000, cli_args.novel_chars),
                chunk_chars=max(32, cli_args.stream_chunk_chars),
                excerpt_chars=max(200, cli_args.chat_excerpt_chars),
            )
        )
    except KeyboardInterrupt:
        pass
