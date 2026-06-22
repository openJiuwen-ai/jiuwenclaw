from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.replan_agent.plan_node import PlanNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.ppt_common import PptCommon

_TEXT_SOURCE_KEYS = PptCommon.TEXT_SOURCE_KEYS
_collect_user_text = PptCommon.collect_user_text
_parse_json_payload = PptCommon.parse_json_payload
_DOC_EXCERPT_MAX_CHARS = 4000
_DEFAULT_AUDIENCE = "通用商务/知识分享"
_DEFAULT_PRESENTATION_PURPOSE = "auto"
_DEFAULT_PAGE_COUNT = 6
_MAX_PAGE_COUNT = 30

_VALID_STYLE_IDS = frozenset(
    {"huawei", "light-tech", "paper-humanities", "dark-tech", "free", "custom"}
)
_VALID_SEARCH_MODES = frozenset({"auto", "no_search", "force_search"})
_VALID_SOURCE_TYPES = frozenset({"topic", "outline", "description"})
_VALID_RESEARCH_DEPTHS = frozenset({"L1", "L2", "L3"})

_STYLE_LABEL_TO_ID: dict[str, str] = {
    "华为风格": "huawei",
    "浅色科技风": "light-tech",
    "纸质人文风": "paper-humanities",
    "深色科技风": "dark-tech",
    "自由发挥": "free",
}

_PAGE_LABEL_TO_COUNT: dict[str, int] = {
    "3-6 页（推荐）": 6,
    "8-12 页": 10,
    "15-20 页": 18,
}

_PURPOSE_LABEL_TO_VALUE: dict[str, str] = {
    "工作汇报": "工作汇报",
    "产品/方案展示": "产品展示",
    "教学/分享": "教学分享",
    "AI 自动判断": "auto",
}

_SLOT_FIELDS = ("topic", "page_count", "audience", "presentation_purpose", "style_id")
_ASK_BATCH_FIELDS = ("page_count", "audience", "presentation_purpose")
_P21_GAP_FIELDS = _ASK_BATCH_FIELDS

_P21_SLOT_SYSTEM_PROMPT = ("""你是 PPT 需求槽位分析助手。从用户消息与文档摘要中提取已知信息，并判断仍缺失的字段。

提取字段：
- topic: 演示主题（字符串；未知则 ""）
- page_count: 目标页数（整数；未知则 null）
- audience: 目标受众（字符串；未知则 ""）
- presentation_purpose: 汇报目的，如「工作汇报」「产品展示」「教学分享」「auto」；未知则 ""
- style_id: 用户明确提及风格时填写：huawei / light-tech / paper-humanities / dark-tech / free / custom；未知则 ""
- style_description: style_id 为 custom 时的描述；否则 ""
- missing_fields: 仍缺失且需用户补充的字段名数组，取值限于 topic / page_count / audience / presentation_purpose / style_id
- need_ask_style: 用户未明确风格时为 true，否则 false

规则：
1. 不要编造用户未提及的信息。
2. 页数最多 30；范围取合理中位值。
3. 已知主题（来自上游 P3）时不要修改 topic，且 missing_fields 不得包含 topic。
4. 不要输出 search_mode / source_type。
5. topic 缺失时由下游 LLM 生成 4 个主题候选并 ask 用户选择，不要生成询问文案。

必须只输出 JSON："""
    + '{"topic":"","page_count":null,"audience":"","presentation_purpose":"",'
    + '"style_id":"","style_description":"","missing_fields":[],"need_ask_style":true}')

_TOPIC_SUGGEST_COUNT = 4

_P21_TOPIC_SUGGEST_SYSTEM_PROMPT = f"""你是 PPT 主题策划助手。根据用户消息与文档摘要，生成恰好 {_TOPIC_SUGGEST_COUNT} 个可直接作为演示文稿主题的候选。

要求：
1. 每个主题必须足够具体、完整，单独一条即可支撑一次 PPT 制作（含明确对象、范围或角度），通常 12~40 字。
2. 四个主题应互不重复，覆盖不同切入点（如受众、角度、范围、侧重点）。
3. 不要输出笼统词如「工作总结」「产品介绍」，要落到可执行的演示命题。
4. 不要编造与用户上下文无关的主题。

必须只输出 JSON：
{{"topics":["主题1","主题2","主题3","主题4"]}}"""

