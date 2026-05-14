# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AskUserQuestion tool: push structured questions to Gateway and await user answers."""

from __future__ import annotations

import asyncio
import json
import logging
import os
import time
import uuid
from typing import Any

from openjiuwen.core.foundation.tool import LocalFunction, Tool, ToolCard

from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer
from jiuwenclaw.agentserver.deep_agent.ask_user_question_registry import (
    ASK_REQUEST_PREFIX,
    AskUserQuestionRegistry,
    get_ask_request_context,
)

logger = logging.getLogger(__name__)

_ASK_TOOL_TIMEOUT = float(os.environ.get("ASK_USER_QUESTION_TIMEOUT", "300"))
_ASK_TOOL_MAX_QUESTIONS = 4
_ASK_TOOL_MIN_OPTIONS = 2
_ASK_TOOL_MAX_OPTIONS = 4

_OTHER_FALLBACK_LABEL = "其他"


def _coerce_raw_options(opts: Any, question_index: int) -> list[dict[str, Any]]:
    """Ensure 2–4 labeled options; trim excess, pad singles, tolerate str entries."""
    if not isinstance(opts, list) or not opts:
        raise ValueError(f"questions[{question_index}].options 必须为非空数组")
    normalized: list[dict[str, Any]] = []
    for j, opt in enumerate(opts):
        if isinstance(opt, str):
            entry = {"label": opt.strip()}
        elif isinstance(opt, dict):
            entry = dict(opt)
        else:
            raise ValueError(f"questions[{question_index}].options[{j}] 必须为对象或字符串")
        label = str(entry.get("label", "") or "").strip()
        if not label:
            raise ValueError(f"questions[{question_index}].options[{j}].label 不能为空")
        normalized.append(entry)

    if len(normalized) > _ASK_TOOL_MAX_OPTIONS:
        logger.info(
            "[ask_user_question] questions[%s].options 共 %s 项，裁剪为 %s 项",
            question_index,
            len(normalized),
            _ASK_TOOL_MAX_OPTIONS,
        )
        normalized = normalized[: _ASK_TOOL_MAX_OPTIONS]

    if len(normalized) == 1:
        logger.info(
            "[ask_user_question] questions[%s].options 仅 1 项，补充「%s」选项以便交互选择",
            question_index,
            _OTHER_FALLBACK_LABEL,
        )
        normalized.append(
            {"label": _OTHER_FALLBACK_LABEL, "description": "请在下一句补充说明你的选择"},
        )

    if len(normalized) < _ASK_TOOL_MIN_OPTIONS:
        raise ValueError(f"questions[{question_index}].options 至少需要 {_ASK_TOOL_MIN_OPTIONS} 个有效选项")

    return normalized


def _normalize_questions(raw: Any) -> list[dict[str, Any]]:
    if isinstance(raw, str):
        raw_st = raw.strip()
        if not raw_st:
            raise ValueError("questions 不能为空")
        parsed: Any = json.loads(raw_st)
    else:
        parsed = raw
    if not isinstance(parsed, list) or not parsed:
        raise ValueError("questions 必须为非空 JSON 数组")
    if len(parsed) > _ASK_TOOL_MAX_QUESTIONS:
        raise ValueError(f"questions 最多 {_ASK_TOOL_MAX_QUESTIONS} 项")
    out: list[dict[str, Any]] = []
    for i, item in enumerate(parsed):
        if not isinstance(item, dict):
            raise ValueError(f"questions[{i}] 必须为对象")
        qtext = str(item.get("question", "") or "").strip()
        if not qtext:
            raise ValueError(f"questions[{i}].question 不能为空")
        raw_opts = item.get("options", item.get("Options", []))
        coerced = _coerce_raw_options(raw_opts, i)
        norm_opts: list[dict[str, str]] = []
        for opt in coerced:
            label = str(opt.get("label", "") or "").strip()
            entry: dict[str, str] = {"label": label}
            desc = opt.get("description")
            if isinstance(desc, str) and desc.strip():
                entry["description"] = desc.strip()
            norm_opts.append(entry)
        entry_q: dict[str, Any] = {
            "question": qtext,
            "options": norm_opts,
            "multi_select": bool(item.get("multi_select", item.get("multiSelect", False))),
        }
        header = item.get("header")
        if isinstance(header, str) and header.strip():
            entry_q["header"] = header.strip()
        out.append(entry_q)
    return out


def _format_answers_for_model(answers: list[Any]) -> str:
    try:
        return json.dumps(answers, ensure_ascii=False, indent=2)
    except Exception:
        return str(answers)


def _format_questions_as_text(questions: list[dict[str, Any]]) -> str:
    lines = ["## 需要您的确认", ""]
    for i, q in enumerate(questions):
        header = str(q.get("header", "") or "").strip() or f"问题 {i + 1}"
        question_text = str(q.get("question", "") or "").strip()
        options = q.get("options", [])
        lines.append(f"**{header}**")
        lines.append(question_text)
        for j, opt in enumerate(options):
            label = str(opt.get("label", "") or "").strip()
            desc = str(opt.get("description", "") or "").strip()
            if desc:
                lines.append(f"{j + 1}. **{label}** — {desc}")
            else:
                lines.append(f"{j + 1}. **{label}**")
        lines.append("")
    lines.append("请回复您的选择（如直接回复选项名称或描述您的需求）。")
    return "\n".join(lines)


