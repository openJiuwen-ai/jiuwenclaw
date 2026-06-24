# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""AskUserQuestion tool: push structured questions to Gateway and await user answers."""

from __future__ import annotations

import asyncio
import json
import logging
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

_ASK_TOOL_MAX_QUESTIONS = 4
_ASK_TOOL_MIN_OPTIONS = 2
_ASK_TOOL_MAX_OPTIONS = 4

_OTHER_FALLBACK_LABEL = "其他"

OUTLINE_CONFIRM_ID = "outline_confirm"
OUTLINE_USE_EDITED_ID = "outline_use_edited"


def _normalize_preview(preview: Any) -> dict[str, Any] | None:
    """Normalize preview field for PPT outline review (markdown body + metadata)."""
    if preview is None:
        return None
    if isinstance(preview, str):
        preview_st = preview.strip()
        if not preview_st:
            return None
        return {
            "title": "",
            "text": preview_st,
            "format": "markdown",
            "editable": True,
        }
    if isinstance(preview, dict):
        text = str(preview.get("text", "") or "").strip()
        if not text:
            raise ValueError("preview.text 不能为空")
        meta = preview.get("meta")
        outline_ref = preview.get("outline_ref")
        out: dict[str, Any] = {
            "title": str(preview.get("title", "") or "").strip(),
            "text": text,
            "format": str(preview.get("format", "markdown") or "markdown"),
            "editable": bool(preview.get("editable", True)),
        }
        if isinstance(outline_ref, str) and outline_ref.strip():
            out["outline_ref"] = outline_ref.strip()
        if isinstance(meta, dict):
            out["meta"] = meta
        return out
    raise ValueError("preview 必须为字符串或对象")


def _normalize_user_answer_option_ids(answers: list[Any]) -> list[Any]:
    """Map legacy Chinese labels to stable ids for outline review options."""
    out: list[Any] = []
    for item in answers:
        if not isinstance(item, dict):
            out.append(item)
            continue
        sel = item.get("selected_options")
        if not isinstance(sel, list) or not sel:
            out.append(item)
            continue
        first = str(sel[0]).strip()
        mapped = first
        if first in ("确认", OUTLINE_CONFIRM_ID):
            mapped = OUTLINE_CONFIRM_ID
        elif first in ("修改", OUTLINE_USE_EDITED_ID):
            mapped = OUTLINE_USE_EDITED_ID
        if mapped == first:
            out.append(item)
            continue
        clone = dict(item)
        clone["selected_options"] = [mapped]
        out.append(clone)
    return out


def _coerce_raw_options(opts: Any, question_index: int, *, has_preview: bool = False) -> list[dict[str, Any]]:
    """Ensure 2–4 labeled options; trim excess, pad singles, tolerate str entries."""
    if has_preview:
        return [
            {"label": "确认", "description": "内容符合预期，继续执行", "id": OUTLINE_CONFIRM_ID},
            {"label": "修改", "description": "使用下方编辑后的正文", "id": OUTLINE_USE_EDITED_ID},
        ]
    if not isinstance(opts, list) or not opts:
        raise ValueError(f"questions[{question_index}].options 必须为非空数组")
    normalized: list[dict[str, Any]] = []
    has_other = False
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
        # free_input=True 时强制将 label 替换为「其他」；下方 elif 分支则处理显式 label 为「其他」的情况。
        # 两者共享 has_other 唯一性约束，确保最终选项中至多出现一个「其他」类型选项。
        if entry.pop("free_input", None):
            if has_other:
                raise ValueError(f"questions[{question_index}].options 中「其他」选项不能超过1个")
            entry["label"] = _OTHER_FALLBACK_LABEL
            has_other = True
            if not entry.get("description"):
                entry["description"] = "请在下一句补充说明你的选择"
        elif label == _OTHER_FALLBACK_LABEL:
            if has_other:
                raise ValueError(f"questions[{question_index}].options 中「其他」选项不能超过1个")
            has_other = True
        normalized.append(entry)

    if len(normalized) > _ASK_TOOL_MAX_OPTIONS:
        logger.info(
            "[ask_user_question] questions[%s].options 共 %s 项，裁剪为 %s 项",
            question_index,
            len(normalized),
            _ASK_TOOL_MAX_OPTIONS,
        )
        normalized = normalized[: _ASK_TOOL_MAX_OPTIONS]

    if len(normalized) == 1 and not has_other:
        logger.info(
            "[ask_user_question] questions[%s].options 仅 1 项，补充「%s」选项以便交互选择",
            question_index,
            _OTHER_FALLBACK_LABEL,
        )
        normalized.append(
            {"label": _OTHER_FALLBACK_LABEL, "description": "请在下一句补充说明你的选择"},
        )

    if len(normalized) < _ASK_TOOL_MIN_OPTIONS and not has_other:
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

        preview_norm: dict[str, Any] | None = None
        try:
            preview_norm = _normalize_preview(item.get("preview"))
        except ValueError as exc:
            raise ValueError(f"questions[{i}].preview: {exc}") from exc
        if preview_norm is None:
            legacy_md = item.get("outline_markdown")
            if isinstance(legacy_md, str) and legacy_md.strip():
                preview_norm = _normalize_preview(legacy_md)

        has_preview = preview_norm is not None
        raw_opts = item.get("options", item.get("Options", []))
        coerced = _coerce_raw_options(raw_opts, i, has_preview=has_preview)
        norm_opts: list[dict[str, str]] = []
        for opt in coerced:
            label = str(opt.get("label", "") or "").strip()
            entry: dict[str, str] = {"label": label}
            desc = opt.get("description")
            if isinstance(desc, str) and desc.strip():
                entry["description"] = desc.strip()
            oid = opt.get("id")
            if isinstance(oid, str) and oid.strip():
                entry["id"] = oid.strip()
            norm_opts.append(entry)

        entry_q: dict[str, Any] = {
            "question": qtext,
            "options": norm_opts,
            "multi_select": bool(item.get("multi_select", item.get("multiSelect", False))),
        }
        header = item.get("header")
        if has_preview and preview_norm is not None:
            title = str(preview_norm.get("title", "") or "").strip()
            if title:
                entry_q["header"] = title
            elif isinstance(header, str) and header.strip():
                entry_q["header"] = header.strip()
            else:
                entry_q["header"] = "大纲审阅"
            entry_q["preview"] = preview_norm
            # 兼容仍读取 outline_markdown 的消费方（如 OfficeClaw 桥接类型）
            entry_q["outline_markdown"] = preview_norm.get("text", "")
        else:
            if isinstance(header, str) and header.strip():
                entry_q["header"] = header.strip()
            else:
                entry_q["header"] = f"问题 {i + 1}"

        # PPT 大纲审批等扩展：透传给网关/前端
        for ext_key in ("interaction_kind", "outline_path", "outline_markdown"):
            if ext_key in entry_q:
                continue
            if ext_key not in item:
                continue
            val = item.get(ext_key)
            if ext_key == "outline_markdown" and isinstance(val, str) and val.strip():
                entry_q[ext_key] = val
            elif ext_key == "outline_path" and isinstance(val, str) and val.strip():
                entry_q[ext_key] = val.strip()
            elif ext_key == "interaction_kind" and isinstance(val, str) and val.strip():
                entry_q[ext_key] = val.strip()
        if has_preview and preview_norm is not None:
            op = item.get("outline_path")
            if isinstance(op, str) and op.strip() and "outline_path" not in entry_q:
                entry_q["outline_path"] = op.strip()
        out.append(entry_q)
    return out