_P24_SYSTEM_PROMPT = """你是 PPT 流水线派生参数分析助手。根据已收集的需求与文档情况，推断 search_mode、source_type、research_depth。

search_mode 规则（互斥，按优先级取第一个匹配）：
1. 用户明确要求不搜索、仅按给定材料、局部改稿或样式微调 → no_search
2. 用户要求最新数据、趋势、市场分析、竞品对比等 → force_search
3. 其余情况（含宽泛主题、有/无文档、用户提供大纲等）→ auto

source_type 规则：
- 用户提供了结构化大纲文本（章节/页码列表）→ outline
- 用户提供了完整内容描述（长段落、无清晰大纲结构）→ description
- 宽泛主题、简短描述、或主要依赖上传文档 → topic

research_depth 规则（与 search_mode、page_count 联动；L1/L2/L3 含义见下游 research-writer）：
- search_mode 为 no_search → L1
- search_mode 为 force_search，或 page_count > 15 → L3
- page_count 在 8~15 → L2
- 其余（含 auto 且页数 ≤7）→ L1

必须只输出 JSON，三个字段均必填且取值必须在枚举内：
{"search_mode":"auto","source_type":"topic","research_depth":"L2"}"""


class RequirementCollectError(RuntimeError):
    """P2 需求收集失败。"""


def _normalize_page_count(value: Any) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        count = value
    elif isinstance(value, float):
        count = int(value)
    elif isinstance(value, str):
        stripped = value.strip()
        if not stripped:
            return None
        range_match = re.search(r"(\d+)\s*[-~到]\s*(\d+)", stripped)
        if range_match:
            low, high = int(range_match.group(1)), int(range_match.group(2))
            count = (low + high) // 2
        else:
            digits = re.search(r"\d+", stripped)
            if not digits:
                return None
            count = int(digits.group(0))
    else:
        return None

    if count < 1:
        return None
    return min(count, _MAX_PAGE_COUNT)


def _normalize_style_id(value: Any) -> str:
    if not isinstance(value, str):
        return ""
    raw = value.strip()
    if not raw:
        return ""
    lowered = raw.casefold()
    alias_map = {
        "huawei": "huawei",
        "light-tech": "light-tech",
        "light tech": "light-tech",
        "paper-humanities": "paper-humanities",
        "dark-tech": "dark-tech",
        "free": "free",
        "custom": "custom",
    }
    if lowered in alias_map:
        return alias_map[lowered]
    if raw in _VALID_STYLE_IDS:
        return raw
    return ""


def _style_id_from_label(label: str) -> tuple[str, str]:
    text = label.strip()
    if not text or text == "其他":
        return "", ""
    if text in _STYLE_LABEL_TO_ID:
        return _STYLE_LABEL_TO_ID[text], ""
    normalized = _normalize_style_id(text)
    if normalized:
        return normalized, ""
    return "custom", text


def _page_count_from_label(label: str, other_text: str = "") -> int | None:
    source = other_text.strip() or label.strip()
    if label.strip() in _PAGE_LABEL_TO_COUNT:
        return _PAGE_LABEL_TO_COUNT[label.strip()]
    return _normalize_page_count(source)


def _purpose_from_label(label: str, other_text: str = "") -> str:
    source = label.strip()
    if source in _PURPOSE_LABEL_TO_VALUE:
        return _PURPOSE_LABEL_TO_VALUE[source]
    custom = other_text.strip() or source
    return custom if custom and custom != "其他" else _DEFAULT_PRESENTATION_PURPOSE


def _audience_from_label(label: str, other_text: str = "") -> str:
    custom = other_text.strip()
    if custom and label.strip() in ("其他", _DEFAULT_AUDIENCE):
        return custom
    source = label.strip()
    if not source or source == "其他":
        return _DEFAULT_AUDIENCE
    return source


def _has_nonempty_topic(inputs: dict[str, Any]) -> bool:
    topic = inputs.get("topic")
    return isinstance(topic, str) and bool(topic.strip())


def _apply_slot_defaults(inputs: dict[str, Any]) -> None:
    if not inputs.get("audience"):
        inputs["audience"] = _DEFAULT_AUDIENCE
    if not inputs.get("presentation_purpose"):
        inputs["presentation_purpose"] = _DEFAULT_PRESENTATION_PURPOSE
    if inputs.get("page_count") is None:
        inputs["page_count"] = _DEFAULT_PAGE_COUNT


def _batch_field_is_satisfied(inputs: dict[str, Any], field: str) -> bool:
    if field == "page_count":
        return inputs.get("page_count") is not None
    if field == "audience":
        audience = inputs.get("audience")
        return isinstance(audience, str) and bool(audience.strip())
    if field == "presentation_purpose":
        purpose = inputs.get("presentation_purpose")
        return isinstance(purpose, str) and bool(purpose.strip())
    return False


