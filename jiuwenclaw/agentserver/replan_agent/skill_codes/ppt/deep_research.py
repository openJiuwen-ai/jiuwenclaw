from __future__ import annotations

import asyncio
import logging
import re
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

from openjiuwen.core.runner.callback import AbortError

from jiuwenclaw.agentserver.replan_agent.plan_node import PlanNode
from jiuwenclaw.agentserver.replan_agent.skill_codes.ppt.ppt_common import PptCommon

logger = logging.getLogger(__name__)

_MAX_SEARCH_ROUNDS = 1
_MAX_BACKFILL_ROUNDS = 1
_MIN_SOURCES_PER_PAGE = 3
_MIN_KEY_FINDINGS = 3
_MIN_DATA_POINTS = 5
_MIN_DATA_TYPES = 2
_MIN_TIMEPOINTS = 3
_MIN_COMPARE_OBJECTS = 2
_MIN_COMPARE_DIMS = 2

_WORD_COUNT_MAP = {"L1": 1200, "L2": 2000, "L3": 3500}
_WORD_COUNT_NO_SEARCH_MAP = {"L1": 800, "L2": 1200, "L3": 2000}

_PAGE_HEADER_RE = re.compile(r"^###\s*P(\d+)\s*[:：]", re.MULTILINE)
_RESEARCH_QUERY_RE = re.compile(r"研究查询[：:]\s*(.+)", re.IGNORECASE)
_DATA_NEED_RE = re.compile(r"数据需求[：:]\s*(.+)", re.IGNORECASE)
_PAGE_TYPE_RE = re.compile(r"类型[：:]\s*(\w+)", re.IGNORECASE)
_SEARCHED_SOURCES_RE = re.compile(r"^##\s*已搜索来源", re.MULTILINE)
_URL_RE = re.compile(r"https?://[^\s\])>\"']+")


@dataclass
class _ResearchConfig:
    """封装 _write_research 所需的配置参数，避免函数签名过长。"""
    search_mode: str
    research_depth: str
    topic: str
    no_data_fallback: bool = False


class DeepResearchNode(PlanNode):
    def __init__(self) -> None:
        super().__init__(
            plan_name="p6_deep_research",
            instruction=(
                "## P6 深度研究（根节点）\n"
                "\n"
                "### 前置条件\n"
                "- `{output_dir}/outline.md` 存在且非空\n"
                "- `read_file` 工具可用\n"
                "\n"
                "### 输入\n"
                "- `output_dir`: 工作目录（读 outline.md，写 research.md）\n"
                "- `search_mode`: no_search / auto / force_search\n"
                "- `research_depth`: L1 / L2 / L3\n"
                "- `source_material`: 用户素材（可空）\n"
                "- `topic`: PPT 主题\n"
                "\n"
                "### 输出\n"
                "```json\n"
                '{\"research_path\": \"{output_dir}/research.md\"}\n'
                "```\n"
                "\n"
                "### 根节点职责（P6.1 解析与策略）\n"
                "1. 读取 `{output_dir}/outline.md`\n"
                "2. LLM 解析需要研究的页面（page_number / title / page_type / research_queries / data_needs），失败时正则回退\n"
                "3. 提取 outline 中已搜索的 URL（`searched_urls`，跳过重复搜索）\n"
                "4. 判断是否执行搜索（`_should_search`）：\n"
                "\n"
                "| search_mode | 素材状态 | 路径 |\n"
                "|---|---|---|\n"
                "| no_search | — | 跳过搜索（P6.2/6.3）→ 直接 P6.4 |\n"
                "| force_search | — | 完整流程 P6.2 → P6.3 → P6.4 |\n"
                "| auto | 素材充实（LLM 评估） | 跳过搜索 → 直接 P6.4 |\n"
                "| auto | 素材不足或为空 | 完整流程 P6.2 → P6.3 → P6.4 |\n"
                "\n"
                "### 子节点调用与数据流\n"
                "```\n"
                "P6.1（根节点内联）\n"
                "  pages, searched_urls, source_material\n"
                "    │\n"
                "    ├─ need_search=True ──► SearchFilterNode (P6.2)\n"
                "    │                        ├─ 输入: pages, searched_urls, source_material\n"
                "    │                        └─ 输出: page_sources\n"
                "    │                            │\n"
                "    │                            ▼\n"
                "    │                       FetchValidateNode (P6.3)\n"
                "    │                        ├─ 输入: pages, page_sources, research_depth\n"
                "    │                        └─ 输出: page_extractions\n"
                "    │                            │\n"
                "    └─ need_search=False ──┐    ▼\n"
                "                            ▼\n"
                "                       WriteResearchNode (P6.4)\n"
                "                        ├─ 输入: output_dir, pages, page_extractions,\n"
                "                        │       source_material, search_mode,\n"
                "                        │       research_depth, topic\n"
                "                        └─ 输出: research_path\n"
                "```\n"
                "\n"
                "### 失败兜底\n"
                "- outline.md 为空/不存在：返回 `{}`\n"
                "- 解析不到 ✅ 页面：返回 `{}`\n"
                "- need_search=False 且 source_material 为空/<200字：设置 `no_data_fallback=True`，"
                "P6.4 走代码模板生成大纲骨架（跳过 LLM 撰写和校验）\n"
                "- 子节点抛错：透传错误，不阻塞其他子节点\n"
                "\n"
                "### 子节点细节\n"
                "各子节点的工具调用、LLM 校验、回溯策略等详见对应 `instruction` 字段：\n"
                "- P6.2 `SearchFilterNode`：素材覆盖度评估 → 并行搜索 → 来源评分 → 缺口补搜\n"
                "- P6.3 `FetchValidateNode`：WebFetch 精准提取 → 幽灵识别 → 4 项数据充分性校验 → 定向回溯\n"
                "- P6.4 `WriteResearchNode`：LLM 撰写 research.md → 8 项产物校验 → 失败重写 1 次\n"
            ),
            sub_plans=[
                SearchFilterNode(),
                FetchValidateNode(),
                WriteResearchNode(),
            ],
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        output_dir = inputs.get("output_dir", "")
        outline_path = f"{output_dir}/outline.md" if output_dir else ""

        outline_text = await self._read_file(outline_path)
        if not outline_text:
            logger.warning("[P6] outline.md 为空或不存在，无法执行深度研究")
            return {}

        pages = await self._parse_outline_pages(outline_text)
        if not pages:
            logger.warning("[P6] 未从 outline.md 中解析到需要研究的页面")
            return {}

        search_mode = inputs.get("search_mode", "auto")
        research_depth = inputs.get("research_depth", "L2")
        source_material = inputs.get("source_material", "")
        searched_urls = self._extract_searched_urls(outline_text)
        need_search = await self._should_search(search_mode, source_material, pages)

        no_data_fallback = (
            not need_search
            and (not source_material or len(source_material.strip()) < 200)
        )
        if no_data_fallback:
            logger.warning(
                "[P6] 跳过搜索且无用户素材，进入无研究数据降级撰写 (search_mode=%s)",
                search_mode,
            )

        page_sources: dict[str, list[dict[str, Any]]] = {}
        page_extractions: dict[str, list[dict[str, Any]]] = {}

        if need_search:
            sub_inputs = {
                **inputs,
                "pages": pages,
                "searched_urls": searched_urls,
            }
            search_result = await self.execute_subplan(self.sub_plans[0], sub_inputs)
            page_sources = search_result.get("page_sources", {}) if isinstance(search_result, dict) else {}

            fetch_inputs = {
                **inputs,
                "pages": pages,
                "page_sources": page_sources,
            }
            fetch_result = await self.execute_subplan(self.sub_plans[1], fetch_inputs)
            page_extractions = fetch_result.get("page_extractions", {}) if isinstance(fetch_result, dict) else {}
        else:
            logger.info("[P6] 跳过搜索，直接撰写报告 (search_mode=%s)", search_mode)

        write_inputs = {
            **inputs,
            "pages": pages,
            "page_sources": page_sources,
            "page_extractions": page_extractions,
            "search_mode": search_mode,
            "research_depth": research_depth,
            "source_material": source_material,
            "no_data_fallback": no_data_fallback,
        }
        write_result = await self.execute_subplan(self.sub_plans[2], write_inputs)

        research_path = write_result.get("research_path", "") if isinstance(write_result, dict) else ""
        return {"research_path": research_path}

    async def _read_file(self, path: str) -> str:
        if not path:
            return ""
        if not self.has_tool("read_file"):
            logger.warning("[P6] read_file 工具不可用，无法读取文件 %s", path)
            return ""
        try:
            result = await self.call_tool("read_file", file_path=path)
            return PptCommon.parse_tool_file_content(result)
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P6] 读取文件失败 %s: %s", path, e)
            return ""

    async def _parse_outline_pages(self, outline_text: str) -> list[dict[str, Any]]:
        prompt = (
            "你是一个大纲解析助手。请从以下 PPT 大纲中提取所有研究需求为 ✅ 的页面信息。\n"
            "对每个页面，提取：\n"
            "- page_number: 页码（整数）\n"
            "- title: 页面标题\n"
            "- page_type: 页面类型（如 trend/data/case/comparison/technology 等）\n"
            "- research_queries: 研究查询列表（字符串数组）\n"
            "- data_needs: 数据需求列表（字符串数组）\n\n"
            "以 JSON 数组格式输出，不要输出其他内容。如果没有需要研究的页面，输出空数组 []。\n\n"
            f"大纲内容：\n{outline_text}"
        )
        result = await self.stream_llm_collect(prompt=prompt, system_prompt="只输出 JSON 数组，不要输出其他内容")
        try:
            pages = self.extract_json(result, expected_type=list)
            if isinstance(pages, list):
                return pages
        except (ValueError, TypeError):
            logger.warning("[P6] LLM 解析大纲页面失败，尝试正则回退")
        return self._parse_outline_pages_fallback(outline_text)

    def _parse_outline_pages_fallback(self, outline_text: str) -> list[dict[str, Any]]:
        pages = []
        for m in _PAGE_HEADER_RE.finditer(outline_text):
            page_num = int(m.group(1))
            start = m.end()
            next_m = _PAGE_HEADER_RE.search(outline_text, start)
            section = outline_text[start:next_m.start() if next_m else len(outline_text)]

            if "✅" not in section:
                continue

            title = section.split("\n")[0].strip().lstrip(":：").strip()
            page_type_m = _PAGE_TYPE_RE.search(section)
            page_type = page_type_m.group(1) if page_type_m else "data"

            queries = [m2.group(1).strip() for m2 in _RESEARCH_QUERY_RE.finditer(section)]
            data_needs = [m2.group(1).strip() for m2 in _DATA_NEED_RE.finditer(section)]

            pages.append({
                "page_number": page_num,
                "title": title,
                "page_type": page_type,
                "research_queries": queries,
                "data_needs": data_needs,
            })
        return pages

    def _extract_searched_urls(self, outline_text: str) -> list[str]:
        m = _SEARCHED_SOURCES_RE.search(outline_text)
        if not m:
            return []
        section = outline_text[m.end():]
        return [u for u in _URL_RE.findall(section)]

    async def _should_search(
        self,
        search_mode: str,
        source_material: str,
        pages: list[dict[str, Any]],
    ) -> bool:
        if search_mode == "no_search":
            return False
        if search_mode == "force_search":
            return True
        if not source_material or len(source_material.strip()) < 200:
            return True
        if search_mode == "auto":
            return await self._evaluate_material_sufficiency(source_material, pages)
        return True

    async def _evaluate_material_sufficiency(
        self,
        source_material: str,
        pages: list[dict[str, Any]],
    ) -> bool:
        research_needs = []
        for page in pages:
            queries = page.get("research_queries", [])
            data_needs = page.get("data_needs", [])
            if queries or data_needs:
                research_needs.append(
                    f"P{page['page_number']}({page.get('page_type', '')}): "
                    f"研究查询={queries}, 数据需求={data_needs}"
                )
        if not research_needs:
            return False

        needs_text = "\n".join(research_needs)
        material_preview = source_material[:3000]

        prompt = (
            "请判断用户素材是否足以支撑以下研究需求，无需额外搜索。\n\n"
            f"研究需求：\n{needs_text}\n\n"
            f"用户素材（前3000字）：\n{material_preview}\n\n"
            "判断标准：\n"
            "- 素材覆盖了大部分页面的研究查询和数据需求 → 回答 sufficient\n"
            "- 素材仅覆盖少部分页面，或数据需求明显缺失 → 回答 insufficient\n\n"
            "只输出 sufficient 或 insufficient，不要输出其他内容。"
        )
        try:
            result = await self.stream_llm_collect(
                prompt=prompt,
                system_prompt="你是一个素材充足性评估助手，只输出 sufficient 或 insufficient。",
            )
            decision = result.strip().lower()
            logger.info("[P6] 素材充足性评估结果: %s", decision)
            return decision != "sufficient"
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.warning("[P6] 素材充足性评估失败，默认需要搜索: %s", e)
            return True


