from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path
from typing import Any

from jiuwenclaw.agentserver.replan_agent.plan_node import PlanNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.ppt_common import PptCommon

logger = logging.getLogger(__name__)

_MATERIAL_RICHNESS = frozenset({"rich", "thin", "empty"})
_VALID_SEARCH_MODES = frozenset({"auto", "no_search", "force_search"})
_VALID_SOURCE_TYPES = frozenset({"topic", "outline", "description"})
_SOURCE_MATERIAL_MAX_CHARS = 4000
_SEARCH_RESULT_MAX_CHARS = 3500
_OUTLINE_NAME = "outline.md"
_SEARCH_RESULTS_FOR_P43_MAX_CHARS = 8000
_PAGE_HEADING_PATTERN = re.compile(r"^###\s+P(\d+)\s*:", re.MULTILINE)
_OUTLINE_FIELD_PATTERN = re.compile(
    r"^-\s*\*\*(?P<field>[^*]+)\*\*[：:]\s*(?P<value>.+?)\s*$",
    re.MULTILINE,
)
_P4_MAX_ATTEMPTS = 2
_QUERY_BOUNDS_NO_MATERIAL = (5, 8)
_QUERY_BOUNDS_WITH_MATERIAL = (3, 5)
_RESEARCH_DIMENSIONS = (
    "领域现状",
    "关键维度",
    "最新动态",
    "核心玩家",
    "争议热点",
)

_P41_SYSTEM_PROMPT = """你是 PPT 内容策划助手。根据主题、目标页数与用户素材，评估素材充裕度并给出研究重点。

素材充裕度 material_richness 判定：
- rich（充实）：有清晰章节结构，且具体数据/案例/论述足以支撑目标页数
- thin（单薄）：仅有提纲或概要，信息量不足以填充多页 PPT
- empty（空）：无素材或内容极少

规则：
1. 只根据给定素材与需求判断，不要编造素材中不存在的信息。
2. 无素材时必须返回 material_richness=empty。
3. focus_areas 为 1~3 句研究重点方向；无素材时可基于 topic 推断。

必须只输出 JSON：
{"material_richness":"empty","focus_areas":"..."}"""

_P42A_SYSTEM_PROMPT = """你是 PPT 快速调研助手。根据主题、研究重点与用户素材，生成固定批次的网页搜索查询。

规则：
1. 查询应覆盖不同维度：领域现状、关键维度、最新动态、核心玩家、争议热点（可合并到单条 query 的 intent 中）。
2. 中文主题建议中英双语 query 搭配；添加年份（如 2026）或 latest/report/statistics 等可信来源关键词。
3. 有用户素材时，聚焦素材未覆盖的维度，避免重复已有信息。
4. 不要编造已确认的事实；只输出搜索 query，不写结论。

必须只输出 JSON：
{"queries":[{"dimension":"领域现状","query":"..."}]}"""

_P43_COMMON_RULES = """大纲格式要求（必须严格遵守）：

1. 文件以 `# 大纲：{topic}` 开头，随后元信息行：
   **受众**、**总页数**、**叙事主线**、**输入类型**、**搜索模式**
2. 若需写入已搜索来源，在 `## 已搜索来源` 下用表格列出 URL 与覆盖维度（不写正式评分，评分留给 P6）。
   无需搜索时删除整个 `## 已搜索来源` 章节。
3. `## 页面规划` 下每页一个 `### P{N}:` 块，字段齐全：
   - **类型**：cover/ending/agenda/section/chapter/transition/conclusion/trend/data/case/comparison/technology 等
   - **研究需求**：cover/ending/agenda/section/chapter/transition/conclusion 标 ❌，其余标 ✅
   - **标题**：结论性完整句（Action + Result）
   - **内容概要**：具体有信息量
   - **研究查询**：✅ 页 2-4 个精准查询；❌ 页填 `-`
   - **数据需求**：✅ 页写具体数据类型和维度，数据需求必须具体化；❌ 页填 `-`
4. 内容页数（研究需求：✅）等于 page_count；默认总页数为 page_count + 2（封面 + 结束页）。
   仅当用户明确要求目录/章节/过渡/总结等结构页时才额外增加，这些结构页不占用内容页数。
5. 基于给定素材与搜索结果，不编造不存在的趋势或数据。
6. 只输出 Markdown 正文，不要 JSON，不要代码围栏。"""

_P43_TOPIC_SYSTEM_PROMPT = f"""你是 PPT 大纲策划师（source_type=topic）。基于搜索结果与用户素材，生成结构化 outline.md。

{_P43_COMMON_RULES}"""

_P43_OUTLINE_SYSTEM_PROMPT = f"""你是 PPT 大纲策划师（source_type=outline）。用户已提供结构化大纲，**必须保留原文**。

核心规则：
1. **禁止修改**用户原文的任何内容
2. **禁止添加**原文中没有的新内容
3. **禁止删除**原文中的任何内容
4. 只做结构化重组，映射为 `### P{{N}}:` 页面块
5. 保留用户原文标题与要点，整合到内容概要中，不重新措辞
6. 为每页推断类型与研究需求标记；研究查询基于各页主题自动生成

{_P43_COMMON_RULES}"""