def _unsatisfied_batch_fields(inputs: dict[str, Any]) -> list[str]:
    return [
        field
        for field in _ASK_BATCH_FIELDS
        if not _batch_field_is_satisfied(inputs, field)
    ]


def _require_batch_fields_collected(inputs: dict[str, Any]) -> None:
    missing = _unsatisfied_batch_fields(inputs)
    if not missing:
        return
    labels = {
        "page_count": "页数",
        "audience": "受众",
        "presentation_purpose": "汇报目的",
    }
    names = "、".join(labels.get(field, field) for field in missing)
    raise RequirementCollectError(f"需求收集未完成：缺少 {names}")


def _prune_satisfied_batch_missing_fields(inputs: dict[str, Any]) -> None:
    inputs["missing_fields"] = [
        field
        for field in (inputs.get("missing_fields") or [])
        if field not in _ASK_BATCH_FIELDS or not _batch_field_is_satisfied(inputs, field)
    ]


def _merge_slot_payload(
    inputs: dict[str, Any],
    payload: dict[str, Any],
    *,
    preserve_topic: bool = False,
) -> None:
    if not preserve_topic:
        topic = payload.get("topic")
        if isinstance(topic, str) and topic.strip():
            inputs["topic"] = topic.strip()

    page_count = _normalize_page_count(payload.get("page_count"))
    if page_count is not None:
        inputs["page_count"] = page_count

    audience = payload.get("audience")
    if isinstance(audience, str) and audience.strip():
        inputs["audience"] = audience.strip()

    purpose = payload.get("presentation_purpose")
    if isinstance(purpose, str) and purpose.strip():
        inputs["presentation_purpose"] = purpose.strip()

    style_id = _normalize_style_id(payload.get("style_id"))
    if style_id:
        inputs["style_id"] = style_id

    style_description = payload.get("style_description")
    if isinstance(style_description, str) and style_description.strip():
        inputs["style_description"] = style_description.strip()

    missing = payload.get("missing_fields")
    if isinstance(missing, list):
        allowed = (
            tuple(field for field in _SLOT_FIELDS if field != "topic")
            if preserve_topic
            else _SLOT_FIELDS
        )
        inputs["missing_fields"] = [
            str(item).strip()
            for item in missing
            if isinstance(item, str) and str(item).strip() in allowed
        ]
    elif not preserve_topic:
        inputs.setdefault("missing_fields", [])

    need_ask_style = payload.get("need_ask_style")
    if isinstance(need_ask_style, bool):
        inputs["need_ask_style"] = need_ask_style
    elif "need_ask_style" not in inputs:
        inputs["need_ask_style"] = not bool(inputs.get("style_id"))


def _build_p21_slot_prompt(
    user_text: str,
    doc_excerpt: str,
    inputs: dict[str, Any],
    *,
    preserve_topic: bool,
) -> str:
    parts = ["请分析 PPT 需求槽位与缺失项。\n"]
    if preserve_topic:
        parts.append(
            f"已知主题（来自上游，勿修改）：{inputs.get('topic', '').strip()}\n"
            "missing_fields 不得包含 topic。\n"
        )
    if user_text:
        parts.append(f"用户消息：\n{user_text}\n")
    if doc_excerpt:
        parts.append(f"文档摘要（doc_raw）：\n{doc_excerpt}\n")
    if inputs.get("has_documents"):
        parts.append(f"has_documents: {bool(inputs.get('has_documents'))}\n")
    parts.append("按 JSON 返回全部槽位、missing_fields、need_ask_style。")
    return "\n".join(parts)


def _parse_slot_analysis_response(raw: str, *, preserve_topic: bool) -> dict[str, Any]:
    payload = _parse_json_payload(raw)
    if not isinstance(payload, dict):
        default_missing = list(_P21_GAP_FIELDS) if preserve_topic else list(_SLOT_FIELDS)
        return {
            "topic": "",
            "page_count": None,
            "audience": "",
            "presentation_purpose": "",
            "style_id": "",
            "style_description": "",
            "missing_fields": default_missing,
            "need_ask_style": True,
        }
    return payload