class SearchFilterNode(PlanNode):
    def __init__(self) -> None:
        super().__init__(
            plan_name="p6_2_search_filter",
            instruction=(
                "## P6.2 检索与筛选（搜索模式）\n"
                "\n"
                "### 前置条件\n"
                "- `web_search` 工具可用\n"
                "- `pages` 列表非空（来自上游解析的需研究页面）\n"
                "\n"
                "### 输入\n"
                "- `pages`: 需要研究的页面列表，每页含 research_queries、data_needs\n"
                "- `searched_urls`: outline.md 中已搜索的 URL，跳过搜索直接入候选池\n"
                "- `source_material`: 用户素材（如有），用于搜索缩减\n"
                "\n"
                "### 执行流程\n"
                "1. **素材覆盖度评估**（有素材时）：用 LLM 逐页评估素材对各页数据需求的覆盖程度（covered/partial/uncovered），并输出未覆盖的数据需求列表\n"
                "2. **按覆盖度生成搜索查询**：\n"
                "   - covered 页：仅 1 次验证性搜索\n"
                "   - partial 页：仅搜索未覆盖的数据需求 + 1 次验证性搜索\n"
                "   - uncovered 页：完整搜索（所有 research_queries + data_needs）\n"
                "3. **并行搜索**：所有查询一次性并行发出，禁止串行\n"
                "4. **来源评分筛选**：用 LLM 对搜索结果做可信度评分（A+/A/A-/B+/B/C），C 级来源当场丢弃\n"
                "5. **已有 URL 合并**：searched_urls 直接加入所有页面的候选池\n"
                "6. **缺口检查**：某页合格来源 <3 个则标记为缺口页\n"
                "7. **定向补搜**（最多1轮）：对缺口页生成补充查询（加 report/白皮书/官方 限定词），并行搜索后归并结果\n"
                "\n"
                "### 来源可信度评分标准\n"
                "| 等级 | 分数 | 来源类型 |\n"
                "|---|---|---|\n"
                "| A+ | 90-100 | 权威机构（政府、国际组织） |\n"
                "| A | 80-89 | 企业官方（年报、财报） |\n"
                "| A- | 70-79 | 学术论文 |\n"
                "| B+ | 65-69 | 权威媒体 |\n"
                "| B | 60-64 | 行业媒体 |\n"
                "| C | <60 | 自媒体/内容农场（排除） |\n"
                "\n"
                "### 排除条件\n"
                "纯观点无数据、来源不明的二手转述、商业推广、可信度 <60\n"
                "\n"
                "### 缺口补搜策略\n"
                "| 缺口类型 | 判定条件 | 补搜策略 |\n"
                "|---|---|---|\n"
                "| 数据需求缺口 | 某条数据需求无来源覆盖 | 针对该需求生成精准查询 |\n"
                "| 来源类型偏斜 | 某页全部为媒体来源 | 加 report/白皮书/官方 限定词 |\n"
                "| 页面来源不足 | 某页合格来源 <3 个 | 换同义词、加英文查询 |\n"
                "\n"
                "### 输出\n"
                "返回 `page_sources`：每页的合格候选 URL 清单\n"
                "```json\n"
                '{"page_sources": {"1": [{"url": "...", "title": "..."}], "2": [...]}}\n'
                "```\n"
                "\n"
                "### 失败兜底\n"
                "- web_search 不可用：返回空 page_sources\n"
                "- 素材覆盖度评估失败：按无素材处理（所有页面 uncovered）\n"
                "- 来源评分失败：保留全部来源\n"
                "- 补搜失败：保留已有来源，不重试\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        pages = inputs.get("pages", [])
        searched_urls = inputs.get("searched_urls", [])
        source_material = inputs.get("source_material", "")

        if not self.has_tool("web_search"):
            logger.warning("[P6.2] web_search 工具不可用，跳过搜索")
            return {"page_sources": {}}

        page_coverage = {}
        if source_material:
            page_coverage = await self._evaluate_page_coverage(pages, source_material)

        page_sources = await self._search_and_filter(pages, searched_urls, page_coverage)
        return {"page_sources": page_sources}

    async def _evaluate_page_coverage(
        self,
        pages: list[dict[str, Any]],
        source_material: str,
    ) -> dict[str, dict[str, Any]]:
        page_descriptions = []
        for page in pages:
            page_descriptions.append(
                f"P{page['page_number']}({page.get('page_type', '')}): "
                f"研究查询={page.get('research_queries', [])}, "
                f"数据需求={page.get('data_needs', [])}"
            )
        pages_text = "\n".join(page_descriptions)
        material_preview = source_material[:3000]

        prompt = (
            "请逐页评估用户素材对各页面研究需求的覆盖程度。\n\n"
            f"页面研究需求：\n{pages_text}\n\n"
            f"用户素材（前3000字）：\n{material_preview}\n\n"
            "对每个页面输出：\n"
            "- coverage: covered（素材已覆盖大部分数据需求）/ partial（部分覆盖）/ uncovered（完全未覆盖）\n"
            "- uncovered_needs: 素材未覆盖的数据需求列表（covered 为空数组，partial 列出未覆盖项，uncovered 列出全部）\n\n"
            '以 JSON 对象格式输出，key 为页码字符串。\n'
            '例如：{"1": {"coverage": "covered", "uncovered_needs": []}, '
            '"2": {"coverage": "partial", "uncovered_needs": ["2024年AI市场规模", "CAGR数据"]}, '
            '"3": {"coverage": "uncovered", "uncovered_needs": ["全部数据需求"]}}\n'
            "只输出 JSON，不要输出其他内容。"
        )
        try:
            result = await self.stream_llm_collect(
                prompt=prompt,
                system_prompt="你是一个素材覆盖度评估助手，只输出 JSON 对象。",
            )
            raw = self.extract_json(result, expected_type=dict)
            if isinstance(raw, dict):
                coverage_map: dict[str, dict[str, Any]] = {}
                for k, v in raw.items():
                    if isinstance(v, dict):
                        coverage_map[str(k)] = {
                            "coverage": str(v.get("coverage", "uncovered")),
                            "uncovered_needs": v.get("uncovered_needs", []),
                        }
                    else:
                        coverage_map[str(k)] = {
                            "coverage": str(v),
                            "uncovered_needs": [],
                        }
                logger.info("[P6.2] 素材覆盖度评估: %s", coverage_map)
                return coverage_map
        except (ValueError, TypeError) as e:
            logger.warning("[P6.2] 素材覆盖度评估失败，按无素材处理: %s", e)
        return {}

    async def _search_and_filter(
        self,
        pages: list[dict[str, Any]],
        existing_urls: list[str],
        page_coverage: dict[str, dict[str, Any]],
    ) -> dict[str, list[dict[str, Any]]]:
        queries = []
        for page in pages:
            page_key = str(page["page_number"])
            cov_info = page_coverage.get(page_key, {})
            coverage = cov_info.get("coverage", "uncovered") if isinstance(cov_info, dict) else "uncovered"
            uncovered_needs = cov_info.get("uncovered_needs", []) if isinstance(cov_info, dict) else []
            research_queries = page.get("research_queries", [])
            data_needs = page.get("data_needs", [])

            if coverage == "covered":
                for q in research_queries[:1]:
                    queries.append({"page": page["page_number"], "query": q})
            elif coverage == "partial":
                for need in uncovered_needs:
                    queries.append({"page": page["page_number"], "query": str(need)})
                for q in research_queries[:1]:
                    queries.append({"page": page["page_number"], "query": q})
            else:
                # uncovered：合并查询，每页最多2个查询（1个研究查询 + 1个数据需求综合查询）
                # 避免每个数据需求单独搜索导致搜索次数爆炸
                page_type = str(page.get("page_type", page.get("type", ""))).lower()
                need_comparison = any(
                    kw in page_type for kw in ("comparison", "technology", "data", "trend")
                )
                if research_queries:
                    queries.append({"page": page["page_number"], "query": research_queries[0]})
                if data_needs:
                    # 将多个数据需求合并为一个综合查询
                    combined_needs = " ".join(str(d) for d in data_needs[:3])
                    # 对需要对比数据的页面类型追加对比关键词，提升结构化表格命中率
                    if need_comparison:
                        combined_needs = f"{combined_needs} 对比 排名 参数"
                    queries.append({"page": page["page_number"], "query": combined_needs})

        # 跨页查询去重：相同查询只搜索一次，结果共享给所有相关页面
        seen_queries: dict[str, list[int]] = {}
        unique_queries: list[dict[str, Any]] = []
        for item in queries:
            q = item["query"].strip()
            if q not in seen_queries:
                seen_queries[q] = [item["page"]]
                unique_queries.append({"query": q, "pages": [item["page"]]})
            else:
                if item["page"] not in seen_queries[q]:
                    seen_queries[q].append(item["page"])

        if not unique_queries:
            return {}

        logger.info(
            "[P6.2] 查询合并去重: 原始 %d → 去重后 %d",
            len(queries), len(unique_queries),
        )

        search_tasks = [
            self.call_tool("web_search", query=item["query"], search_mode="default")
            for item in unique_queries
        ]
        results = await asyncio.gather(*search_tasks, return_exceptions=True)

        page_sources: dict[str, list[dict[str, Any]]] = {}
        for item, result in zip(unique_queries, results):
            page_nums = item["pages"]
            if isinstance(result, Exception):
                logger.warning("[P6.2] 搜索失败 query=%s: %s", item["query"][:50], result)
                continue
            if isinstance(result, str) and result.startswith("[ERROR]"):
                continue
            sources = self._parse_search_results(result)
            for page_num in page_nums:
                page_sources.setdefault(str(page_num), []).extend(sources)

        for url in existing_urls:
            for page_key in page_sources:
                page_sources[page_key].append({"url": url, "from_existing": True})

        page_sources = await self._score_and_filter_sources(page_sources)

        gap_pages = self._find_gap_pages(pages, page_sources)
        if gap_pages:
            page_sources = await self._backfill_search(gap_pages, page_sources)

        return page_sources

    def _parse_search_results(self, raw: str) -> list[dict[str, Any]]:
        sources = []
        for m in _URL_RE.finditer(raw):
            url = m.group(0)
            start = max(0, m.start() - 100)
            context = raw[start:m.start()]
            title = context.split("\n")[-1].strip().lstrip("-•* ").strip()
            sources.append({"url": url, "title": title})
        return sources

    async def _score_and_filter_sources(
        self,
        page_sources: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[dict[str, Any]]]:
        seen_urls: set[str] = set()
        all_sources = []
        for sources in page_sources.values():
            for source in sources:
                if not source.get("from_existing") and source.get("url"):
                    url = source["url"]
                    if url not in seen_urls:
                        seen_urls.add(url)
                        all_sources.append(source)

        if not all_sources:
            return page_sources

        logger.info("[P6.2] URL 去重后待评分来源数: %d", len(all_sources))

        source_list_text = "\n".join(
            f"{i+1}. {s.get('title', '')} — {s.get('url', '')}"
            for i, s in enumerate(all_sources)
        )

        prompt = (
            "请对以下搜索结果来源进行可信度评分。\n\n"
            f"来源列表：\n{source_list_text}\n\n"
            "评分标准：\n"
            "- A+ (90-100)：权威机构（政府、国际组织）\n"
            "- A (80-89)：企业官方（年报、财报）\n"
            "- A- (70-79)：学术论文\n"
            "- B+ (65-69)：权威媒体\n"
            "- B (60-64)：行业媒体\n"
            "- C (<60)：自媒体/内容农场（排除）\n\n"
            "排除条件：纯观点无数据、来源不明的二手转述、商业推广、可信度 <60。\n\n"
            '以 JSON 对象输出，key 为来源序号（数字字符串，从1开始），value 为评分等级（A+/A/A-/B+/B/C）。\n'
            '例如：{"1": "A", "2": "C", "3": "B+"}\n'
            "只输出 JSON 对象，不要输出其他内容，不要输出原始URL。"
        )
        try:
            result = await self.stream_llm_collect(
                prompt=prompt,
                system_prompt="你是来源可信度评估助手，只输出 JSON 对象。",
            )
            raw_scores = self.extract_json(result, expected_type=dict)
            if not isinstance(raw_scores, dict):
                return page_sources

            url_scores: dict[str, str] = {}
            for key, grade in raw_scores.items():
                try:
                    idx = int(str(key).strip()) - 1
                except (ValueError, TypeError):
                    continue
                if 0 <= idx < len(all_sources):
                    url_scores[all_sources[idx]["url"]] = str(grade).strip().upper()

            grade_order = {"A+": 0, "A": 1, "A-": 2, "B+": 3, "B": 4}

            filtered: dict[str, list[dict[str, Any]]] = {}
            for page_key, sources in page_sources.items():
                kept = []
                for s in sources:
                    if s.get("from_existing"):
                        s["grade"] = "existing"
                        kept.append(s)
                        continue
                    url = s.get("url", "")
                    grade = url_scores.get(url, "")
                    if grade in grade_order:
                        s["grade"] = grade
                        kept.append(s)
                kept.sort(key=lambda x: grade_order.get(x.get("grade", ""), 99))
                filtered[page_key] = kept

            logger.info("[P6.2] 来源评分完成，合格 %d / %d", len(url_scores), len(all_sources))
            return filtered
        except (ValueError, TypeError) as e:
            logger.warning("[P6.2] 来源评分失败，保留全部来源: %s", e)
            return page_sources

    def _find_gap_pages(
        self,
        pages: list[dict[str, Any]],
        page_sources: dict[str, list[dict[str, Any]]],
    ) -> list[dict[str, Any]]:
        gap_pages = []
        for page in pages:
            page_key = str(page["page_number"])
            sources = page_sources.get(page_key, [])
            if len(sources) < _MIN_SOURCES_PER_PAGE:
                gap_pages.append(page)
        return gap_pages

    async def _backfill_search(
        self,
        gap_pages: list[dict[str, Any]],
        page_sources: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[dict[str, Any]]]:
        if not self.has_tool("web_search"):
            return page_sources

        # 回填搜索：每页只生成1个综合查询，避免查询数膨胀
        backfill_queries = []
        for page in gap_pages:
            research_queries = page.get("research_queries", [])
            data_needs = page.get("data_needs", [])
            # 优先用研究查询 + "报告 白皮书"，无则用首个数据需求 + "官方数据"
            if research_queries:
                backfill_queries.append({
                    "page": page["page_number"],
                    "query": f"{research_queries[0]} report 白皮书",
                })
            elif data_needs:
                backfill_queries.append({
                    "page": page["page_number"],
                    "query": f"{data_needs[0]} 官方数据",
                })

        if not backfill_queries:
            return page_sources

        tasks = [
            self.call_tool("web_search", query=item["query"], search_mode="default")
            for item in backfill_queries
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        for item, result in zip(backfill_queries, results):
            page_key = str(item["page"])
            if isinstance(result, Exception):
                continue
            if isinstance(result, str) and result.startswith("[ERROR]"):
                continue
            sources = self._parse_search_results(result)
            page_sources.setdefault(page_key, []).extend(sources)

        return page_sources


class FetchValidateNode(PlanNode):
    def __init__(self) -> None:
        super().__init__(
            plan_name="p6_3_fetch_validate",
            instruction=(
                "## P6.3 深度抓取与验证（搜索模式）\n"
                "\n"
                "### 前置条件\n"
                "- `fetch_webpage` 工具可用（不可用时直接返回空 page_extractions）\n"
                "- `page_sources` 非空（来自上游检索筛选的合格候选 URL，已按可信度评级排序）\n"
                "\n"
                "### 输入\n"
                "- `pages`: 需要研究的页面列表（每页含 page_number / type / title / data_needs / research_queries）\n"
                "- `page_sources`: 每页的合格候选 URL 清单（已评分筛选，按 A+ > A > A- > B+ > B 排序）\n"
                "- `research_depth`: L1 / L2 / L3，决定交叉验证阈值\n"
                "\n"
                "### 执行流程（5 阶段，顺序严格）\n"
                "\n"
                "#### 阶段 1：来源筛选 + 并行抓取\n"
                "- 每页取 `page_sources[:2]` 作为 top_sources（评级最高 2 个，B+ 及以上优先）\n"
                "- 优先级隐含在评级中：权威机构报告 > 白皮书/论文 > 知名媒体深度报道 > 官方数据 > 企业官方\n"
                "- 调用 `fetch_webpage(url=..., prompt=..., max_chars=8000, timeout_seconds=8)` 并行抓取\n"
                "- **必须传 `prompt` 参数**，目标 500-800 tokens/篇，禁止返回全文\n"
                "- 单 URL 失败跳过，不阻塞其他\n"
                "\n"
                "#### 阶段 2：幽灵来源识别（LLM 判断）\n"
                "对每页抓取结果调用 LLM，识别以下 6 类幽灵来源（返回应排除的序号）：\n"
                "1. 无URL或URL明显无效\n"
                "2. DOI不匹配（DOI链接指向的内容与标题/预期不符）\n"
                "3. 标题/年份与内容矛盾\n"
                "4. 无法回溯的二手转述\n"
                "5. 引用来源与页面数据需求领域不符\n"
                "6. 发布时间异常（>2 年旧信息，非经典案例除外）\n"
                "\n"
                "#### 阶段 3：数据充分性校验（4 项，LLM 判断）\n"
                "对每页幸存来源调用 LLM 校验：\n"
                "1. 证据密度：≥3 条 key_findings 且 ≥5 条关键数据点\n"
                "2. 数据类型覆盖：≥2 种（绝对值/百分比/排名/增长率）\n"
                "3. 时序/对比：trend/data/comparison/technology 页须有结构化数据（时序 ≥3 时间点；对比 ≥2 对象 × ≥2 维度）\n"
                "4. 交叉验证：L3 ≥3 源 / L2 ≥2 源（单源标注）/ L1 标注单源\n"
                "\n"
                "**校验返回受控词汇**（`missing` 字段必须从以下选取）：\n"
                "- `\"证据密度不足\"` / `\"数据类型单一\"` / `\"缺时序数据\"` / `\"缺对比数据\"` / `\"来源不足\"`\n"
                "\n"
                "#### 阶段 4：定向回溯（最多 1 轮）\n"
                "对每个缺口页：\n"
                "1. 优先从 `page_sources[2:4]`（评级次高的备选来源）补抓\n"
                "2. 候选池不足时，按 missing 类别生成定向查询调 `web_search`：\n"
                "   - `缺时序数据` → `{topic} 历年数据 趋势`\n"
                "   - `缺对比数据` → `{topic} 对比 排名`\n"
                "   - `数据类型单一` → `{topic} 统计数据 报告`\n"
                "   - `证据密度不足` → `{topic} 白皮书 研究报告`\n"
                "   - `来源不足` → `{topic} 官方报告 权威数据`\n"
                "3. 新 URL 走阶段 1 同样的抓取流程（带 prompt 参数）\n"
                "\n"
                "#### 阶段 5：仍不通过则标注\n"
                "回溯后再校验，仍不通过的页面在 page_extractions 中插入：\n"
                "```json\n"
                '{"url": "", "content": "[数据有限] 该页面研究素材不足，需降级撰写", "data_limited": true}\n'
                "```\n"
                "\n"
                "### WebFetch prompt 构造规则\n"
                "```\n"
                "从本文提取关于「{该页 data_needs 拼接}」的信息，仅输出以下结构化内容，禁止输出全文或无关内容：\n"
                "1. 关键事实（具体数据点、统计数字，带年份）\n"
                "2. 核心观点（1-2句结论性陈述）\n"
                "3. 案例信息（具体公司/产品/实施情况，含名称）\n"
                "4. 时序数据（如有：格式为\"指标：2023年=X，2024年=Y，2025年=Z\"）\n"
                "5. 对比数据（如有：格式为\"对象A=X，对象B=Y\"）\n"
                "6. 原始来源（数据出处和发布时间）\n"
                "如文中无相关数据，输出\"本文无相关数据\"即可。\n"
                "```\n"
                "\n"
                "### 输出\n"
                "返回 `page_extractions`：\n"
                "```json\n"
                '{"page_extractions": {\n'
                '  "1": [{"url": "https://...", "content": "..."}],\n'
                '  "2": [{"url": "", "content": "[数据有限] ...", "data_limited": true}]\n'
                "}}\n"
                "```\n"
                "\n"
                "### 失败兜底\n"
                "- fetch_webpage 不可用：直接返回 `{}`\n"
                "- 单个 URL 抓取异常：日志记录，跳过该 URL\n"
                "- LLM 幽灵识别失败：保留全部来源，不过滤\n"
                "- LLM 校验失败：保守视为缺口，进入回溯\n"
                "- web_search 不可用或补搜失败：仅用候选池剩余 URL 回溯\n"
                "- 回溯后仍不通过：标注 `data_limited: true`，传递给后续撰写节点降级处理\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        pages = inputs.get("pages", [])
        page_sources = inputs.get("page_sources", {})
        research_depth = inputs.get("research_depth", "L2")

        page_extractions = await self._fetch_and_validate(pages, page_sources, research_depth)
        return {"page_extractions": page_extractions}

    async def _fetch_and_validate(
        self,
        pages: list[dict[str, Any]],
        page_sources: dict[str, list[dict[str, Any]]],
        research_depth: str = "L2",
    ) -> dict[str, list[dict[str, Any]]]:
        if not self.has_tool("fetch_webpage"):
            logger.warning("[P6.3] fetch_webpage 工具不可用，跳过深度抓取")
            return {}

        page_extractions = await self._batch_fetch(pages, page_sources, research_depth=research_depth)

        page_extractions = await self._identify_ghost_sources(pages, page_extractions)

        gap_pages_with_reason = await self._validate_data_sufficiency(pages, page_extractions, research_depth)
        if gap_pages_with_reason:
            page_extractions = await self._backfill_fetch(
                gap_pages_with_reason, page_sources, page_extractions, research_depth
            )

        return page_extractions

    async def _batch_fetch(
        self,
        pages: list[dict[str, Any]],
        page_sources: dict[str, list[dict[str, Any]]],
        extra_urls: dict[str, list[str]] | None = None,
        research_depth: str = "L2",
    ) -> dict[str, list[dict[str, Any]]]:
        # 按研究深度动态调整每页抓取数：L1=2, L2=3, L3=4
        # 保证 ≥2 独立来源，留出冗余应对幽灵来源排除与交叉验证
        top_n = {"L1": 2, "L2": 3, "L3": 4}.get(research_depth, 3)

        fetch_tasks = []
        fetch_map: list[dict[str, Any]] = []

        for page in pages:
            page_key = str(page["page_number"])
            sources = page_sources.get(page_key, [])
            top_sources = sources[:top_n]

            if extra_urls and extra_urls.get(page_key):
                existing_top_urls = {s.get("url") for s in top_sources}
                for url in extra_urls[page_key]:
                    if url not in existing_top_urls:
                        top_sources.append({"url": url})

            for source in top_sources:
                url = source.get("url", "")
                if not url:
                    continue
                fetch_tasks.append(
                    self.call_tool(
                        "fetch_webpage",
                        url=url,
                        max_chars=8000,
                        timeout_seconds=8,
                    )
                )
                fetch_map.append({"page_key": page_key, "url": url})

        if not fetch_tasks:
            return {}

        results = await asyncio.gather(*fetch_tasks, return_exceptions=True)

        page_extractions: dict[str, list[dict[str, Any]]] = {}
        for meta, result in zip(fetch_map, results):
            page_key = meta["page_key"]
            if isinstance(result, Exception):
                logger.warning("[P6.3] WebFetch 失败 url=%s: %s", meta["url"][:80], result)
                continue
            if isinstance(result, str) and result.startswith("[ERROR]"):
                continue
            page_extractions.setdefault(page_key, []).append({
                "url": meta["url"],
                "content": str(result),
            })

        return page_extractions

    async def _identify_ghost_sources(
        self,
        pages: list[dict[str, Any]],
        page_extractions: dict[str, list[dict[str, Any]]],
    ) -> dict[str, list[dict[str, Any]]]:
        page_needs: dict[str, str] = {}
        for page in pages:
            pk = str(page["page_number"])
            needs = "；".join(page.get("data_needs", []))
            page_needs[pk] = needs or page.get("title", "")

        async def _check_one_ghost(
            page_key: str,
            extractions: list[dict[str, Any]],
        ) -> tuple[str, list[dict[str, Any]]]:
            if not extractions:
                return (page_key, extractions)

            needs_desc = page_needs.get(page_key, "")
            source_list = "\n".join(
                f"{i+1}. URL: {e.get('url', '')}\n   内容摘要: {e.get('content', '')[:200]}"
                for i, e in enumerate(extractions)
            )
            prompt = (
                "请识别以下来源中的幽灵来源（不可靠/虚假来源），返回应排除的序号列表。\n\n"
                f"页面数据需求：{needs_desc}\n\n"
                f"来源列表：\n{source_list}\n\n"
                "幽灵来源特征：\n"
                "1. 无URL或URL明显无效\n"
                "2. DOI不匹配（DOI链接指向的内容与标题/预期不符）\n"
                "3. 标题/年份与内容矛盾（如标题说2024但内容是2021数据）\n"
                "4. 无法回溯的二手转述（如\"据XX报道\"但无原始链接）\n"
                "5. 引用来源与页面数据需求领域不符\n"
                "6. 发布时间异常（>2年旧信息，非经典案例）\n\n"
                '以 JSON 数组输出应排除的序号（从1开始），无需排除则输出 []。\n'
                "只输出 JSON 数组，不要输出其他内容。"
            )
            try:
                result = await self.stream_llm_collect(
                    prompt=prompt,
                    system_prompt="你是来源可靠性验证助手，只输出 JSON 数组。",
                )
                exclude_indices = self.extract_json(result, expected_type=list)
                if isinstance(exclude_indices, list):
                    exclude_set = set(int(i) - 1 for i in exclude_indices if isinstance(i, (int, float)))
                    verified = [e for i, e in enumerate(extractions) if i not in exclude_set]
                    return (page_key, verified)
                else:
                    return (page_key, extractions)
            except (ValueError, TypeError) as e:
                logger.warning("[P6.3] 幽灵来源LLM识别失败，保留全部: %s", e)
                return (page_key, extractions)

        tasks = [_check_one_ghost(pk, ext) for pk, ext in page_extractions.items()]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        filtered: dict[str, list[dict[str, Any]]] = {}
        for r in results:
            if isinstance(r, Exception):
                logger.warning("[P6.3] 幽灵来源识别任务异常: %s", r)
                continue
            page_key, verified = r
            filtered[page_key] = verified

        return filtered

    async def _validate_data_sufficiency(
        self,
        pages: list[dict[str, Any]],
        page_extractions: dict[str, list[dict[str, Any]]],
        research_depth: str = "L2",
        strict: bool = False,
    ) -> list[tuple[dict[str, Any], list[str]]]:
        # 交叉验证所需最小独立来源数（校验项4 直接据此二元判断）
        min_sources = {"L3": 3, "L2": 2, "L1": 1}.get(research_depth, 2)

        # 校验标准分级：首次宽松（避免无效回填），回填后严格（保证最终质量）
        if strict:
            density_rule = "每页 ≥3 条 key_findings 且关键数据点 ≥5 条"
            type_rule = "≥2 种数据类型（绝对值/百分比/排名/增长率）"
            density_missing = '"证据密度不足"：key_findings <3 或 关键数据点 <5'
            type_missing = '"数据类型单一"：仅有 1 种数据类型（绝对值/百分比/排名/增长率）'
            strict_label = "严格（回填后二次校验）"
        else:
            density_rule = "每页 ≥2 条 key_findings 且关键数据点 ≥3 条"
            type_rule = "≥1 种数据类型（绝对值/百分比/排名/增长率）"
            density_missing = '"证据密度不足"：key_findings <2 或 关键数据点 <3'
            type_missing = '"数据类型单一"：未提取到任何数据类型（绝对值/百分比/排名/增长率）'
            strict_label = "宽松（首次校验）"

        async def _check_one_page(
            page: dict[str, Any],
        ) -> tuple[dict[str, Any], list[str]] | None:
            page_key = str(page["page_number"])
            extractions = page_extractions.get(page_key, [])
            if not extractions:
                return (page, ["无任何抓取内容"])

            combined = self._compose_validation_content(extractions)
            page_type = page.get("type", "")
            data_needs = "；".join(page.get("data_needs", []))
            source_count = len({e.get("url", "") for e in extractions if e.get("url")})

            prompt = (
                "请校验以下抓取内容的数据充分性，仅输出 JSON。\n\n"
                "【判断纪律】\n"
                "- 逐项快速判断，每项给出结论后不再回溯，禁止反复质疑\n"
                "- 存在歧义时一律按\"通过\"处理，避免过度思考\n"
                "- 简洁推理，直接给结论\n\n"
                f"页面类型：{page_type}\n"
                f"数据需求：{data_needs}\n"
                f"研究深度：{research_depth}\n"
                f"独立来源数：{source_count}（当前已抓取的不同URL数量，直接用于校验项4判断）\n"
                f"校验严格度：{strict_label}\n\n"
                f"抓取内容：\n{combined}\n\n"
                "校验项（4 项，均为二元判断，是→通过）：\n"
                f"1. 证据密度：{density_rule}\n"
                f"2. 数据类型覆盖：{type_rule}\n"
                "3. 时序/对比数据：trend/data 页需有≥3时间点的时序数据；"
                "comparison/technology 页需有任意结构化表格（≥2行×≥2列即可，"
                "不要求表格对象与数据需求匹配）\n"
                f"4. 交叉验证：独立来源数≥{min_sources}（直接按上方独立来源数判断）\n\n"
                '输出 JSON：{"pass": true/false, "missing": ["缺失类别1", "缺失类别2"]}\n'
                "missing 字段必须从以下受控词汇表中选取（可多选）：\n"
                f"- {density_missing}\n"
                f"- {type_missing}\n"
                "- \"缺时序数据\"：trend/data 页未提取到 ≥3 时间点\n"
                "- \"缺对比数据\"：comparison/technology 页未提取到任意结构化表格（≥2行×≥2列）\n"
                "- \"来源不足\"：独立来源数 < " + str(min_sources) + "\n"
                "通过则输出 missing: []。\n"
                "只输出 JSON，不要输出其他内容。"
            )
            try:
                result = await self.stream_llm_collect(
                    prompt=prompt,
                    system_prompt="你是数据充分性校验助手，只输出 JSON。",
                )
                check = self.extract_json(result, expected_type=dict)
                if isinstance(check, dict) and not check.get("pass", False):
                    raw_missing = check.get("missing", [])
                    if isinstance(raw_missing, list):
                        missing = [str(m) for m in raw_missing]
                    elif isinstance(raw_missing, str) and raw_missing:
                        missing = [raw_missing]
                    else:
                        missing = ["未说明"]
                    logger.info(
                        "[P6.3] 页面 %s 数据不充分(strict=%s): %s",
                        page_key, strict, missing,
                    )
                    return (page, missing)
                return None
            except (ValueError, TypeError) as e:
                logger.warning("[P6.3] 页面 %s 校验失败，视为缺口: %s", page_key, e)
                return (page, ["校验失败"])

        # 并发执行所有页面的校验，避免串行 LLM 调用耗时累加
        tasks = [_check_one_page(page) for page in pages]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        gap_pages: list[tuple[dict[str, Any], list[str]]] = []
        for page, r in zip(pages, results):
            if isinstance(r, Exception):
                logger.warning("[P6.3] 页面 %s 校验任务异常: %s", page.get("page_number"), r)
                gap_pages.append((page, ["校验失败"]))
                continue
            if r is not None:
                gap_pages.append(r)

        return gap_pages

    def _compose_validation_content(
        self,
        extractions: list[dict[str, Any]],
        max_chars: int = 2500,
    ) -> str:
        """合并抓取内容，优先保留结构化表格段落，按字符估算截断到约 2500 字（≈3000 token）。

        截断策略：
        1. 先从所有抓取内容中分离 markdown 表格段落（| ... |）和普通段落
        2. 优先拼接表格段落（结构化数据对校验更关键）
        3. 剩余预算拼接普通段落
        4. 超出 max_chars 时截断
        """
        table_parts: list[str] = []
        prose_parts: list[str] = []
        for e in extractions:
            content = e.get("content", "")
            if not content:
                continue
            lines = content.split("\n")
            current_table: list[str] = []
            for line in lines:
                stripped = line.strip()
                if stripped.startswith("|") and stripped.endswith("|") and "|" in stripped[1:-1]:
                    current_table.append(line)
                else:
                    if current_table:
                        table_parts.append("\n".join(current_table))
                        current_table = []
                    if stripped:
                        prose_parts.append(line)
            if current_table:
                table_parts.append("\n".join(current_table))

        result_parts: list[str] = []
        used = 0

        for part in table_parts:
            if used >= max_chars:
                break
            remain = max_chars - used
            if len(part) > remain:
                if remain > 100:
                    result_parts.append(part[:remain])
                    used = max_chars
                break
            result_parts.append(part)
            used += len(part)

        for part in prose_parts:
            if used >= max_chars:
                break
            remain = max_chars - used
            if len(part) > remain:
                if remain > 100:
                    result_parts.append(part[:remain])
                    used = max_chars
                break
            result_parts.append(part)
            used += len(part)

        return "\n---\n".join(result_parts) if result_parts else ""

    async def _backfill_fetch(
        self,
        gap_pages: list[tuple[dict[str, Any], list[str]]],
        page_sources: dict[str, list[dict[str, Any]]],
        page_extractions: dict[str, list[dict[str, Any]]],
        research_depth: str = "L2",
    ) -> dict[str, list[dict[str, Any]]]:
        if not self.has_tool("fetch_webpage"):
            return page_extractions

        extra_urls: dict[str, list[str]] = {}
        gap_page_list = [p for p, _ in gap_pages]

        # 首次抓取数已按 research_depth 动态调整（L1=2/L2=3/L3=4），
        # 回填从已抓取范围之后取 2 个新 URL
        backfill_start = {"L1": 2, "L2": 3, "L3": 4}.get(research_depth, 3)
        backfill_end = backfill_start + 2

        for page, missing in gap_pages:
            page_key = str(page["page_number"])
            sources = page_sources.get(page_key, [])
            existing_urls = {e.get("url") for e in page_extractions.get(page_key, [])}

            for source in sources[backfill_start:backfill_end]:
                url = source.get("url", "")
                if url and url not in existing_urls:
                    extra_urls.setdefault(page_key, []).append(url)

            if not extra_urls.get(page_key) and self.has_tool("web_search"):
                targeted_queries = self._build_targeted_queries(page, missing)
                # 补搜只取第1个查询，避免搜索次数膨胀
                for q in targeted_queries[:1]:
                    try:
                        search_result = await self.call_tool(
                            "web_search",
                            query=q,
                            search_mode="default",
                        )
                        new_urls = _URL_RE.findall(str(search_result))
                        for u in new_urls[:2]:
                            if u not in existing_urls:
                                extra_urls.setdefault(page_key, []).append(u)
                    except Exception as e:
                        if isinstance(e, AbortError):
                            raise
                        logger.warning("[P6.3] 补搜失败 page=%s: %s", page_key, e)

        if extra_urls:
            backfill = await self._batch_fetch(
                gap_page_list,
                page_sources,
                extra_urls=extra_urls,
                research_depth=research_depth,
            )
            for page_key, extractions in backfill.items():
                page_extractions.setdefault(page_key, []).extend(extractions)

        # 回填后用严格标准二次校验，保证最终报告质量
        still_gap = await self._validate_data_sufficiency(
            gap_page_list, page_extractions, research_depth, strict=True,
        )
        for page, _missing in still_gap:
            page_key = str(page["page_number"])
            page_extractions.setdefault(page_key, []).append({
                "url": "",
                "content": "[数据有限] 该页面研究素材不足，需降级撰写",
                "data_limited": True,
            })

        return page_extractions

    def _build_targeted_queries(self, page: dict[str, Any], missing: list[str]) -> list[str]:
        title = page.get("title", "")
        topic = title or "；".join(page.get("data_needs", [])[:1]) or page.get("type", "")
        queries: list[str] = []

        missing_set = set(missing)
        if "缺时序数据" in missing_set:
            queries.append(f"{topic} 历年数据 趋势")
        if "缺对比数据" in missing_set:
            queries.append(f"{topic} 对比 排名")
        if "数据类型单一" in missing_set:
            queries.append(f"{topic} 统计数据 报告")
        if "证据密度不足" in missing_set:
            queries.append(f"{topic} 白皮书 研究报告")
        if "来源不足" in missing_set:
            queries.append(f"{topic} 官方报告 权威数据")

        if not queries:
            base = page.get("research_queries", [])[:1]
            for q in base:
                queries.append(f"{q} 报告 白皮书 官方")
            if not queries:
                queries.append(f"{topic} 报告 白皮书 官方")

        return queries


class WriteResearchNode(PlanNode):
    def __init__(self) -> None:
        super().__init__(
            plan_name="p6_4_write_research",
            instruction=(
                "## P6.4 报告撰写与校验\n"
                "\n"
                "### 前置条件\n"
                "- `write_file` 工具可用（不可用时记录错误日志，返回空 research_path）\n"
                "- `output_dir` 非空（最终落盘到 `{output_dir}/research.md`）\n"
                "\n"
                "### 输入\n"
                "- `pages`: 需要研究的页面列表（含 page_number / type / title / data_needs）\n"
                "- `page_extractions`: 上游抓取节点产出的研究素材（搜索模式），no_search 时为空\n"
                "  - 含 `data_limited: true` 的条目表示该页素材不足，需降级撰写并标注\n"
                "- `source_material`: 用户素材（搜索模式裁剪到 3000 字预览；no_search 模式裁剪到 12000 字）\n"
                "- `search_mode`: no_search / auto / force_search\n"
                "- `research_depth`: L1 / L2 / L3\n"
                "- `topic`: PPT 主题\n"
                "- `output_dir`: 报告输出目录\n"
                "- `no_data_fallback`: 无研究数据降级标志（True 时既无搜索结果又无用户素材）\n"
                "\n"
                "### 执行流程\n"
                "1. **降级路径**：`no_data_fallback=True` → 直接由代码模板生成大纲骨架 markdown（不调 LLM），跳过校验落盘\n"
                "2. **正常路径**：\n"
                "   - 调用 LLM 按结构骨架生成完整 Markdown\n"
                "   - 调用 `write_file(file_path=output_dir/research.md, content=...)` 落盘\n"
                "   - 8 项产物校验（LLM）：通过则结束\n"
                "   - 失败重试：校验不通过则重写 1 次并覆盖落盘（不再二次校验）\n"
                "\n"
                "### research.md 结构骨架\n"
                "```\n"
                "# {topic} — 大纲研究报告\n"
                "> 生成时间：自动 | 研究深度：{research_depth} | 搜索模式：{search_mode}\n"
                "---\n"
                "## 逐页研究成果\n"
                "### P{N}: {页面标题}\n"
                "> 页面类型：{type}\n"
                "**核心论点**：{一句结论性陈述}\n"
                "#### PPT 内容建议\n"
                "- **推荐主标题**：{headline}\n"
                "- **核心论点**（5-10条，每条附展开说明和来源引用）\n"
                "- **关键数据清单**（Markdown表格，搜索模式≥5行 / no_search≥3行，含数据类型列）：\n"
                "  | 数据项 | 数值/结果 | 来源 | 时间 | 数据类型 |\n"
                "- **时序数据**（trend/data/comparison/technology页必填，≥3时间点）：\n"
                "  | 指标 | {t1} | {t2} | {t3} | 来源 |\n"
                "- **对比数据**（comparison/data/technology页必填，≥2对象×≥2维度）：\n"
                "  | 对比维度 | {A} | {B} | 来源 |\n"
                "- **案例素材**：{entity} — {description} [来源名称]\n"
                "```\n"
                "\n"
                "### 写作硬规则\n"
                "1. 要点优先，核心论点附展开说明和来源引用\n"
                "2. 精准引用：事实陈述首次出现时同句内附来源标注（如 [Gartner]、[年度报告]），禁止伪引用\n"
                "3. 反空泛：禁止'市场前景广阔''发展迅速'等无来源修饰，用精确数字替代；禁止 TODO/xxx 等占位文本\n"
                "4. 数据完整保留：所有数据点必须出现在关键数据清单表格中\n"
                "5. 来源可识别：使用来源名称标注，禁止纯数字编号\n"
                "6. 关键数据清单每页 ≥5 条（no_search ≥3 条）、≥2 种数据类型\n"
                "7. trend/data/comparison/technology 页必须有时序数据（≥3时间点）和对比数据（≥2对象×≥2维度）\n"
                "8. 数据有限页面：page_extractions 中含 `data_limited: true` 的页面，在该页 PPT 内容建议下显式标注'数据有限，基于用户素材'或'数据有限'\n"
                "\n"
                "### no_search 模式调整\n"
                "| 项目 | 搜索模式 | no_search 模式 |\n"
                "|---|---|---|\n"
                "| 数据来源 | 外部研究为主 | 用户素材为主 |\n"
                "| 来源标注 | [机构名] | [资料名] |\n"
                "| 全文字数 | L1≥1.2k/L2≥2k/L3≥3.5k | L1≥800/L2≥1.2k/L3≥2k |\n"
                "| 关键数据清单 | ≥5 条 | ≥3 条 |\n"
                "| 数据有限标注 | 仅在搜索不足时 | 每个仅凭素材的页面均标注'数据有限，基于用户素材' |\n"
                "\n"
                "### 产物校验（8 项，全部由 LLM 判断）\n"
                "1. 顶层章节含 `## 逐页研究成果`\n"
                "2. 每页结构：每个页面有 `### P{N}:` 标题且包含 `#### PPT 内容建议`\n"
                "3. PPT 内容建议含：推荐主标题 + 核心论点(5-10条) + 关键数据清单表格(≥5/≥3行) + 时序数据表(必要时) + 对比数据表(必要时) + 案例素材\n"
                "4. 数据表格格式：关键数据清单有'数据类型'列；时序/对比为专用表格而非散文\n"
                "5. 引用规范：每页 ≥3 个来源标注（来源名称，非数字编号），可识别且无伪引用\n"
                "6. 反空泛：无模糊修饰、无占位文本（TODO、xxx 等）\n"
                "7. 字数达标：实际中文字数 ≥ 最低字数的 80%\n"
                "8. 素材充实度：核心论点有展开说明和来源标注；案例含具体实体名称\n"
                "\n"
                "校验返回结构：`{\"pass\": true/false, \"failed_items\": [项号], \"reason\": \"...\"}`\n"
                "\n"
                "### 输出\n"
                "返回 `research_path`：research.md 的完整文件路径\n"
                "```json\n"
                '{"research_path": "{output_dir}/research.md"}\n'
                "```\n"
                "\n"
                "### 失败兜底\n"
                "- write_file 不可用：记录错误日志，返回空 research_path\n"
                "- LLM 撰写返回空：触发重写流程\n"
                "- 校验 LLM 调用失败/解析失败：保守视为未通过，触发重写\n"
                "- 重写 1 次后无论是否通过，直接落盘当前版本（避免无限循环）\n"
            ),
        )

    async def _execute(self, inputs: dict[str, Any]) -> dict[str, Any]:
        output_dir = inputs.get("output_dir", "")
        pages = inputs.get("pages", [])
        page_extractions = inputs.get("page_extractions", {})
        source_material = inputs.get("source_material", "")
        search_mode = inputs.get("search_mode", "auto")
        research_depth = inputs.get("research_depth", "L2")
        topic = inputs.get("topic", "")
        no_data_fallback = bool(inputs.get("no_data_fallback", False))

        research_md = await self._write_research_parallel(
            pages=pages,
            page_extractions=page_extractions,
            source_material=source_material,
            config=_ResearchConfig(
                search_mode=search_mode,
                research_depth=research_depth,
                topic=topic,
                no_data_fallback=no_data_fallback,
            ),
        )

        research_path = f"{output_dir}/research.md"
        await self._write_file(research_path, research_md)

        if no_data_fallback:
            logger.info("[P6.4] 无研究数据降级模式，跳过 8 项产物校验")
            return {"research_path": research_path}

        passed = await self._validate_research(research_md, pages, search_mode, research_depth)
        if not passed:
            logger.warning("[P6.4] research.md 校验未通过，尝试重写1次")
            research_md = await self._write_research_parallel(
                pages=pages,
                page_extractions=page_extractions,
                source_material=source_material,
                config=_ResearchConfig(
                    search_mode=search_mode,
                    research_depth=research_depth,
                    topic=topic,
                    no_data_fallback=no_data_fallback,
                ),
            )
            await self._write_file(research_path, research_md)

        return {"research_path": research_path}

    async def _write_file(self, path: str, content: str) -> None:
        if not self.has_tool("write_file"):
            logger.error("[P6.4] write_file 工具不可用，无法写入文件 %s", path)
            return
        try:
            await self.call_tool("write_file", file_path=path, content=content)
        except Exception as e:
            if isinstance(e, AbortError):
                raise
            logger.error("[P6.4] 写入文件失败 %s: %s", path, e)

    async def _write_single_page(
        self,
        page: dict[str, Any],
        extractions: list[dict[str, Any]],
        source_material: str,
        config: _ResearchConfig,
        min_words_per_page: int,
    ) -> str:
        """并行撰写单页研究报告，返回以 `### P{N}:` 开头的该页 Markdown 片段。"""
        page_num = page["page_number"]
        page_type = page.get("page_type", page.get("type", ""))
        title = page.get("title", "")
        data_needs = page.get("data_needs", []) or []

        extraction_summary = ""
        if extractions:
            for ext in extractions:
                extraction_summary += f"来源: {ext['url']}\n{ext['content']}\n\n"

        material_section = ""
        if source_material:
            material_limit = 8000 if config.search_mode == "no_search" else 2000
            truncated = source_material[:material_limit]
            material_section = f"\n\n用户素材（前 {material_limit} 字）：\n{truncated}"

        prompt = (
            "你是一位深度内容研究员。请撰写以下单页的研究报告段落，"
            "直接输出该页 Markdown 内容（以 `### P{N}:` 开头），"
            "不要输出报告标题（# 开头）或其他页面内容。\n\n"
            f"主题：{config.topic}\n"
            f"页面编号：P{page_num}\n"
            f"页面标题：{title}\n"
            f"页面类型：{page_type}\n"
            f"数据需求：{'; '.join(str(d) for d in data_needs)}\n"
            f"搜索模式：{config.search_mode}\n"
            f"研究深度：{config.research_depth}\n"
            f"本页最低字数：{min_words_per_page} 字\n\n"
            "### 严格格式要求（只输出本页章节，以 `### P{N}:` 开头）\n"
            "```\n"
            "### P{N}: {页面标题}\n"
            "> 页面类型：{type}\n"
            "**核心论点**：{一句结论性陈述}\n"
            "#### PPT 内容建议\n"
            "- **推荐主标题**：{headline}\n"
            "- **核心论点**（5-10条，每条附展开说明和来源引用）\n"
            "- **关键数据清单**（Markdown表格，≥5行，含数据类型列）：\n"
            "  | 数据项 | 数值/结果 | 来源 | 时间 | 数据类型 |\n"
            "- **时序数据**（trend/data/comparison/technology页必填，≥3时间点）：\n"
            "  | 指标 | {t1} | {t2} | {t3} | 来源 |\n"
            "- **对比数据**（comparison/data/technology页必填，≥2对象×≥2维度）：\n"
            "  | 对比维度 | {A} | {B} | 来源 |\n"
            "- **案例素材**：{entity} — {description} [来源名称]\n"
            "```\n\n"
            "### 写作硬规则\n"
            "1. 要点优先，核心论点附展开说明和来源引用\n"
            "2. 精准引用：事实陈述首次出现时同句内附来源标注（如 [Gartner]、[年度报告]），禁止伪引用\n"
            "3. 反空泛：禁止'市场前景广阔''发展迅速'等无来源修饰，用精确数字替代\n"
            "4. 数据完整保留：所有数据点必须出现在关键数据清单表格中\n"
            "5. 来源可识别：使用来源名称标注，禁止纯数字编号\n"
            "6. 关键数据清单每页 ≥5 条、≥2 种数据类型\n"
            "7. trend/data/comparison/technology 页必须有时序数据（≥3时间点）和对比数据（≥2对象×≥2维度）\n"
            f"8. 本页 ≥{min_words_per_page} 字\n"
            f"{'9. no_search 模式：数据有限页面标注「数据有限，基于用户素材」，关键数据清单 ≥3 行即可' if config.search_mode == 'no_search' else ''}\n\n"
            f"### 抓取内容\n{extraction_summary}"
            f"{material_section}"
        )

        result = await self.stream_llm_collect(
            prompt=prompt,
            system_prompt="你是深度内容研究员，直接输出该页的 Markdown 内容，不要输出解释。",
        )
        return result.strip() if result else ""

    def _build_fallback_page_section(self, page: dict[str, Any]) -> str:
        """单页撰写失败时的兜底骨架。"""
        page_num = page.get("page_number", "")
        title = page.get("title", "")
        page_type = page.get("page_type", page.get("type", ""))
        return (
            f"### P{page_num}: {title}\n"
            f"> 页面类型：{page_type}\n"
            "**核心论点**：[撰写失败，待补充]\n"
            "#### PPT 内容建议\n"
            f"- **推荐主标题**：{title}\n"
            "- **核心论点**：待补充\n"
            "- **关键数据清单**：待补充\n"
        )

    async def _write_research_parallel(
        self,
        pages: list[dict[str, Any]],
        page_extractions: dict[str, list[dict[str, Any]]],
        source_material: str,
        config: _ResearchConfig,
    ) -> str:
        """并行撰写所有页面，简单拼接成完整 research.md（P8 按 `### P{N}:` 拆分）。"""
        if config.no_data_fallback:
            return self._build_no_data_research(
                pages, config.topic, config.search_mode, config.research_depth,
            )

        total_min_words = _WORD_COUNT_MAP.get(config.research_depth, 2000)
        if config.search_mode == "no_search":
            total_min_words = _WORD_COUNT_NO_SEARCH_MAP.get(config.research_depth, 1200)
        min_words_per_page = max(200, total_min_words // max(len(pages), 1))

        logger.info(
            "[P6.4] 并行撰写 %d 页（每页最低 %d 字）",
            len(pages), min_words_per_page,
        )

        tasks = [
            self._write_single_page(
                page=page,
                extractions=page_extractions.get(str(page["page_number"]), []),
                source_material=source_material,
                config=config,
                min_words_per_page=min_words_per_page,
            )
            for page in pages
        ]
        results = await asyncio.gather(*tasks, return_exceptions=True)

        header = (
            f"# {config.topic} — 大纲研究报告\n"
            f"> 生成时间：自动 | 研究深度：{config.research_depth} | 搜索模式：{config.search_mode}\n"
            "---\n\n"
            "## 逐页研究成果\n\n"
        )

        page_sections: list[str] = []
        for page, result in zip(pages, results):
            page_num = int(page["page_number"])
            if isinstance(result, Exception):
                logger.warning("[P6.4] 页面 P%d 撰写失败: %s", page_num, result)
                page_sections.append(self._build_fallback_page_section(page))
            else:
                page_sections.append(result if result else self._build_fallback_page_section(page))

        return header + "\n\n".join(page_sections)

    def _build_no_data_research(
        self,
        pages: list[dict[str, Any]],
        topic: str,
        search_mode: str,
        research_depth: str,
    ) -> str:
        lines = [
            f"# {topic} — 大纲研究报告",
            f"> 生成时间：自动 | 研究深度：{research_depth} | 搜索模式：{search_mode}",
            "",
            "> ⚠️ **无研究数据降级模式**：未执行外部搜索且未提供用户素材，本报告仅输出大纲骨架，"
            "需后续阶段补充具体数据。",
            "",
            "---",
            "",
            "## 逐页研究成果",
            "",
        ]
        for page in pages:
            page_num = page.get("page_number", "")
            title = page.get("title", "")
            page_type = page.get("type", "")
            data_needs = page.get("data_needs", []) or []
            queries = page.get("research_queries", []) or []

            lines.append(f"### P{page_num}: {title}")
            lines.append(f"> 页面类型：{page_type}")
            lines.append("")
            lines.append("**核心论点**：[数据有限，基于大纲规划]")
            lines.append("")
            lines.append("#### PPT 内容建议")
            lines.append(f"- **推荐主标题**：{title}")
            lines.append("- **核心论点**：")
            if queries:
                for q in queries[:5]:
                    lines.append(f"  - {q}（待补充数据）")
            else:
                lines.append("  - 待补充")
            lines.append("- **关键数据清单**（无研究数据，待后续补充）：")
            lines.append("  | 数据项 | 数值/结果 | 来源 | 时间 | 数据类型 |")
            lines.append("  | --- | --- | --- | --- | --- |")
            if data_needs:
                for need in data_needs[:3]:
                    lines.append(f"  | {need} | 待补充 | 待补充 | 待补充 | 待补充 |")
            else:
                lines.append("  | 待补充 | 待补充 | 待补充 | 待补充 | 待补充 |")
            lines.append("- **数据有限**，本页未执行外部搜索，亦无用户素材。")
            lines.append("")

        return "\n".join(lines)

    async def _validate_research(
        self,
        content: str,
        pages: list[dict[str, Any]],
        search_mode: str,
        research_depth: str,
    ) -> bool:
        if not content:
            return False

        word_count_table = (
            "搜索模式：L1≥1.2k / L2≥2k / L3≥3.5k\n"
            "no_search：L1≥800 / L2≥1.2k / L3≥2k"
        )
        min_words = _WORD_COUNT_MAP.get(research_depth, 2000)
        if search_mode == "no_search":
            min_words = _WORD_COUNT_NO_SEARCH_MAP.get(research_depth, 1200)

        chinese_chars = len(re.findall(r"[\u4e00-\u9fff]", content))

        page_keys = [str(p["page_number"]) for p in pages]
        page_keys_text = "、".join(f"P{k}" for k in page_keys) or "无"

        found_page_nums = set()
        for m in _PAGE_HEADER_RE.finditer(content):
            found_page_nums.add(int(m.group(1)))
        expected_page_nums = set(int(p["page_number"]) for p in pages)
        missing_pages = sorted(expected_page_nums - found_page_nums)
        extra_pages = sorted(found_page_nums - expected_page_nums)
        page_alignment_ok = not missing_pages and not extra_pages

        if not page_alignment_ok:
            logger.warning(
                "[P6.4] research.md 页码不对齐，缺失=%s 多余=%s，直接判定校验不通过",
                missing_pages or None,
                extra_pages or None,
            )
            return False

        found_pages_text = "、".join(f"P{n}" for n in sorted(found_page_nums)) or "无"
        missing_pages_text = "、".join(f"P{n}" for n in missing_pages) or "无"
        extra_pages_text = "、".join(f"P{n}" for n in extra_pages) or "无"

        data_table_min = 5 if search_mode != "no_search" else 3

        min_words_80 = int(min_words * 0.8)
        prompt = (
            "请对以下 research.md 内容做 8 项产物验证，仅输出 JSON。\n\n"
            f"搜索模式：{search_mode}\n"
            f"研究深度：{research_depth}\n"
            f"最低字数：{min_words}（统计标准：{word_count_table}）\n"
            f"实际中文字数：{chinese_chars}\n"
            f"应包含页面：{page_keys_text}\n"
            f"实际包含页面：{found_pages_text}\n"
            f"缺失页面：{missing_pages_text}\n"
            f"多余页面：{extra_pages_text}\n"
            f"关键数据清单最少行数：{data_table_min}\n\n"
            "research.md 内容：\n"
            "```markdown\n"
            f"{content}\n"
            "```\n\n"
            "8 项校验：\n"
            "1. 顶层章节：含 `## 逐页研究成果`\n"
            f"2. 每页结构：每个页面（{page_keys_text}）有 `### P{{N}}:` 标题且包含 `#### PPT 内容建议`；"
            f"页码必须与应包含页面完全一致，不得缺失（{missing_pages_text}）"
            f"或多余（{extra_pages_text}）\n"
            f"3. PPT 内容建议：含推荐主标题、核心论点(5-10条)、"
            f"关键数据清单表格(≥{data_table_min}行)、"
            f"时序数据表格(trend/data页必填)、"
            f"对比数据表格(comparison/technology页必填)、案例素材\n"
            "4. 数据表格格式：关键数据清单含\"数据类型\"列；时序/对比为专用表格非散文\n"
            "5. 引用规范：每页 ≥3 个来源标注（使用来源名称，非数字编号），可识别且无伪引用\n"
            "6. 反空泛：无\"前景广阔/发展迅速\"等模糊修饰、无占位文本（如 TODO、xxx）\n"
            f"7. 字数达标：实际中文字数（{chinese_chars}）≥ 最低字数的 80%（即 ≥{min_words_80}）\n"
            "8. 素材充实度：核心论点有展开说明和来源标注；案例含具体实体名称\n\n"
            '输出 JSON：{"pass": true/false, "failed_items": [不通过项编号], "reason": "简要说明"}\n'
            "全部通过则 failed_items: []。\n"
            "只输出 JSON，不要输出其他内容。"
        )

        try:
            result = await self.stream_llm_collect(
                prompt=prompt,
                system_prompt="你是 research.md 产物验证助手，只输出 JSON。",
            )
            check = self.extract_json(result, expected_type=dict)
            if not isinstance(check, dict):
                logger.warning("[P6.4] 校验结果解析失败，视为未通过")
                return False
            passed = bool(check.get("pass", False))
            if not passed:
                logger.info(
                    "[P6.4] research.md 校验未通过 failed=%s reason=%s",
                    check.get("failed_items", []),
                    check.get("reason", ""),
                )
            return passed
        except (ValueError, TypeError) as e:
            logger.warning("[P6.4] 校验 LLM 调用失败，视为未通过: %s", e)
            return False

    async def _execute_stream(self, inputs: dict[str, Any]) -> AsyncIterator[dict[str, Any]]:
        result = await self._execute(inputs)
        yield {
            **result,
            "node": self.plan_name,
            "status": "warning",
            "message": "深度研究节点暂未实现，已透传当前上下文",
        }