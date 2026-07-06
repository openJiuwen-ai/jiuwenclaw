# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

"""ContentPlanNode 单元测试（P4.1 / P4.2 / P4.3 / P4.4）。"""

from __future__ import annotations

import asyncio
import importlib.util
import sys
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

_PKG_ROOT = Path(__file__).resolve().parents[3]

_P41_RESPONSE_RICH = '{"material_richness":"rich","focus_areas":"AI 市场规模与竞品格局"}'
_P41_RESPONSE_EMPTY = '{"material_richness":"empty","focus_areas":"基于主题做行业概览"}'

_P42A_RESPONSE = (
    '{"queries":['
    '{"dimension":"领域现状","query":"2025 AI agent market overview"},'
    '{"dimension":"关键维度","query":"enterprise AI agent platforms comparison"},'
    '{"dimension":"最新动态","query":"AI coding agent trends 2026"},'
    '{"dimension":"核心玩家","query":"Cursor Copilot enterprise market share"},'
    '{"dimension":"争议热点","query":"AI agent ROI enterprise challenges"}'
    "]}"
)

_MOCK_SEARCH_RESULT = "搜索结果摘要：AI Agent 市场规模持续增长，含多家机构观点。"


def _make_outline_markdown(
    *,
    topic: str = "2025 AI 趋势",
    page_count: int = 2,
    include_searched_sources: bool = False,
    p2_missing_research_query: bool = False,
) -> str:
    pages = []
    for index in range(1, page_count + 1):
        page_type = "trend"
        research = "✅"
        queries = "-" if p2_missing_research_query else '"AI agent market 2026", "智能体市场规模"'
        data_need = "全球市场规模、CAGR"
        title = "AI Agent 市场规模持续扩大"
        pages.append(
            f"""### P{index}: 页面{index}
- **类型**：{page_type}
- **研究需求**：{research}
- **标题**：{title}
- **内容概要**：基于调研与素材的核心要点。
- **研究查询**：{queries}
- **数据需求**：{data_need}"""
        )
    body = "\n\n".join(pages)
    searched_sources = ""
    if include_searched_sources:
        searched_sources = """
## 已搜索来源

| URL | 覆盖维度 |
|-----|----------|
| https://example.com/ai-report | 领域现状 |
"""
    return f"""# 大纲：{topic}

**受众**：企业高管
**总页数**：{page_count}
**叙事主线**：从概览到趋势
**输入类型**：topic
**搜索模式**：auto
{searched_sources}
## 页面规划

{body}
"""


_P43_OUTLINE_RESPONSE = _make_outline_markdown()