def _build_p24_prompt(inputs: dict[str, Any], user_text: str, doc_excerpt: str) -> str:
    parts = ["请根据以下已收集需求推断派生参数。\n"]
    parts.append(
        "已收集：\n"
        f"- topic: {inputs.get('topic', '')}\n"
        f"- page_count: {inputs.get('page_count')}\n"
        f"- audience: {inputs.get('audience', '')}\n"
        f"- presentation_purpose: {inputs.get('presentation_purpose', '')}\n"
        f"- style_id: {inputs.get('style_id', '')}\n"
        f"- has_documents: {bool(inputs.get('has_documents'))}\n"
        f"- doc_parse_ok: {bool(inputs.get('doc_parse_ok'))}\n"
    )
    if user_text:
        parts.append(f"用户原文：\n{user_text}\n")
    if doc_excerpt:
        parts.append(f"文档摘要：\n{doc_excerpt}\n")
    parts.append("按 JSON 返回 search_mode、source_type、research_depth。")
    return "\n".join(parts)


async def _ask_missing_batch_fields(node: PlanNode, inputs: dict[str, Any]) -> None:
    missing_fields = _unsatisfied_batch_fields(inputs)
    if not missing_fields:
        return

    questions = _build_batch_questions(missing_fields)
    if not questions:
        raise RequirementCollectError("无法组装页数/受众/目的的询问题目")

    if not node.has_tool("ask_user_question"):
        raise RequirementCollectError("缺少 ask_user_question 工具，无法收集页数/受众/目的")

    result = await node.call_tool("ask_user_question", questions=questions)
    status, answers = _normalize_ask_result(result)
    if status != "answered" or not answers:
        detail = ""
        if isinstance(result, dict):
            detail = str(result.get("message") or "").strip()
        raise RequirementCollectError(
            "批量需求收集未完成（页数/受众/目的）"
            + (f": {detail}" if detail else f"（status={status}）")
        )

    _apply_ask_answers(inputs, answers, sent_questions=questions)
    _prune_satisfied_batch_missing_fields(inputs)


def _parse_derive_params_response(raw: str) -> dict[str, str]:
    payload = _parse_json_payload(raw)
    if not isinstance(payload, dict):
        raise RequirementCollectError("派生参数解析失败：LLM 未返回有效 JSON")

    search_mode = str(payload.get("search_mode") or "").strip()
    source_type = str(payload.get("source_type") or "").strip()
    research_depth = str(payload.get("research_depth") or "").strip().upper()

    if not search_mode:
        raise RequirementCollectError("派生参数不完整：缺少 search_mode")
    if search_mode not in _VALID_SEARCH_MODES:
        raise RequirementCollectError(f"派生参数无效：search_mode={search_mode!r}")

    if not source_type:
        raise RequirementCollectError("派生参数不完整：缺少 source_type")
    if source_type not in _VALID_SOURCE_TYPES:
        raise RequirementCollectError(f"派生参数无效：source_type={source_type!r}")

    if not research_depth:
        raise RequirementCollectError("派生参数不完整：缺少 research_depth")
    if research_depth not in _VALID_RESEARCH_DEPTHS:
        raise RequirementCollectError(f"派生参数无效：research_depth={research_depth!r}")

    return {
        "search_mode": search_mode,
        "source_type": source_type,
        "research_depth": research_depth,
    }


async def _derive_params_via_llm(node: PlanNode, inputs: dict[str, Any]) -> dict[str, str]:
    user_text = _collect_user_text(inputs)
    doc_excerpt = await PptCommon.read_file(
        node,
        inputs.get("doc_raw_path"),
        max_chars=_DOC_EXCERPT_MAX_CHARS,
        error_type=RequirementCollectError,
    )

    response = await node.stream_llm_collect(
        _build_p24_prompt(inputs, user_text, doc_excerpt),
        system_prompt=_P24_SYSTEM_PROMPT,
    )
    if not isinstance(response, str) or not response.strip():
        raise RequirementCollectError("派生参数推断失败：LLM 返回为空")

    return _parse_derive_params_response(response)


def _field_from_header(header: str) -> str | None:
    mapping = {
        "页数": "page_count",
        "受众": "audience",
        "目的": "presentation_purpose",
        "主题": "topic",
        "风格": "style_id",
    }
    return mapping.get(header.strip())


def _field_for_answer_item(
    item: dict[str, Any],
    sent_questions: list[dict[str, Any]] | None,
) -> str | None:
    """按答案中的 question 文本与发出题目精确匹配，映射到槽位字段。"""
    answer_q = str(item.get("question") or "").strip()
    if answer_q and sent_questions:
        for sent in sent_questions:
            sent_q = str(sent.get("question") or "").strip()
            if sent_q and sent_q == answer_q:
                return _field_from_header(str(sent.get("header") or ""))
    return _field_from_header(str(item.get("header") or ""))