_P43_DESCRIPTION_SYSTEM_PROMPT = f"""你是 PPT 大纲策划师（source_type=description）。从用户详细描述文本中提取页面结构。

核心规则：
1. 识别描述中的页面/章节结构，提取每页标题与关键要点
2. 保留描述中的逻辑结构与组织方式
3. 要点为描述内容的简明摘要
4. 补充页面类型、研究需求、研究查询、数据需求等元信息

{_P43_COMMON_RULES}"""


class ContentPlanError(RuntimeError):
    """P4 内容策划失败。"""


def _require_p4_prerequisites(inputs: dict[str, Any]) -> None:
    topic = inputs.get("topic")
    if not isinstance(topic, str) or not topic.strip():
        raise ContentPlanError("缺少演示主题 topic，无法进入 P4")

    page_count = inputs.get("page_count")
    if page_count is None:
        raise ContentPlanError("缺少 page_count，无法进入 P4")

    audience = inputs.get("audience")
    if not isinstance(audience, str) or not audience.strip():
        raise ContentPlanError("缺少 audience，无法进入 P4")

    search_mode = str(inputs.get("search_mode") or "").strip()
    if search_mode not in _VALID_SEARCH_MODES:
        raise ContentPlanError(f"缺少或无效的 search_mode: {search_mode!r}")

    source_type = str(inputs.get("source_type") or "").strip()
    if source_type not in _VALID_SOURCE_TYPES:
        raise ContentPlanError(f"缺少或无效的 source_type: {source_type!r}")

    output_dir = inputs.get("output_dir")
    if not output_dir or not str(output_dir).strip():
        raise ContentPlanError("缺少 output_dir，无法进入 P4")


def _decide_p4_should_search(search_mode: str, material_richness: str) -> bool:
    """按 outline-planner 素材充裕度 × search_mode 决策表计算是否执行 P4.2。"""
    if search_mode == "no_search":
        return False
    if search_mode == "force_search":
        return True
    if material_richness == "rich":
        return False
    if material_richness in ("thin", "empty"):
        return True
    raise ContentPlanError(f"无效的 material_richness: {material_richness!r}")


def _p4_search_reason(search_mode: str, material_richness: str, should_search: bool) -> str:
    if not should_search:
        if search_mode == "no_search":
            return "search_mode=no_search，跳过快速调研"
        if search_mode == "auto" and material_richness == "rich":
            return "auto 模式且素材充实，跳过快速调研"
        return "无需快速调研"
    if search_mode == "force_search":
        return "search_mode=force_search，执行快速调研"
    return f"auto 模式且素材为 {material_richness}，执行快速调研"


def _parse_p41_response(raw: str) -> dict[str, str]:
    payload = PptCommon.parse_json_payload(raw)
    if not isinstance(payload, dict):
        raise ContentPlanError("P4.1 解析失败：LLM 未返回有效 JSON")

    material_richness = str(payload.get("material_richness") or "").strip().lower()
    if material_richness not in _MATERIAL_RICHNESS:
        raise ContentPlanError(f"P4.1 无效的 material_richness: {material_richness!r}")

    focus_areas = str(payload.get("focus_areas") or "").strip()
    if not focus_areas:
        raise ContentPlanError("P4.1 缺少 focus_areas")

    return {
        "material_richness": material_richness,
        "focus_areas": focus_areas,
    }


def _build_p41_prompt(inputs: dict[str, Any], source_material: str) -> str:
    parts = [
        "请评估以下 PPT 需求的素材充裕度。\n",
        f"- topic: {inputs.get('topic', '')}\n",
        f"- page_count: {inputs.get('page_count')}\n",
        f"- audience: {inputs.get('audience', '')}\n",
        f"- presentation_purpose: {inputs.get('presentation_purpose', '')}\n",
        f"- source_type: {inputs.get('source_type', '')}\n",
        f"- search_mode: {inputs.get('search_mode', '')}\n",
        f"- has_documents: {bool(inputs.get('has_documents'))}\n",
        f"- doc_parse_ok: {bool(inputs.get('doc_parse_ok'))}\n",
    ]
    failure_reason = inputs.get("failure_reason")
    if isinstance(failure_reason, str) and failure_reason.strip():
        parts.append(f"上次失败原因：\n{failure_reason.strip()}\n")
    if source_material:
        parts.append(f"用户素材（doc_raw 摘要）：\n{source_material}\n")
    else:
        parts.append("用户素材：无\n")
    parts.append("按 JSON 返回 material_richness、focus_areas。")
    return "\n".join(parts)