def _load_module(module_name: str, file_path: Path):
    spec = importlib.util.spec_from_file_location(module_name, file_path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


_load_module(
    "jiuwenclaw.agentserver.skill_turbo.plan_node",
    _PKG_ROOT / "jiuwenclaw/agentserver/skill_turbo/plan_node.py",
)
cp = _load_module(
    "jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.content_plan",
    _PKG_ROOT / "jiuwenclaw/agentserver/skill_turbo/skill_codes/ppt/content_plan.py",
)


def _base_ctx(**overrides: Any) -> dict[str, Any]:
    ctx: dict[str, Any] = {
        "topic": "2025 AI 趋势",
        "page_count": 10,
        "audience": "企业高管",
        "presentation_purpose": "工作汇报",
        "search_mode": "auto",
        "source_type": "topic",
        "output_dir": str(_PKG_ROOT / "tests" / "unit" / "skill_turbo" / "_tmp_p4"),
    }
    ctx.update(overrides)
    return ctx


async def _mock_stream_llm_from_queue(
    prompt: str, system_prompt: str = "", node_name: str | None = None, **_: Any,
) -> AsyncIterator[str]:
    """将 call_llm 模式的队列包装为 stream_llm 所需的 AsyncIterator。"""
    # 由各 _make_pX_node 在闭包中维护 llm_queue
    raise NotImplementedError("should be overridden per node factory")


def _make_p41_node(*, llm_responses: list[str] | None = None) -> cp.P41NormalizeNode:
    node = cp.P41NormalizeNode()
    llm_queue = list(llm_responses or [])

    async def _mock_call_llm(prompt: str, system_prompt: str = "", **_: Any) -> str:
        if not llm_queue:
            return _P41_RESPONSE_EMPTY
        return llm_queue.pop(0)

    async def _mock_stream_llm(
        prompt: str, system_prompt: str = "", node_name: str | None = None, **_: Any,
    ) -> AsyncIterator[str]:
        text = await _mock_call_llm(prompt, system_prompt=system_prompt)
        yield text

    async def _mock_call_tool(tool_name: str, **kwargs: Any) -> Any:
        if tool_name == "read_file":
            return _mock_read_file(**kwargs)
        raise ValueError(f"unknown tool: {tool_name}")

    node.set_runtime_callbacks(
        has_tool=lambda name: name == "read_file",
        use_tool=_mock_call_tool,
        call_llm=_mock_call_llm,
        stream_llm=_mock_stream_llm,
    )
    return node


def _mock_write_file(**kwargs: Any) -> dict[str, Any]:
    path = Path(str(kwargs["file_path"]))
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(kwargs["content"], encoding="utf-8")
    return {"success": True, "file_path": str(path)}


def _mock_read_file(**kwargs: Any) -> dict[str, str]:
    path = Path(str(kwargs["file_path"]))
    if not path.is_file():
        return {"content": ""}
    return {"content": path.read_text(encoding="utf-8")}


def _make_p44_node() -> cp.P44ValidateNode:
    node = cp.P44ValidateNode()

    async def _mock_call_tool(tool_name: str, **kwargs: Any) -> Any:
        if tool_name == "read_file":
            return _mock_read_file(**kwargs)
        raise ValueError(f"unknown tool: {tool_name}")

    node.set_runtime_callbacks(
        has_tool=lambda name: name == "read_file",
        use_tool=_mock_call_tool,
    )
    return node


def _make_p42_node(
    *,
    llm_responses: list[str] | None = None,
    search_results: list[str] | None = None,
    has_web_search: bool = True,
) -> cp.P42QuickResearchNode:
    node = cp.P42QuickResearchNode()
    llm_queue = list(llm_responses or [])
    search_queue = list(search_results or [])

    async def _mock_call_llm(prompt: str, system_prompt: str = "", **_: Any) -> str:
        if not llm_queue:
            raise RuntimeError("unexpected llm call")
        return llm_queue.pop(0)

    async def _mock_stream_llm(
        prompt: str, system_prompt: str = "", node_name: str | None = None, **_: Any,
    ) -> AsyncIterator[str]:
        text = await _mock_call_llm(prompt, system_prompt=system_prompt)
        yield text

    async def _mock_call_tool(tool_name: str, **kwargs: Any) -> Any:
        if tool_name == "web_search":
            if not has_web_search:
                raise ValueError("no tool")
            if not search_queue:
                return _MOCK_SEARCH_RESULT
            return search_queue.pop(0)
        raise ValueError(f"unknown tool: {tool_name}")

    def _has_tool(name: str) -> bool:
        return name == "web_search" if has_web_search else False

    node.set_runtime_callbacks(
        has_tool=_has_tool,
        use_tool=_mock_call_tool,
        call_llm=_mock_call_llm,
        stream_llm=_mock_stream_llm,
    )
    return node


def _make_p43_node(*, llm_responses: list[str] | None = None) -> cp.P43OutlineGenNode:
    node = cp.P43OutlineGenNode()
    llm_queue = list(llm_responses or [])

    async def _mock_call_llm(prompt: str, system_prompt: str = "", **_: Any) -> str:
        if not llm_queue:
            return _P43_OUTLINE_RESPONSE
        return llm_queue.pop(0)

    async def _mock_stream_llm(
        prompt: str, system_prompt: str = "", node_name: str | None = None, **_: Any,
    ) -> AsyncIterator[str]:
        text = await _mock_call_llm(prompt, system_prompt=system_prompt)
        yield text

    async def _mock_call_tool(tool_name: str, **kwargs: Any) -> Any:
        if tool_name == "write_file":
            return _mock_write_file(**kwargs)
        if tool_name == "read_file":
            return _mock_read_file(**kwargs)
        raise ValueError(f"unknown tool: {tool_name}")

    def _has_tool(name: str) -> bool:
        return name in ("write_file", "read_file")

    node.set_runtime_callbacks(
        has_tool=_has_tool,
        use_tool=_mock_call_tool,
        call_llm=_mock_call_llm,
        stream_llm=_mock_stream_llm,
    )
    return node


@pytest.mark.unit
@pytest.mark.parametrize(
    ("search_mode", "richness", "expected"),
    [
        ("no_search", "rich", False),
        ("no_search", "thin", False),
        ("no_search", "empty", False),
        ("auto", "rich", False),
        ("auto", "thin", True),
        ("auto", "empty", True),
        ("force_search", "rich", True),
        ("force_search", "thin", True),
        ("force_search", "empty", True),
    ],
)
def test_decide_p4_should_search(search_mode: str, richness: str, expected: bool) -> None:
    assert cp._decide_p4_should_search(search_mode, richness) is expected


@pytest.mark.unit
def test_parse_p41_response() -> None:
    parsed = cp._parse_p41_response(_P41_RESPONSE_RICH)
    assert parsed["material_richness"] == "rich"
    assert "竞品" in parsed["focus_areas"]


@pytest.mark.unit
def test_parse_p41_response_raises_on_invalid_richness() -> None:
    with pytest.raises(cp.ContentPlanError, match="material_richness"):
        cp._parse_p41_response('{"material_richness":"medium","focus_areas":"x"}')


@pytest.mark.unit
def test_require_p4_prerequisites_raises_without_topic() -> None:
    with pytest.raises(cp.ContentPlanError, match="topic"):
        cp._require_p4_prerequisites(_base_ctx(topic=""))


@pytest.mark.unit
def test_p41_auto_rich_skips_search() -> None:
    node = _make_p41_node(llm_responses=[_P41_RESPONSE_RICH])
    ctx = _base_ctx(search_mode="auto")
    result = asyncio.run(node._execute(ctx))
    assert result["material_richness"] == "rich"
    assert result["p4_should_search"] is False
    assert result["content_plan_status"] == "normalized"
    assert "跳过" in result["p4_search_reason"]


@pytest.mark.unit
def test_p41_auto_empty_needs_search() -> None:
    node = _make_p41_node(llm_responses=[_P41_RESPONSE_EMPTY])
    ctx = _base_ctx(search_mode="auto")
    result = asyncio.run(node._execute(ctx))
    assert result["material_richness"] == "empty"
    assert result["p4_should_search"] is True


@pytest.mark.unit
def test_p41_reads_doc_raw(tmp_path: Path) -> None:
    doc_path = tmp_path / "doc_raw.md"
    doc_path.write_text("# 报告\n\n详细章节与数据。", encoding="utf-8")
    node = _make_p41_node(llm_responses=[_P41_RESPONSE_RICH])
    ctx = _base_ctx(
        search_mode="auto",
        output_dir=str(tmp_path),
        doc_raw_path=str(doc_path),
        has_documents=True,
        doc_parse_ok=True,
    )
    result = asyncio.run(node._execute(ctx))
    assert result["has_source_material"] is True
    assert result["source_material_chars"] > 0


@pytest.mark.unit
def test_parse_p42a_queries_count() -> None:
    parsed = cp._parse_p42a_queries(_P42A_RESPONSE, has_source_material=False)
    assert len(parsed) == 5


@pytest.mark.unit
def test_parse_p42a_queries_raises_on_wrong_count() -> None:
    too_few = '{"queries":[{"dimension":"x","query":"a"}]}'
    with pytest.raises(cp.ContentPlanError, match="查询数量"):
        cp._parse_p42a_queries(too_few, has_source_material=False)


@pytest.mark.unit
def test_p42_skipped_when_no_search_needed() -> None:
    node = cp.P42QuickResearchNode()
    ctx = {"p4_should_search": False}
    result = asyncio.run(node._execute(ctx))
    assert result["p4_quick_research_status"] == "skipped"


@pytest.mark.unit
def test_p42_completes_and_stores_search_results(tmp_path: Path) -> None:
    node = _make_p42_node(llm_responses=[_P42A_RESPONSE])
    ctx = _base_ctx(
        output_dir=str(tmp_path),
        p4_should_search=True,
        focus_areas="AI 市场规模",
    )
    result = asyncio.run(node._execute(ctx))
    assert result["p4_quick_research_status"] == "completed"
    assert result["p4_search_hit_count"] == 5
    assert "search_results" in result
    assert len(result["search_results"]) == 5


@pytest.mark.unit
def test_p42_raises_without_web_search_tool() -> None:
    node = _make_p42_node(
        llm_responses=[_P42A_RESPONSE],
        has_web_search=False,
    )
    ctx = _base_ctx(p4_should_search=True, focus_areas="AI")
    with pytest.raises(cp.ContentPlanError, match="web_search"):
        asyncio.run(node._execute(ctx))


@pytest.mark.unit
def test_p42_raises_when_all_searches_fail() -> None:
    node = _make_p42_node(
        llm_responses=[_P42A_RESPONSE],
        search_results=["[ERROR]: failed"] * 5,
    )
    ctx = _base_ctx(p4_should_search=True, focus_areas="AI")
    with pytest.raises(cp.ContentPlanError, match="均无有效结果"):
        asyncio.run(node._execute(ctx))


@pytest.mark.unit
def test_should_include_searched_sources() -> None:
    assert cp._should_include_searched_sources({"p4_quick_research_status": "completed"}) is True
    assert cp._should_include_searched_sources({"p4_quick_research_status": "skipped"}) is False


@pytest.mark.unit
def test_format_search_results_for_p43() -> None:
    search_results = [
        {"dimension": "领域现状", "query": "AI agent market", "result": "市场规模持续增长"},
        {"dimension": "核心玩家", "query": "AI platforms", "result": "主流平台对比"},
    ]
    text = cp._format_search_results_for_p43(search_results)
    assert "### 网页搜索结果" in text
    assert "AI agent market" in text
    assert "领域现状" in text
    assert "市场规模持续增长" in text


@pytest.mark.unit
def test_validate_outline_markdown_basic() -> None:
    cp._validate_outline_markdown_basic(
        _make_outline_markdown(page_count=2),
        topic="2025 AI 趋势",
        page_count=2,
    )


@pytest.mark.unit
def test_validate_outline_raises_on_page_count_mismatch() -> None:
    with pytest.raises(cp.ContentPlanError, match="内容页数"):
        cp._validate_outline_markdown_basic(
            _make_outline_markdown(page_count=2),
            topic="2025 AI 趋势",
            page_count=3,
        )


@pytest.mark.unit
def test_p43_completes_and_writes_outline(tmp_path: Path) -> None:
    node = _make_p43_node(llm_responses=[_P43_OUTLINE_RESPONSE])
    ctx = _base_ctx(
        output_dir=str(tmp_path),
        page_count=2,
        focus_areas="AI 市场规模",
        content_plan_status="quick_research_done",
        p4_quick_research_status="completed",
    )
    result = asyncio.run(node._execute(ctx))
    assert result["p4_outline_gen_status"] == "completed"
    assert result["content_plan_status"] == "outline_generated"
    outline_path = Path(result["outline_path"])
    assert outline_path.is_file()
    content = outline_path.read_text(encoding="utf-8")
    assert "# 大纲：2025 AI 趋势" in content
    assert "## 页面规划" in content
    assert "### P1:" in content


@pytest.mark.unit
def test_p43_raises_on_empty_llm() -> None:
    node = _make_p43_node(llm_responses=[""])
    ctx = _base_ctx(page_count=2, focus_areas="AI")
    with pytest.raises(cp.ContentPlanError, match="LLM 返回为空"):
        asyncio.run(node._execute(ctx))


@pytest.mark.unit
def test_p43_uses_outline_prompt_for_source_type_outline() -> None:
    captured: dict[str, str] = {}
    node = cp.P43OutlineGenNode()

    async def _mock_call_llm(prompt: str, system_prompt: str = "", **_: Any) -> str:
        captured["system_prompt"] = system_prompt
        return _make_outline_markdown(page_count=2)

    async def _mock_stream_llm(
        prompt: str, system_prompt: str = "", node_name: str | None = None, **_: Any,
    ) -> AsyncIterator[str]:
        text = await _mock_call_llm(prompt, system_prompt=system_prompt)
        yield text

    async def _mock_call_tool(tool_name: str, **kwargs: Any) -> Any:
        if tool_name == "write_file":
            return _mock_write_file(**kwargs)
        raise ValueError(f"unknown tool: {tool_name}")

    node.set_runtime_callbacks(
        has_tool=lambda name: name == "write_file",
        use_tool=_mock_call_tool,
        call_llm=_mock_call_llm,
        stream_llm=_mock_stream_llm,
    )
    ctx = _base_ctx(page_count=2, source_type="outline", focus_areas="AI")
    asyncio.run(node._execute(ctx))
    assert "source_type=outline" in captured["system_prompt"]
    assert "保留原文" in captured["system_prompt"]


def _write_outline_file(tmp_path: Path, content: str) -> str:
    outline_path = tmp_path / "outline.md"
    outline_path.write_text(content, encoding="utf-8")
    return str(outline_path)


@pytest.mark.unit
def test_validate_full_requires_research_fields_on_check_pages() -> None:
    invalid = _make_outline_markdown(page_count=2, p2_missing_research_query=True)
    with pytest.raises(cp.ContentPlanError, match="研究查询"):
        cp._validate_outline_markdown_full(
            invalid,
            topic="2025 AI 趋势",
            page_count=2,
            include_searched_sources=False,
        )


@pytest.mark.unit
def test_validate_full_requires_searched_sources_when_search_done() -> None:
    outline = _make_outline_markdown(page_count=2, include_searched_sources=False)
    with pytest.raises(cp.ContentPlanError, match="已搜索来源"):
        cp._validate_outline_markdown_full(
            outline,
            topic="2025 AI 趋势",
            page_count=2,
            include_searched_sources=True,
        )


@pytest.mark.unit
def test_p44_passes_valid_outline(tmp_path: Path) -> None:
    outline_path = _write_outline_file(
        tmp_path,
        _make_outline_markdown(page_count=2, include_searched_sources=True),
    )
    ctx = _base_ctx(
        output_dir=str(tmp_path),
        page_count=2,
        outline_path=outline_path,
        p4_quick_research_status="completed",
    )
    asyncio.run(cp._run_p44_validate(_make_p44_node(), ctx))
    assert ctx["p4_validate_status"] == "passed"
    assert ctx["content_plan_status"] == "completed"


@pytest.mark.unit
def test_p44_passes_when_search_skipped(tmp_path: Path) -> None:
    outline_path = _write_outline_file(tmp_path, _make_outline_markdown(page_count=2))
    ctx = _base_ctx(
        output_dir=str(tmp_path),
        page_count=2,
        outline_path=outline_path,
        p4_quick_research_status="skipped",
    )
    asyncio.run(cp._run_p44_validate(_make_p44_node(), ctx))
    assert ctx["p4_validate_status"] == "passed"


@pytest.mark.unit
def test_content_plan_retries_once_on_validate_failure(tmp_path: Path, monkeypatch) -> None:
    attempts = {"p41": 0, "p44": 0}
    real_p44 = cp._run_p44_validate

    async def fake_p41(node, inputs):
        attempts["p41"] += 1
        inputs.setdefault("focus_areas", "AI")
        inputs["material_richness"] = "rich"
        inputs["p4_should_search"] = False
        inputs["p4_quick_research_status"] = "skipped"
        inputs["content_plan_status"] = "normalized"

    async def fake_p42(node, inputs):
        inputs["p4_quick_research_status"] = "skipped"

    async def fake_p43(node, inputs):
        path = Path(str(inputs["output_dir"])) / "outline.md"
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            _make_outline_markdown(page_count=2, include_searched_sources=False),
            encoding="utf-8",
        )
        inputs["outline_path"] = str(path.resolve())
        inputs["content_plan_status"] = "outline_generated"

    async def fake_p44(node, inputs):
        attempts["p44"] += 1
        if attempts["p44"] == 1:
            raise cp.ContentPlanError("P4.4 校验失败：mock")
        await real_p44(_make_p44_node(), inputs)

    monkeypatch.setattr(cp, "_run_p41_normalize", fake_p41)
    monkeypatch.setattr(cp, "_run_p42_quick_research", fake_p42)
    monkeypatch.setattr(cp, "_run_p43_outline_gen", fake_p43)
    monkeypatch.setattr(cp, "_run_p44_validate", fake_p44)

    node = cp.ContentPlanNode()
    ctx = _base_ctx(output_dir=str(tmp_path), page_count=2)
    result = asyncio.run(node._execute(ctx))
    assert attempts["p41"] == 2
    assert attempts["p44"] == 2
    assert result["content_plan_status"] == "completed"
    assert result["p4_retry_count"] == 1