def _format_answers_for_model(answers: list[Any]) -> str:
    try:
        return json.dumps(answers, ensure_ascii=False, indent=2)
    except Exception:
        return str(answers)


def _first_outline_preview(questions: list[dict[str, Any]]) -> dict[str, Any] | None:
    for q in questions:
        preview = q.get("preview")
        if isinstance(preview, dict) and str(preview.get("text") or "").strip():
            return preview
    return None


async def _ask_user_question_impl(questions: Any) -> dict[str, Any]:
    interactive, session_id, stream_rid, channel_id = get_ask_request_context()
    reg = AskUserQuestionRegistry.get_instance()
    if stream_rid and not interactive:
        interactive = reg.stream_interactive_ask_enabled(stream_rid)
    try:
        normalized = _normalize_questions(questions)
    except Exception as exc:
        return {"status": "error", "message": f"参数错误: {exc}", "answers": []}

    if not stream_rid:
        return {"status": "error", "message": "内部错误：缺少 stream_request_id，无法发送追问。", "answers": []}

    # 大纲预览：仍受引导模式控制；关闭时不弹窗，直接跳过大纲确认继续执行。
    if not interactive:
        first_preview = _first_outline_preview(normalized)
        if first_preview is not None:
            return {
                "status": "skipped",
                "message": "未开启引导式交互，跳过大纲确认，使用生成的大纲继续。",
                "answers": [],
                "original_content": str(first_preview.get("text") or "").strip(),
            }

    ask_id = f"{ASK_REQUEST_PREFIX}{uuid.uuid4().hex}"
    payload: dict[str, Any] = {
        "event_type": "chat.ask_user_question",
        "request_id": ask_id,
        "source": "ask_tool",
        "questions": normalized
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
        answers = await registry.wait_for_answer(ask_id)
    except asyncio.CancelledError:
        return {"status": "cancelled", "message": "会话已取消。", "answers": []}

    normalized_answers = _normalize_user_answer_option_ids(answers)
    return {
        "status": "answered",
        "answers": normalized_answers,
        "summary": f"用户已作答（JSON）:\n{_format_answers_for_model(normalized_answers)}",
    }

_ASK_TOOL_CARD = ToolCard(
    id="jiuwenclaw_ask_user_question",
    name="ask_user_question",
    description=(
        "向用户展示一组带选项的结构化问题（可多题），推送选择 UI 并阻塞等待用户作答。"
        "questions 为 JSON 数组，每项含 question、"
        "options[{label, description?, id?, free_input?}]、可选 header、multi_select；free_input=true 的选项 label 自动设为「其他」；"
        "可选 preview{text,title?,format?,editable?,outline_ref?,meta?} "
        "用于 PPT 大纲 Markdown 审阅（将注入 outline_confirm / outline_use_edited 选项）；"
        "仅带 preview 的大纲确认受引导模式约束，未开启引导时返回 skipped 不弹窗。"
    ),
    input_params={
        "type": "object",
        "properties": {
            "questions": {
                "type": ["array", "string"],
                # OpenAI 等校验器要求 array 必须带 items；对 string 实例 items 不适用。
                "items": {"type": "object"},
                "description": "问题列表：JSON 数组，或 JSON 数组的字符串形式",
            }
        },
        "required": ["questions"],
    },
)


def get_ask_user_question_tool() -> Tool:
    return LocalFunction(card=_ASK_TOOL_CARD, func=_ask_user_question_impl)