def _apply_p41_result(inputs: dict[str, Any], parsed: dict[str, str], source_material: str) -> None:
    search_mode = str(inputs.get("search_mode") or "").strip()
    material_richness = parsed["material_richness"]
    should_search = _decide_p4_should_search(search_mode, material_richness)

    inputs["has_source_material"] = bool(source_material)
    inputs["source_material_chars"] = len(source_material)
    inputs["material_richness"] = material_richness
    inputs["focus_areas"] = parsed["focus_areas"]
    inputs["p4_should_search"] = should_search
    inputs["p4_search_reason"] = _p4_search_reason(search_mode, material_richness, should_search)
    inputs["content_plan_status"] = "normalized"


async def _run_p41_normalize(node: PlanNode, inputs: dict[str, Any]) -> None:
    _require_p4_prerequisites(inputs)

    source_material = await PptCommon.read_file(
        node,
        inputs.get("doc_raw_path"),
        max_chars=_SOURCE_MATERIAL_MAX_CHARS,
        error_type=ContentPlanError,
    )
    response = await node.stream_llm_collect(
        _build_p41_prompt(inputs, source_material),
        system_prompt=_P41_SYSTEM_PROMPT,
    )
    if not isinstance(response, str) or not response.strip():
        raise ContentPlanError("P4.1 失败：LLM 返回为空")

    parsed = _parse_p41_response(response)
    _apply_p41_result(inputs, parsed, source_material)


def _query_count_bounds(has_source_material: bool) -> tuple[int, int]:
    return _QUERY_BOUNDS_WITH_MATERIAL if has_source_material else _QUERY_BOUNDS_NO_MATERIAL


def _normalize_tool_text(result: Any) -> str:
    if result is None:
        return ""
    if isinstance(result, str):
        return result.strip()
    if isinstance(result, dict):
        for key in ("content", "output", "result", "stdout", "text", "answer"):
            value = result.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        if result.get("success") is False:
            error = result.get("error") or result.get("message")
            if isinstance(error, str):
                return f"[ERROR]: {error}"
    return str(result).strip()


def _is_search_result_usable(text: str) -> bool:
    stripped = text.strip()
    if not stripped:
        return False
    if stripped.startswith("[ERROR]"):
        return False
    return True