def _apply_answer_item(
    inputs: dict[str, Any],
    item: dict[str, Any],
    *,
    sent_questions: list[dict[str, Any]] | None = None,
) -> None:
    field = _field_for_answer_item(item, sent_questions)

    selected = item.get("selected_options")
    label = ""
    if isinstance(selected, list) and selected:
        label = str(selected[0]).strip()
    other_text = str(
        item.get("other_text")
        or item.get("custom_text")
        or item.get("custom_input")
        or ""
    ).strip()

    if field == "page_count":
        count = _page_count_from_label(label, other_text)
        if count is not None:
            inputs["page_count"] = count
    elif field == "audience":
        inputs["audience"] = _audience_from_label(label, other_text)
    elif field == "presentation_purpose":
        inputs["presentation_purpose"] = _purpose_from_label(label, other_text)
    elif field == "topic":
        if label.startswith("确认："):
            inputs["topic"] = label.removeprefix("确认：").strip()
        else:
            topic = other_text or label
            if topic and topic != "其他":
                inputs["topic"] = topic
    elif field == "style_id":
        style_id, description = _style_id_from_label(label if label != "其他" else other_text)
        if not style_id and other_text:
            style_id, description = _style_id_from_label(other_text)
        if style_id:
            inputs["style_id"] = style_id
        if description:
            inputs["style_description"] = description
            if style_id == "custom":
                inputs["additional_notes"] = description


def _apply_ask_answers(
    inputs: dict[str, Any],
    answers: list[Any],
    *,
    sent_questions: list[dict[str, Any]] | None = None,
) -> None:
    for item in answers:
        if isinstance(item, dict):
            _apply_answer_item(inputs, item, sent_questions=sent_questions)


def _build_batch_questions(missing_fields: list[str]) -> list[dict[str, Any]]:
    questions: list[dict[str, Any]] = []

    if "page_count" in missing_fields:
        questions.append(
            {
                "header": "页数",
                "question": "需要多少页？",
                "multi_select": False,
                "options": [
                    {"label": "3-6 页（推荐）", "description": "适合简短汇报、产品介绍"},
                    {"label": "8-12 页", "description": "适合详细分析、项目方案"},
                    {"label": "15-20 页", "description": "适合深度报告、培训材料"},
                ],
            }
        )

    if "audience" in missing_fields:
        questions.append(
            {
                "header": "受众",
                "question": "目标受众是谁？",
                "multi_select": False,
                "options": [
                    {"label": "企业高管", "description": "强调结论先行、数据驱动"},
                    {"label": "技术团队", "description": "可包含技术细节与架构"},
                    {"label": "投资人/客户", "description": "强调商业价值与 ROI"},
                    {"label": "普通大众", "description": "简洁易懂、避免术语"},
                ],
            }
        )

    if "presentation_purpose" in missing_fields:
        questions.append(
            {
                "header": "目的",
                "question": "这次演示的主要目的是？",
                "multi_select": False,
                "options": [
                    {"label": "工作汇报", "description": "汇报进展、成果、总结"},
                    {"label": "产品/方案展示", "description": "产品发布、方案推介"},
                    {"label": "教学/分享", "description": "培训教程、知识分享"},
                    {"label": "AI 自动判断", "description": "根据主题自动选择目的"},
                ],
            }
        )

    return questions[:4]


def _build_style_question() -> dict[str, Any]:
    return {
        "header": "风格",
        "question": "请选择演示文稿的视觉风格",
        "multi_select": False,
        "options": [
            {"label": "华为风格", "description": "企业汇报、红色主题、严谨专业"},
            {"label": "浅色科技风", "description": "产品发布、黑白调性、极简设计"},
            {"label": "纸质人文风", "description": "文化主题、温暖质感"},
            {"label": "深色科技风", "description": "硬核场景、高对比度"},
            {"label": "自由发挥", "description": "由 AI 根据主题自动设计"},
        ],
    }


def _style_id_resolved(inputs: dict[str, Any]) -> str:
    return _normalize_style_id(inputs.get("style_id"))


def _style_needs_user_ask(inputs: dict[str, Any]) -> bool:
    """style_id 仍缺失时，判断是否需 ask（调用方应已确认 style 未 resolved）。"""
    if bool(inputs.get("need_ask_style")):
        return True
    return "style_id" in (inputs.get("missing_fields") or [])