def _parse_timeout(timeout: Any) -> float:
    if timeout is None:
        return _ASK_TOOL_TIMEOUT
    try:
        parsed = float(timeout)
    except (TypeError, ValueError) as exc:
        raise ValueError("timeout 必须是数字（秒）") from exc
    if parsed <= 0:
        raise ValueError("timeout 必须大于 0")
    return parsed


async def _ask_user_question_impl(questions: Any, timeout: Any = None) -> dict[str, Any]:
    interactive, session_id, stream_rid, channel_id = get_ask_request_context()
    reg = AskUserQuestionRegistry.get_instance()
    # 仅按本次流的 stream_request_id 回填（修复 ContextVar 在异步工具内丢失），不按 session 继承以免sticky。
    if stream_rid and not interactive:
        interactive = reg.stream_interactive_ask_enabled(stream_rid)
    try:
        normalized = _normalize_questions(questions)
        timeout_sec = _parse_timeout(timeout)
    except Exception as exc:
        return {"status": "error", "message": f"参数错误: {exc}", "answers": []}

    if not stream_rid:
        return {"status": "error", "message": "内部错误：缺少 stream_request_id，无法发送追问。", "answers": []}

    if not interactive:
        # 非引导：不发 chat.ask_user_question（避免前端弹选择框），也不发 chat.delta（前端已从工具接中展示），不阻塞工具；
        # 用户在下一条普通消息中回复即可。
        formatted = _format_questions_as_text(normalized)
        return {
            "status": "text_only",
            "message": "当前未开启引导式交互；问题已由工具结果展示。请结束本步、待用户下一条消息中直接回复。",
            "formatted_questions": formatted,
            "answers": [],
        }

    ask_id = f"{ASK_REQUEST_PREFIX}{uuid.uuid4().hex}"
    # 与 wait_for_answer(timeout) 使用同一截止时刻；网关/前端用 expires_at_ms 展示倒计时或「请于 xx 前完成」
    expires_at_ms = int(time.time() * 1000 + float(timeout_sec) * 1000)
    payload: dict[str, Any] = {
        "event_type": "chat.ask_user_question",
        "request_id": ask_id,
        "source": "ask_tool",
        "questions": normalized,
        "expires_at_ms": expires_at_ms,
        "timeout_sec": float(timeout_sec),
    }
    if session_id:
        payload["session_id"] = session_id

    msg = {
        "request_id": stream_rid,
        "channel_id": channel_id,
        "payload": payload,
        "is_complete": False,
    }
    try:
        server = AgentWebSocketServer.get_instance()
        await server.send_push(msg)
    except Exception as exc:
        logger.exception("[ask_user_question] send_push 失败: %s", exc)
        return {"status": "error", "message": f"无法将追问事件推送到 Gateway: {exc}", "answers": []}

    registry = AskUserQuestionRegistry.get_instance()
    try:
        answers = await registry.wait_for_answer(ask_id, timeout=timeout_sec)
    except asyncio.TimeoutError:
        return {
            "status": "timeout",
            "message": f"用户在 {int(timeout_sec)} 秒内未回答，请根据上下文自行判断或再次提问。",
            "answers": [],
        }
    except asyncio.CancelledError:
        return {"status": "cancelled", "message": "会话已取消。", "answers": []}

    return {
        "status": "answered",
        "answers": answers,
        "summary": f"用户已作答（JSON）:\n{_format_answers_for_model(answers)}",
    }


_ASK_TOOL_CARD = ToolCard(
    id="jiuwenclaw_ask_user_question",
    name="ask_user_question",
    description=(
        "向用户展示一组带选项的结构化问题（可多题），并阻塞等待用户选择。"
        "仅当引导/交互已开启时推送选择 UI；未开启时不推送 chat.delta（由对话流展示），" 
        "由用户在下一条消息中自由回复。questions 为 JSON 数组，每项含 question、"
        "options[{label, description?}]、可选 header、multi_select。"
    ),
    input_params={
        "type": "object",
        "properties": {
            "questions": {
                "type": ["array", "string"],
                # OpenAI 等校验器要求 array 必须带 items；对 string 实例 items 不适用。
                "items": {"type": "object"},
                "description": "问题列表：JSON 数组，或 JSON 数组的字符串形式",
            },
            "timeout": {
                "type": ["number", "integer", "string"],
                "description": "等待用户回答超时秒数（默认 300）",
            },
        },
        "required": ["questions"],
    },
)


def get_ask_user_question_tool() -> Tool:
    return LocalFunction(card=_ASK_TOOL_CARD, func=_ask_user_question_impl)