def _truncate_text(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n...(内容已截断)"


def _parse_p42a_queries(raw: str, *, has_source_material: bool) -> list[dict[str, str]]:
    payload = PptCommon.parse_json_payload(raw)
    if not isinstance(payload, dict):
        raise ContentPlanError("P4.2a 解析失败：LLM 未返回有效 JSON")

    queries_raw = payload.get("queries")
    if not isinstance(queries_raw, list) or not queries_raw:
        raise ContentPlanError("P4.2a 缺少 queries 数组")

    min_count, max_count = _query_count_bounds(has_source_material)
    if not min_count <= len(queries_raw) <= max_count:
        raise ContentPlanError(
            f"P4.2a 查询数量应为 {min_count}~{max_count}，实际 {len(queries_raw)}"
        )

    parsed: list[dict[str, str]] = []
    seen: set[str] = set()
    for item in queries_raw:
        if not isinstance(item, dict):
            raise ContentPlanError("P4.2a queries 项必须为对象")
        dimension = str(item.get("dimension") or "").strip() or "综合"
        query = str(item.get("query") or "").strip()
        if not query:
            raise ContentPlanError("P4.2a 存在空 query")
        key = query.casefold()
        if key in seen:
            continue
        seen.add(key)
        parsed.append({"dimension": dimension, "query": query})

    if not min_count <= len(parsed) <= max_count:
        raise ContentPlanError(
            f"P4.2a 去重后查询数量应为 {min_count}~{max_count}，实际 {len(parsed)}"
        )
    return parsed


def _build_p42a_prompt(inputs: dict[str, Any], source_material: str) -> str:
    min_count, max_count = _query_count_bounds(bool(source_material))
    parts = [
        f"请生成 {min_count}~{max_count} 条并行搜索 query。\n",
        f"- topic: {inputs.get('topic', '')}\n",
        f"- page_count: {inputs.get('page_count')}\n",
        f"- audience: {inputs.get('audience', '')}\n",
        f"- focus_areas: {inputs.get('focus_areas', '')}\n",
        f"- has_source_material: {bool(source_material)}\n",
        f"- 建议覆盖维度: {', '.join(_RESEARCH_DIMENSIONS)}\n",
    ]
    if source_material:
        parts.append(f"用户素材摘要：\n{source_material}\n")
    parts.append('按 JSON 返回 {"queries":[{"dimension":"...","query":"..."}]}。')
    return "\n".join(parts)


def _format_search_results_for_p43(search_results: list[dict[str, str]]) -> str:
    """将搜索结果格式化为 P4.3 prompt 中的文本。"""
    parts: list[str] = ["### 网页搜索结果\n"]
    total_chars = 0
    for batch in search_results:
        block = (
            f"#### query: {batch['query']}（维度: {batch.get('dimension', '')}）\n"
            f"{batch['result']}\n\n"
        )
        if total_chars + len(block) > _SEARCH_RESULTS_FOR_P43_MAX_CHARS:
            parts.append("...(更多搜索结果已截断)\n")
            break
        parts.append(block)
        total_chars += len(block)
    return "\n".join(parts)


async def _run_parallel_web_searches(
    node: PlanNode,
    queries: list[dict[str, str]],
) -> list[dict[str, str]]:
    if not node.has_tool("web_search"):
        raise ContentPlanError("缺少 web_search 工具，无法执行 P4.2 快速调研")

    async def _search_one(item: dict[str, str]) -> dict[str, str]:
        raw = await node.call_tool("web_search", query=item["query"])
        result = _truncate_text(_normalize_tool_text(raw), _SEARCH_RESULT_MAX_CHARS)
        return {
            "dimension": item["dimension"],
            "query": item["query"],
            "result": result,
        }

    return list(await asyncio.gather(*[_search_one(item) for item in queries]))


async def _run_p42_quick_research(node: PlanNode, inputs: dict[str, Any]) -> None:
    source_material = await PptCommon.read_file(
        node,
        inputs.get("doc_raw_path"),
        max_chars=_SOURCE_MATERIAL_MAX_CHARS,
        error_type=ContentPlanError,
    )
    has_source_material = bool(source_material)

    response_a = await node.stream_llm_collect(
        _build_p42a_prompt(inputs, source_material),
        system_prompt=_P42A_SYSTEM_PROMPT,
    )
    if not isinstance(response_a, str) or not response_a.strip():
        raise ContentPlanError("P4.2a 失败：LLM 返回为空")

    query_items = _parse_p42a_queries(response_a, has_source_material=has_source_material)
    search_batches = await _run_parallel_web_searches(node, query_items)

    usable = [batch for batch in search_batches if _is_search_result_usable(batch["result"])]
    if not usable:
        raise ContentPlanError("P4.2b 快速调研失败：所有 web_search 均无有效结果")

    inputs["search_results"] = usable
    inputs["p4_search_queries"] = [item["query"] for item in query_items]
    inputs["p4_search_hit_count"] = len(usable)
    inputs["p4_quick_research_status"] = "completed"
    inputs["content_plan_status"] = "quick_research_done"


def _p43_system_prompt(source_type: str) -> str:
    if source_type == "outline":
        return _P43_OUTLINE_SYSTEM_PROMPT
    if source_type == "description":
        return _P43_DESCRIPTION_SYSTEM_PROMPT
    return _P43_TOPIC_SYSTEM_PROMPT


def _should_include_searched_sources(inputs: dict[str, Any]) -> bool:
    return str(inputs.get("p4_quick_research_status") or "").strip() == "completed"


def _is_no_search_degraded(inputs: dict[str, Any]) -> bool:
    search_mode = str(inputs.get("search_mode") or "").strip()
    if search_mode != "no_search":
        return False
    richness = str(inputs.get("material_richness") or "").strip()
    return richness in ("thin", "empty")


def _build_p43_prompt(
    inputs: dict[str, Any],
    source_material: str,
    search_results_text: str,
) -> str:
    topic = str(inputs.get("topic") or "").strip()
    page_count = inputs.get("page_count")
    audience = str(inputs.get("audience") or "").strip()
    source_type = str(inputs.get("source_type") or "topic").strip()
    search_mode = str(inputs.get("search_mode") or "").strip()
    focus_areas = str(inputs.get("focus_areas") or "").strip()
    presentation_purpose = str(inputs.get("presentation_purpose") or "").strip()
    include_sources = _should_include_searched_sources(inputs)
    degraded = _is_no_search_degraded(inputs)

    parts = [
        f"请生成 outline.md 正文，主题：{topic}\n",
        f"- page_count: {page_count}（内容页数，不含封面/结束页；默认总页数为 page_count + 2）\n",
        f"- audience: {audience}\n",
        f"- source_type: {source_type}\n",
        f"- search_mode: {search_mode}\n",
        f"- focus_areas: {focus_areas}\n",
    ]
    if presentation_purpose:
        parts.append(f"- presentation_purpose: {presentation_purpose}\n")
    parts.append(f"- include_searched_sources_section: {include_sources}\n")
    if degraded:
        parts.append(
            "- 注意：no_search 模式且素材不足，请在大纲中标注「素材有限」相关页面，尽力基于现有素材生成。\n"
        )
    if str(inputs.get("search_mode") or "").strip() == "no_search":
        parts.append(
            '- no_search 模式：研究查询与数据需求仍需填写（描述"如有搜索会查询什么"），但标注为「仅参考」。\n'
        )

    failure_reason = inputs.get("failure_reason")
    if isinstance(failure_reason, str) and failure_reason.strip():
        parts.append(f"上次失败原因：\n{failure_reason.strip()}\n")

    if source_material:
        parts.append(f"用户素材（doc_raw）：\n{source_material}\n")
    else:
        parts.append("用户素材：无\n")

    if search_results_text:
        parts.append(f"调研结果（网页搜索）：\n{search_results_text}\n")
    elif include_sources:
        parts.append("调研结果：无（请基于素材与主题生成，已搜索来源章节可留空表格或简要说明）\n")
    else:
        parts.append("调研结果：无（跳过搜索，不要写 ## 已搜索来源 章节）\n")

    parts.append("输出完整 outline.md Markdown 正文。")
    return "\n".join(parts)


def _strip_markdown_fence(text: str) -> str:
    stripped = text.strip()
    fence_match = PptCommon.JSON_FENCE_PATTERN.search(stripped)
    if fence_match and stripped.startswith("```"):
        return fence_match.group(1).strip()
    return stripped


def _validate_outline_markdown_basic(text: str, *, topic: str, page_count: Any) -> None:
    stripped = text.strip()
    if not stripped:
        raise ContentPlanError("P4.3 生成的 outline 为空")

    if "# 大纲：" not in stripped:
        raise ContentPlanError("P4.3 outline 缺少 `# 大纲：` 标题")

    if topic.strip():
        topic_marker = f"# 大纲：{topic.strip()}"
        if topic_marker not in stripped:
            raise ContentPlanError("P4.3 outline 标题与 topic 不匹配")

    if "## 页面规划" not in stripped:
        raise ContentPlanError("P4.3 outline 缺少 `## 页面规划` 章节")

    page_numbers = [int(match.group(1)) for match in _PAGE_HEADING_PATTERN.finditer(stripped)]
    if not page_numbers:
        raise ContentPlanError("P4.3 outline 缺少 `### P{N}:` 页面块")

    expected_content_pages = int(page_count) if page_count is not None else None
    if expected_content_pages is not None:
        pages = _split_outline_pages(stripped)
        content_count = sum(1 for _, blk in pages if _is_research_required_page(blk))
        if content_count != expected_content_pages:
            raise ContentPlanError(
                f"P4.3 outline 内容页数（✅）应为 {expected_content_pages}，"
                f"实际 {content_count}"
            )

    required_fields = ("**类型**", "**研究需求**", "**标题**", "**内容概要**", "**研究查询**", "**数据需求**")
    for field in required_fields:
        if field not in stripped:
            raise ContentPlanError(f"P4.3 outline 缺少字段 {field}")


def _split_outline_pages(text: str) -> list[tuple[int, str]]:
    matches = list(_PAGE_HEADING_PATTERN.finditer(text))
    if not matches:
        return []
    pages: list[tuple[int, str]] = []
    for index, match in enumerate(matches):
        start = match.start()
        end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
        pages.append((int(match.group(1)), text[start:end]))
    return pages


def _extract_outline_field(block: str, field: str) -> str:
    for line_match in _OUTLINE_FIELD_PATTERN.finditer(block):
        if line_match.group("field").strip() == field:
            return line_match.group("value").strip()
    return ""


def _is_research_required_page(block: str) -> bool:
    return "✅" in _extract_outline_field(block, "研究需求")


def _is_placeholder_field_value(value: str) -> bool:
    stripped = value.strip()
    if not stripped:
        return True
    normalized = stripped
    for suffix in ("（仅参考）", "(仅参考)"):
        normalized = normalized.replace(suffix, "")
    normalized = normalized.strip()
    return normalized in {"-", "—", "–", "无", "N/A", "n/a"}


def _validate_outline_markdown_full(
    text: str,
    *,
    topic: str,
    page_count: Any,
    include_searched_sources: bool,
) -> None:
    _validate_outline_markdown_basic(text, topic=topic, page_count=page_count)

    if include_searched_sources and "## 已搜索来源" not in text:
        raise ContentPlanError("P4.4 outline 缺少 `## 已搜索来源` 章节（搜索模式下必填）")

    if not include_searched_sources and "## 已搜索来源" in text:
        pass  # 允许 LLM 误写，不因此失败

    for page_number, block in _split_outline_pages(text):
        if not _is_research_required_page(block):
            continue
        research_queries = _extract_outline_field(block, "研究查询")
        data_needs = _extract_outline_field(block, "数据需求")
        if _is_placeholder_field_value(research_queries):
            raise ContentPlanError(
                f"P4.4 P{page_number} 研究需求为 ✅，但缺少有效 **研究查询**"
            )
        if _is_placeholder_field_value(data_needs):
            raise ContentPlanError(
                f"P4.4 P{page_number} 研究需求为 ✅，但缺少有效 **数据需求**"
            )


def _resolve_outline_path(inputs: dict[str, Any]) -> Path:
    outline_path = inputs.get("outline_path")
    if outline_path:
        return Path(str(outline_path)).expanduser().resolve()

    output_dir = inputs.get("output_dir")
    if not output_dir:
        raise ContentPlanError("P4.4 缺少 outline_path 与 output_dir")
    return (Path(str(output_dir)).expanduser() / _OUTLINE_NAME).resolve()


async def _run_p44_validate(node: PlanNode, inputs: dict[str, Any]) -> None:
    outline_path = _resolve_outline_path(inputs)
    outline_text = await PptCommon.read_file(
        node,
        outline_path,
        required=True,
        label=_OUTLINE_NAME,
        error_type=ContentPlanError,
    )

    topic = str(inputs.get("topic") or "").strip()
    _validate_outline_markdown_full(
        outline_text,
        topic=topic,
        page_count=inputs.get("page_count"),
        include_searched_sources=_should_include_searched_sources(inputs),
    )

    inputs["outline_path"] = str(outline_path)
    inputs["p4_validate_status"] = "passed"
    inputs["content_plan_status"] = "completed"


async def _write_outline(
    node: PlanNode,
    output_dir: str | Path,
    content: str,
) -> Path:
    path = Path(str(output_dir)).expanduser() / _OUTLINE_NAME
    written = await PptCommon.write_file(
        node,
        path,
        content,
        label=_OUTLINE_NAME,
        error_type=ContentPlanError,
    )
    logger.info("[P4] %s 已落盘：%s", _OUTLINE_NAME, written)
    return written


async def _run_p43_outline_gen(node: PlanNode, inputs: dict[str, Any]) -> None:
    _require_p4_prerequisites(inputs)

    source_type = str(inputs.get("source_type") or "topic").strip()
    if source_type not in _VALID_SOURCE_TYPES:
        raise ContentPlanError(f"P4.3 无效的 source_type: {source_type!r}")

    source_material = await PptCommon.read_file(
        node,
        inputs.get("doc_raw_path"),
        max_chars=_SOURCE_MATERIAL_MAX_CHARS,
        error_type=ContentPlanError,
    )
    search_results_text = ""
    search_results = inputs.get("search_results")
    if search_results:
        search_results_text = _format_search_results_for_p43(search_results)

    response = await node.stream_llm_collect(
        _build_p43_prompt(inputs, source_material, search_results_text),
        system_prompt=_p43_system_prompt(source_type),
    )
    if not isinstance(response, str) or not response.strip():
        raise ContentPlanError("P4.3 失败：LLM 返回为空")

    outline_text = _strip_markdown_fence(response)
    topic = str(inputs.get("topic") or "").strip()
    _validate_outline_markdown_basic(outline_text, topic=topic, page_count=inputs.get("page_count"))

    _all_page_nums = [int(m.group(1)) for m in _PAGE_HEADING_PATTERN.finditer(outline_text)]
    inputs["total_pages"] = len(_all_page_nums)

    outline_path = await _write_outline(
        node,
        str(inputs["output_dir"]),
        outline_text,
    )
    inputs["outline_path"] = str(outline_path)
    inputs["p4_outline_gen_status"] = "completed"
    inputs["content_plan_status"] = "outline_generated"


class P41NormalizeNode(PlanNode):
    """P4.1 — 需求标准化与素材充裕度评估。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p4_1_normalize",
            instruction=(
                "## P4.1 需求标准化与素材评估\n"
                "\n"
                "### 节点职责\n"
                "读取素材 → LLM 评估充裕度 → 计算 p4_should_search。\n"
                "\n"
                "### 前置条件\n"
                "- `read_file` / `stream_llm` 工具可用\n"
                "- `output_dir` 已由 P0 创建\n"
                "\n"
                "### 输入\n"
                "- `doc_raw_path`（可选）: 文档素材路径（有则读取，无则 source_material 为空）\n"
                "- `search_mode`（必填）: 搜索策略\n"
                "- `topic`（必填）: PPT 主题\n"
                "\n"
                "### 输出\n"
                "- `has_source_material`: bool — 是否有素材\n"
                "- `source_material_chars`: int — 素材字符数\n"
                "- `material_richness`: str — 充裕度评估（rich / moderate / poor / empty）\n"
                "- `focus_areas`: list[str] — 重点领域\n"
                "- `p4_should_search`: bool — 是否需要 P4.2 搜索\n"
                "- `p4_search_reason`: str — 搜索或不搜索的原因\n"
                "- `content_plan_status`: str = 'normalizing'\n"
                "\n"
                "### 执行流程\n"
                "1. 读取 doc_raw_path 作为 source_material\n"
                "2. call_llm 评估 material_richness\n"
                "3. 按 search_mode × 素材充裕度规则表计算 p4_should_search\n"
                "\n"
                "### 失败兜底\n"
                "- doc_raw_path 不存在或为空: has_source_material=False, material_richness='empty'\n"
                "- LLM 评估失败: raise ContentPlanError\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        await _run_p41_normalize(self, inputs)
        return inputs


class P42QuickResearchNode(PlanNode):
    """P4.2 — 条件化快速调研：生成 query → 并行 web_search，搜索结果直接传递给 P4.3。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p4_2_quick_research",
            instruction=(
                "## P4.2 条件快速调研\n"
                "\n"
                "### 节点职责\n"
                "p4_should_search=True 时：LLM 生成搜索 query → 并行 web_search → 搜索结果供 P4.3 使用。\n"
                "p4_should_search=False 时：跳过。\n"
                "\n"
                "### 前置条件\n"
                "- P4.1 已完成，`p4_should_search` 已确定\n"
                "- `stream_llm` / `web_search` 工具可用（仅 p4_should_search=True 时需要）\n"
                "\n"
                "### 输入\n"
                "- `p4_should_search`（必填）: 是否需要搜索\n"
                "- `topic`（必填）: PPT 主题（生成搜索 query 的依据）\n"
                "- `focus_areas`（可选）: 重点领域（辅助 query 生成）\n"
                "\n"
                "### 输出\n"
                "p4_should_search=True 时：\n"
                "- `search_results`: list[dict[str, str]] — 搜索结果批次列表（每项含 query/dimension/result）\n"
                "- `p4_search_queries`: list[str] — 本次搜索使用的 query 列表\n"
                "- `p4_search_hit_count`: int — 搜索命中数量\n"
                "- `p4_quick_research_status`: str = 'completed'\n"
                "\n"
                "p4_should_search=False 时：\n"
                "- `p4_quick_research_status`: str = 'skipped'\n"
                "- `search_results` 为空 / 不存在\n"
                "\n"
                "### 执行流程\n"
                "1. p4_should_search=False → 直接跳过，写入 skipped\n"
                "2. p4_should_search=True → LLM 生成固定批次 query\n"
                "3. 并行 web_search，汇总搜索结果\n"
                "\n"
                "### 失败兜底\n"
                "- web_search 全部失败: raise ContentPlanError\n"
                "- LLM 生成 query 失败: 使用 topic 直接作为单条 query 搜索\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        if not inputs.get("p4_should_search"):
            inputs["p4_quick_research_status"] = "skipped"
            return inputs

        await _run_p42_quick_research(self, inputs)
        return inputs


class P43OutlineGenNode(PlanNode):
    """P4.3 — 按 source_type 生成 outline.md。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p4_3_outline_gen",
            instruction=(
                "## P4.3 大纲生成\n"
                "\n"
                "### 节点职责\n"
                "按 source_type 策略 LLM 生成大纲，write_file 落盘 outline.md。\n"
                "\n"
                "### 前置条件\n"
                "- P4.1 已完成，素材评估已产出\n"
                "- P4.2 已完成（或已跳过），search_results 已确定\n"
                "- `stream_llm` / `write_file` 工具可用\n"
                "\n"
                "### 输入\n"
                "- `output_dir`（必填）: 工作目录\n"
                "- `source_type`（必填）: 素材来源策略\n"
                "- `topic`（必填）: PPT 主题\n"
                "- `page_count`（必填）: 页数\n"
                "- `search_results`（可选）: P4.2 搜索结果（有则辅助大纲生成）\n"
                "- `source_material` / `focus_areas`（可选）: P4.1 产出的素材与重点领域\n"
                "\n"
                "### 输出\n"
                "- `outline_path`: str — `{output_dir}/outline.md` 绝对路径（文件已写入）\n"
                "- `p4_outline_gen_status`: str = 'completed'\n"
                "- `content_plan_status`: str = 'outline_generated'\n"
                "\n"
                "### 执行流程\n"
                "1. 读取 search_results（如有）与素材\n"
                "2. 按 source_type 策略构造 LLM prompt\n"
                "3. call_llm 生成大纲 Markdown\n"
                "4. write_file 落盘 `{output_dir}/outline.md`\n"
                "\n"
                "### 失败兜底\n"
                "- LLM 生成空内容: raise ContentPlanError\n"
                "- write_file 失败: raise ContentPlanError\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        await _run_p43_outline_gen(self, inputs)
        return inputs


class P44ValidateNode(PlanNode):
    """P4.4 — 产物校验：outline.md 结构完整；搜索模式下校验已搜索来源章节。"""

    def __init__(self) -> None:
        super().__init__(
            plan_name="p4_4_validate",
            instruction=(
                "## P4.4 产物校验\n"
                "\n"
                "### 节点职责\n"
                "读取 outline.md → 规则校验结构与内容完整性。\n"
                "\n"
                "### 前置条件\n"
                "- P4.3 已完成，`outline_path` 指向已落盘的 outline.md\n"
                "- `read_file` 工具可用\n"
                "\n"
                "### 输入\n"
                "- `outline_path`（必填）: outline.md 文件路径\n"
                "- `search_mode`（可选）: 搜索模式下需校验已搜索来源章节\n"
                "\n"
                "### 输出\n"
                "校验通过时：\n"
                "- `p4_validate_status`: str = 'passed'\n"
                "- `content_plan_status`: str = 'completed'\n"
                "\n"
                "校验失败时：\n"
                "- raise ContentPlanError，触发 P4 整体重试\n"
                "\n"
                "### 执行流程\n"
                "1. read_file 读取 outline.md\n"
                "2. 校验结构标记：`# 大纲：`、`## 页面规划`\n"
                "3. 校验 ✅ 页研究查询 / 数据需求\n"
                "4. 搜索模式下校验 `## 已搜索来源` 章节\n"
                "\n"
                "### 失败兜底\n"
                "- outline.md 不存在或为空: raise ContentPlanError\n"
                "- 结构不完整: raise ContentPlanError，触发 P4 从 P4.1 重跑（最多 1 次重试）\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        await _run_p44_validate(self, inputs)
        return inputs


class ContentPlanNode(PlanNode):
    """P4 — 内容策划（P4.1 → P4.2 → P4.3 → P4.4）。

    预期输入（ctx / inputs，应由 P0/P2/P3 就绪）:
        必填: topic, page_count, audience, search_mode, source_type, output_dir
        可选: presentation_purpose, doc_raw_path, has_documents, doc_parse_ok
        可选: failure_reason — P4 整体重试时附带

    预期输出（P4.1 完成后写入）:
        has_source_material, source_material_chars, material_richness, focus_areas
        p4_should_search, p4_search_reason, content_plan_status

    预期输出（P4.2 完成后追加）:
        search_results, p4_search_queries, p4_search_hit_count
        p4_quick_research_status（completed | skipped）

    预期输出（P4.3 完成后追加）:
        outline_path, p4_outline_gen_status, content_plan_status=outline_generated

    预期输出（P4.4 校验通过后）:
        p4_validate_status=passed, content_plan_status=completed

    重试：P4 整体最多 2 次（初始 + 1 次重试，从 P4.1 重跑），失败时写入 failure_reason。
    """

    def __init__(self) -> None:
        super().__init__(
            plan_name="p4_content_plan",
            instruction=(
                "## P4 内容策划\n"
                "\n"
                "### 节点职责\n"
                "1. 需求标准化与素材充裕度评估（P4.1）\n"
                "2. 条件化快速调研（P4.2）\n"
                "3. 生成大纲 outline.md（P4.3）\n"
                "4. 产物校验（P4.4）\n"
                "P4 整体最多 2 次尝试（初始 + 1 次重试，从 P4.1 重跑）。\n"
                "\n"
                "### 前置条件\n"
                "- P0/P2/P3 已完成，`topic`, `page_count`, `audience`, `search_mode`, `source_type`, `output_dir` 已就绪\n"
                "- `stream_llm` / `web_search` / `read_file` / `write_file` 工具可用\n"
                "\n"
                "### 输入\n"
                "- `topic`（必填）: PPT 主题\n"
                "- `page_count`（必填）: 页数\n"
                "- `audience`（必填）: 受众\n"
                "- `search_mode`（必填）: 搜索策略\n"
                "- `source_type`（必填）: 素材来源\n"
                "- `output_dir`（必填）: 工作目录\n"
                "- `presentation_purpose`（可选）: 演示目的\n"
                "- `doc_raw_path` / `has_documents` / `doc_parse_ok`（可选）: 文档相关\n"
                "- `failure_reason`（可选）: P4 整体重试时附带\n"
                "\n"
                "### 输出\n"
                "P4.1 完成后：\n"
                "- `has_source_material`, `source_material_chars`, `material_richness`, `focus_areas`\n"
                "- `p4_should_search`, `p4_search_reason`, `content_plan_status`\n"
                "\n"
                "P4.2 完成后追加：\n"
                "- `search_results`, `p4_search_queries`, `p4_search_hit_count`\n"
                "- `p4_quick_research_status`: completed | skipped\n"
                "\n"
                "P4.3 完成后追加：\n"
                "- `outline_path`: `{output_dir}/outline.md` 绝对路径\n"
                "- `p4_outline_gen_status`: 'completed'\n"
                "- `content_plan_status`: 'outline_generated'\n"
                "\n"
                "P4.4 校验通过后：\n"
                "- `p4_validate_status`: 'passed'\n"
                "- `content_plan_status`: 'completed'\n"
                "\n"
                "### 执行流程\n"
                "1. P4.1: 读取素材 → LLM 评估充裕度 → 计算 p4_should_search\n"
                "2. P4.2: p4_should_search=True 时并行 web_search，否则跳过\n"
                "3. P4.3: 按 source_type 策略 LLM 生成大纲 → write_file 落盘 outline.md\n"
                "4. P4.4: read_file 读取 outline.md → 规则校验结构与内容\n"
                "\n"
                "### 失败兜底\n"
                "- P4.4 校验失败: raise ContentPlanError，触发 P4 整体重试（最多 1 次重试）\n"
                "- 2 次均失败: 写入 failure_reason，由根节点决定后续处理\n"
            ),
            sub_plans=[
                P41NormalizeNode(),
                P42QuickResearchNode(),
                P43OutlineGenNode(),
                P44ValidateNode(),
            ],
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        ctx = inputs
        last_error: str | None = None

        for attempt in range(_P4_MAX_ATTEMPTS):
            if attempt:
                ctx["failure_reason"] = last_error or "P4 校验失败"
                ctx["p4_retry_count"] = attempt

            try:
                for subplan in self.sub_plans:
                    await self.execute_subplan(subplan, ctx)
                return ctx
            except ContentPlanError as exc:
                last_error = str(exc)
                ctx["p4_validate_status"] = "failed"
                if attempt + 1 >= _P4_MAX_ATTEMPTS:
                    ctx["failure_reason"] = last_error
                    raise

        raise ContentPlanError(last_error or "P4 失败")