def _finalize_style_slot(inputs: dict[str, Any], *, fallback: str | None = None) -> None:
    style_id = _style_id_resolved(inputs) or (fallback or "")
    if not style_id:
        raise RequirementCollectError("风格收集未完成：缺少 style_id")
    inputs["style_id"] = style_id
    inputs["need_ask_style"] = False
    inputs["missing_fields"] = [
        field for field in (inputs.get("missing_fields") or [])
        if field != "style_id"
    ]


async def _ask_missing_style(node: PlanNode, inputs: dict[str, Any]) -> None:
    if not node.has_tool("ask_user_question"):
        raise RequirementCollectError("缺少 ask_user_question 工具，无法收集风格")

    style_question = _build_style_question()
    result = await node.call_tool(
        "ask_user_question",
        questions=[style_question],
    )
    status, answers = _normalize_ask_result(result)
    if status != "answered" or not answers:
        detail = ""
        if isinstance(result, dict):
            detail = str(result.get("message") or "").strip()
        raise RequirementCollectError(
            "风格收集未完成"
            + (f": {detail}" if detail else f"（status={status}）")
        )

    _apply_ask_answers(inputs, answers, sent_questions=[style_question])


def _normalize_ask_result(result: Any) -> tuple[str, list[Any]]:
    if not isinstance(result, dict):
        return "error", []
    status = str(result.get("status") or "error")
    answers = result.get("answers")
    if not isinstance(answers, list):
        answers = []
    return status, answers


def _build_topic_suggest_prompt(inputs: dict[str, Any], doc_excerpt: str) -> str:
    parts = [f"请生成 {_TOPIC_SUGGEST_COUNT} 个可独立制作 PPT 的演示主题候选。\n"]
    user_text = _collect_user_text(inputs)
    if user_text:
        parts.append(f"用户消息：\n{user_text}\n")
    if doc_excerpt:
        parts.append(f"文档摘要：\n{doc_excerpt}\n")
    parts.append(
        f'按 JSON 返回 {{"topics":["..."]}}，topics 数组长度必须为 {_TOPIC_SUGGEST_COUNT}。'
    )
    return "\n".join(parts)


def _parse_topic_suggestions(raw: str) -> list[str]:
    payload = _parse_json_payload(raw)
    if not isinstance(payload, dict):
        return []
    topics_raw = payload.get("topics")
    if not isinstance(topics_raw, list):
        return []

    seen: set[str] = set()
    topics: list[str] = []
    for item in topics_raw:
        text = str(item).strip()
        if not text:
            continue
        key = text.casefold()
        if key in seen:
            continue
        seen.add(key)
        topics.append(text)
    return topics


def _build_topic_ask_question(topics: list[str]) -> dict[str, Any]:
    if len(topics) < 2:
        raise RequirementCollectError("主题候选不足，无法发起用户选择")
    option_topics = topics[:_TOPIC_SUGGEST_COUNT]
    return {
        "header": "主题",
        "question": "请选择本次演示的主题方向（每个选项均可直接作为完整 PPT 主题）：",
        "multi_select": False,
        "options": [{"label": topic} for topic in option_topics],
    }


def _topic_text_from_ask_answers(answers: list[Any]) -> str:
    for item in answers:
        if not isinstance(item, dict):
            continue
        other_text = str(
            item.get("other_text")
            or item.get("custom_text")
            or item.get("custom_input")
            or ""
        ).strip()
        if other_text:
            return other_text
        selected = item.get("selected_options")
        if not isinstance(selected, list) or not selected:
            continue
        label = str(selected[0]).strip()
        if label and label != "其他":
            return label
    return ""


def _append_topic_supplement(inputs: dict[str, Any], reply_text: str) -> None:
    text = reply_text.strip()
    if not text:
        return
    inputs["topic_user_reply"] = text
    supplement = f"[用户补充主题]: {text}"
    for key in _TEXT_SOURCE_KEYS:
        existing = inputs.get(key)
        if isinstance(existing, str) and existing.strip():
            inputs[key] = f"{existing.strip()}\n{supplement}"
            return
    inputs["user_message"] = supplement


async def _generate_topic_suggestions(
    node: PlanNode,
    inputs: dict[str, Any],
    doc_excerpt: str,
) -> list[str]:
    response = await node.stream_llm_collect(
        _build_topic_suggest_prompt(inputs, doc_excerpt),
        system_prompt=_P21_TOPIC_SUGGEST_SYSTEM_PROMPT,
    )
    topics = _parse_topic_suggestions(response)
    if len(topics) < _TOPIC_SUGGEST_COUNT:
        raise RequirementCollectError(
            f"未能生成 {_TOPIC_SUGGEST_COUNT} 个有效主题候选（实际 {len(topics)} 个）"
        )
    return topics[:_TOPIC_SUGGEST_COUNT]


async def _resolve_topic_via_ask(node: PlanNode, inputs: dict[str, Any]) -> None:
    if not node.has_tool("ask_user_question"):
        raise RequirementCollectError("缺少 ask_user_question 工具，无法收集演示主题")

    doc_excerpt = await PptCommon.read_file(
        node,
        inputs.get("doc_raw_path"),
        max_chars=_DOC_EXCERPT_MAX_CHARS,
        error_type=RequirementCollectError,
    )
    topic_options = await _generate_topic_suggestions(node, inputs, doc_excerpt)
    topic_question = _build_topic_ask_question(topic_options)

    ask_result = await node.call_tool(
        "ask_user_question",
        questions=[topic_question],
    )
    status, answers = _normalize_ask_result(ask_result)
    if status != "answered":
        detail = ""
        if isinstance(ask_result, dict):
            detail = str(ask_result.get("message") or "").strip()
        raise RequirementCollectError(
            f"未能获取用户主题选择（status={status}）" + (f": {detail}" if detail else "")
        )

    selected_topic = _topic_text_from_ask_answers(answers)
    if not selected_topic:
        raise RequirementCollectError("用户未选择有效主题")

    inputs["topic"] = selected_topic
    inputs["topic_user_reply"] = selected_topic
    inputs["missing_fields"] = [
        field for field in (inputs.get("missing_fields") or []) if field != "topic"
    ]
    _append_topic_supplement(inputs, selected_topic)


class P21SlotExtractNode(PlanNode):
    """P2.1 — LLM 槽位分析；topic 缺失时 LLM 生成 4 个主题候选并 ask 用户选择。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p2_1_slot_extract",
            instruction=(
                "单次 LLM 提取槽位与 missing_fields；topic 仍缺失时 call_llm 生成 4 个"
                "可独立做 PPT 的主题候选，再 ask_user_question 供用户选择；"
                "所选 label 直接写入 topic，不再二次提炼。"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        user_text = _collect_user_text(inputs)
        doc_excerpt = await PptCommon.read_file(
            self,
            inputs.get("doc_raw_path"),
            max_chars=_DOC_EXCERPT_MAX_CHARS,
            error_type=RequirementCollectError,
        )
        preserve_topic = _has_nonempty_topic(inputs)

        response = await self.stream_llm_collect(
            _build_p21_slot_prompt(user_text, doc_excerpt, inputs, preserve_topic=preserve_topic),
            system_prompt=_P21_SLOT_SYSTEM_PROMPT,
        )
        payload = _parse_slot_analysis_response(response, preserve_topic=preserve_topic)
        _merge_slot_payload(inputs, payload, preserve_topic=preserve_topic)

        if not _has_nonempty_topic(inputs):
            await _resolve_topic_via_ask(self, inputs)

        inputs["requirement_collect_status"] = "slots_analyzed"
        return inputs


class P22AskBatchNode(PlanNode):
    """P2.2 — 收集 page_count / audience / presentation_purpose，缺一不可。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p2_2_ask_batch",
            instruction=(
                "确保 page_count、audience、presentation_purpose 三项均已收集；"
                "缺失时 ask_user_question，否则报错，不填默认值。"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        await _ask_missing_batch_fields(self, inputs)
        _require_batch_fields_collected(inputs)
        return inputs


class P23AskStyleNode(PlanNode):
    """P2.3 — 收集 style_id。

    进入本节点时，topic 及 page_count / audience / presentation_purpose
    应已由 P2.1、P2.2 填齐；本节点只负责 style_id（custom 时含 style_description）。
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p2_3_ask_style",
            instruction=(
                "确保 style_id 已收集；P2.1 标记 need_ask_style 或 style_id 仍缺失时 "
                "ask_user_question，否则使用 free；不处理其他槽位。"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        use_implicit_free = False
        if not _style_id_resolved(inputs):
            if _style_needs_user_ask(inputs):
                await _ask_missing_style(self, inputs)
            else:
                use_implicit_free = True

        _finalize_style_slot(inputs, fallback="free" if use_implicit_free else None)
        return inputs


class P24DeriveParamsNode(PlanNode):
    """P2.4 — LLM 推断 search_mode、source_type、research_depth。

    进入本节点时，P2.1–P2.3 应已填齐 topic / 批量槽位 / style_id；
    本节点只负责三项派生参数，解析或校验失败即报错，不使用默认值。
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p2_4_derive_params",
            instruction=(
                "call_llm 推断 search_mode、source_type、research_depth；"
                "LLM 无有效输出或字段不在枚举内时 raise。"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        derived = await _derive_params_via_llm(self, inputs)
        inputs.update(derived)
        return inputs


class RequirementCollectNode(PlanNode):
    """P2 — 需求收集（P2.1 → P2.2 → P2.3 → P2.4）。

    预期输入（ctx / inputs）:
        可选: task | user_request | user_message | query — 用户原文（含 officeclaw JSON 包装）
        可选: topic — 上游 P3 推断或用户已给主题
        可选: topic_inferred, doc_raw_path, has_documents, doc_parse_ok
        可选: slots_from_query — P1 在无附件且无路径时预提取的槽位信息
        可选: slots_from_query_complete — P1 标记预提取槽位是否全部非空

    预期输出（成功时写入同一 ctx，下列字段必须齐备）:
        topic, page_count, audience, presentation_purpose, style_id
        style_description, additional_notes（style_id=custom 时）
        search_mode, source_type, research_depth
        requirement_collect_status（P2.1 写入，通常为 slots_analyzed）

    过程字段（成功收尾后通常已清理）:
        missing_fields, need_ask_style

    子步骤保证:
        P2.1 — 槽位识别；topic 缺失时 ask + 二次 LLM 提炼
        P2.2 — page_count / audience / presentation_purpose 缺一不可
        P2.3 — 仅收集 style_id（P2.1 判定无需 ask 时可隐式 free）
        P2.4 — LLM 推断三项派生参数，解析/校验失败即报错

    快捷路径:
        无附件且 P1 已预提取全部槽位（slots_from_query_complete=True）时，
        直接填入预提取值，跳过 P2.1–P2.3，仅执行 P2.4。

    失败时 raise RequirementCollectError，不静默填 batch 槽位或派生参数默认值。
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p2_requirement_collect",
            instruction=(
                "需求收集：P2.1 槽位识别（缺 topic 则 ask）；"
                "P2.2/P2.3 条件 ask_user_question；"
                "P2.4 LLM 推断 search_mode / source_type / research_depth；"
                "无附件且 P1 已预提取全部槽位时可跳过 P2.1–P2.3；"
                "缺失或解析失败即报错。"
            ),
            sub_plans=[
                P21SlotExtractNode(),
                P22AskBatchNode(),
                P23AskStyleNode(),
                P24DeriveParamsNode(),
            ],
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        ctx = inputs

        # 快捷路径：无附件且 P1 已从 query 预提取全部槽位
        pre_slots = ctx.get("slots_from_query", {})
        all_filled = ctx.get("slots_from_query_complete", False)
        if not ctx.get("has_documents") and all_filled and pre_slots:
            for slot in ("topic", "page_count", "audience", "presentation_purpose", "style_id"):
                v = pre_slots.get(slot)
                if slot == "page_count" and v is not None:
                    ctx[slot] = v
                elif isinstance(v, str) and v.strip():
                    ctx[slot] = v
            await self.skip_subplan(self.sub_plans[0], ctx, message="slots pre-filled from query")
            await self.skip_subplan(self.sub_plans[1], ctx, message="slots pre-filled from query")
            await self.skip_subplan(self.sub_plans[2], ctx, message="slots pre-filled from query")
            await self.execute_subplan(self.sub_plans[3], ctx)  # P2.4 必跑

            if not _has_nonempty_topic(ctx):
                raise RequirementCollectError("缺少演示主题 topic，无法继续 PPT 流水线")
            return ctx

        # 部分预填：把 P1 提取的已知槽位填入 ctx，让 P2.1 减少工作量
        if pre_slots and not ctx.get("has_documents"):
            for slot, value in pre_slots.items():
                if slot == "page_count" and value is not None and ctx.get("page_count") is None:
                    ctx[slot] = value
                elif isinstance(value, str) and value.strip() and not ctx.get(slot):
                    ctx[slot] = value

        await self.execute_subplan(self.sub_plans[0], ctx)

        for subplan in self.sub_plans[1:]:
            await self.execute_subplan(subplan, ctx)

        if not _has_nonempty_topic(ctx):
            raise RequirementCollectError("缺少演示主题 topic，无法继续 PPT 流水线")

        return ctx
