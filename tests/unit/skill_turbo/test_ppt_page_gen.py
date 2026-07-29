# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
# pylint: disable=protected-access

"""PPTPageGen P8.1/P8.2 性能路径单元测试。"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest

from jiuwenclaw.agentserver.skill_turbo.plan_node import AbortError
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt import ppt_page_gen as ppg
from jiuwenclaw.agentserver.skill_turbo.skill_codes.ppt.utils.bash_utils import (
    BashResult,
)


_VALID_HTML = """<!DOCTYPE html>
<html>
<head><meta charset="utf-8"><style>.ppt-slide { width: 1220px; height: 660px; }</style></head>
<body>
<div class="ppt-slide">
  <h1>历史文化介绍</h1>
  <p>这是用于验证并发页面生成与确定性 HTML 校验的真实页面内容。</p>
  <p>页面成功后应直接落盘，不再发起密度检查、搜索补充或整页重写。</p>
</div>
</body>
</html>
"""


def _worker_inputs(page_count: int = 1, **overrides: Any) -> dict[str, Any]:
    inputs: dict[str, Any] = {
        "pages_dir": "D:/workspace/pages",
        "style_id": "business-classic",
        "style_text": "---\nfont-family: Arial\n---\n",
        "outline_text": "# outline",
        "outline_pages": {
            page_num: f"### P{page_num}: 页面 {page_num}"
            for page_num in range(1, page_count + 1)
        },
        "research_pages": {},
        "all_pages": list(range(1, page_count + 1)),
        "image_map": {},
        "designer_md_text": "",
    }
    inputs.update(overrides)
    return inputs


def _configure_worker(
    responses: list[str | BaseException],
    *,
    llm_calls: list[str],
    tool_calls: list[str],
    concurrency: dict[str, int] | None = None,
    written_contents: list[str] | None = None,
) -> ppg.PageWorkerNode:
    node = ppg.PageWorkerNode()
    queue = list(responses)

    async def _stream_llm(
        _prompt: str,
        _system_prompt: str = "",
        node_name: str | None = None,
        **_: Any,
    ) -> AsyncIterator[str]:
        llm_calls.append(str(node_name or ""))
        if concurrency is not None:
            concurrency["active"] += 1
            concurrency["peak"] = max(concurrency["peak"], concurrency["active"])
            await asyncio.sleep(0)
        response = queue.pop(0)
        if concurrency is not None:
            concurrency["active"] -= 1
        if isinstance(response, BaseException):
            raise response
        yield response

    async def _use_tool(tool_name: str, **kwargs: Any) -> dict[str, Any]:
        tool_calls.append(tool_name)
        if tool_name != "write_file":
            raise AssertionError(f"unexpected tool call: {tool_name}")
        if written_contents is not None:
            written_contents.append(str(kwargs.get("content") or ""))
        return {"success": True}

    node.set_runtime_callbacks(
        has_tool=lambda name: name == "write_file",
        use_tool=_use_tool,
        stream_llm=_stream_llm,
    )
    return node


@pytest.mark.unit
def test_page_worker_generates_each_page_once_without_post_generation_llm_or_search() -> None:
    llm_calls: list[str] = []
    tool_calls: list[str] = []
    concurrency = {"active": 0, "peak": 0}
    node = _configure_worker(
        [_VALID_HTML, _VALID_HTML, _VALID_HTML],
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        concurrency=concurrency,
    )

    result = asyncio.run(node._execute(_worker_inputs(page_count=3)))

    assert sorted(llm_calls) == ["p8_1_page_1", "p8_1_page_2", "p8_1_page_3"]
    assert concurrency["peak"] == 3
    assert tool_calls == ["write_file", "write_file", "write_file"]
    assert result["page_files"] == [
        "page-1.pptx.html",
        "page-2.pptx.html",
        "page-3.pptx.html",
    ]
    assert result["missing_pages"] == []
    assert result["low_density_pages"] == []
    assert result["density_report"] == {}


@pytest.mark.unit
def test_page_prompt_forbids_visible_page_numbers_for_all_page_types() -> None:
    for outline_page, research_page in [
        (
            "### P2: 内容页\n- **类型**: data\n- **研究需求**: ✅",
            "### P2: 内容页\n#### PPT 内容建议\n正文素材",
        ),
        ("### P10: 结束页\n- **类型**: ending\n- **研究需求**: ❌", ""),
    ]:
        prompt = ppg._build_page_prompt(
            2,
            style_id="business-classic",
            style_text="---\nfont-family: Arial\n---\n",
            outline_page=outline_page,
            research_page=research_page,
            user_query="生成 8 页商务经典风格 PPT",
        )

        assert "可见运行页码禁令（所有页型）" in prompt
        assert "用户要求“生成 N 页”只表示页数，不等于要求显示页码" in prompt
        assert "agenda 正文中的章节目标页码" in prompt


@pytest.mark.unit
def test_page_number_policy_requires_explicit_user_intent() -> None:
    assert not ppg._resolve_page_number_policy("生成 10 页 PPT").enabled
    assert not ppg._resolve_page_number_policy("不要显示页码，生成 10 页 PPT").enabled

    policy = ppg._resolve_page_number_policy("请在右下角添加页码")

    assert policy.enabled
    assert policy.position == "bottom-right"
    assert ppg._format_visible_page_number(policy, 2, 10) == "2 / 10"


@pytest.mark.unit
def test_page_prompt_defers_explicit_page_number_to_deterministic_patch() -> None:
    prompt = ppg._build_page_prompt(
        2,
        style_id="business-classic",
        style_text="---\nfont-family: Arial\n---\n",
        outline_page="### P2: 内容页\n- **类型**: data\n- **研究需求**: ✅",
        research_page="### P2: 内容页\n#### PPT 内容建议\n正文素材",
        user_query="页码生成在右下角",
        total_pages=10,
    )

    assert "用户显式页码要求（优先于默认禁令）" in prompt
    assert "文字逐字为 `2 / 10`" in prompt
    assert "当前页面生成阶段不得自行创建页码" in prompt
    assert "插入统一的可编辑文本页码" in prompt


@pytest.mark.unit
@pytest.mark.parametrize(
    ("query", "position", "marker"),
    [
        ("在左下角添加页码，仅显示当前页数字", "bottom-left", "7"),
        ("在右上角显示页码，格式为 Page N", "top-right", "Page 7"),
        ("在左上角生成页码，格式为第 N 页", "top-left", "第 7 页"),
        ("右下角显示两位补零页码，格式为 P N", "bottom-right", "P07"),
    ],
)
def test_explicit_page_number_position_and_format(
    query: str,
    position: str,
    marker: str,
) -> None:
    html = ppg._apply_visible_page_number_policy(
        _VALID_HTML,
        user_query=query,
        page_number=7,
        total_pages=12,
        style_id="business-classic",
    )

    assert html.count('data-skill-turbo-page-number="true"') == 1
    assert f'data-position="{position}"' in html
    assert f">{marker}</span>" in html


@pytest.mark.unit
def test_page_prompt_keeps_overlays_behind_editable_content_and_charts_vectorized() -> None:
    prompt = ppg._build_page_prompt(
        2,
        style_id="business-classic",
        style_text="---\nfont-family: Arial\n---\n",
        outline_page="### P2: 内容页\n- **类型**: data\n- **研究需求**: ✅",
        research_page="### P2: 内容页\n#### PPT 内容建议\n正文素材",
    )

    assert "背景图片 → 遮罩 → `relative z-10` 内容层" in prompt
    assert "遮罩只能覆盖背景图片" in prompt
    assert "禁止给语义内容的父容器设置 `opacity`" in prompt
    assert "ECharts 必须使用 SVG renderer" in prompt
    assert "echarts.graphic.LinearGradient/RadialGradient" in prompt
    assert "会使图表在 PPTX 中转成位图" in prompt


@pytest.mark.unit
@pytest.mark.parametrize(
    ("outline_page", "research_page"),
    [
        (
            "### P2: 内容页\n- **类型**: data\n- **研究需求**: ✅",
            "### P2: 内容页\n#### PPT 内容建议\n正文素材",
        ),
        ("### P6: 第二章\n- **类型**: chapter\n- **研究需求**: ❌", ""),
    ],
)
def test_page_prompt_enforces_page_chrome_source_contract_for_all_page_types(
    outline_page: str,
    research_page: str,
) -> None:
    prompt = ppg._build_page_prompt(
        2,
        style_id="custom",
        style_text="---\nfont-family: Arial\n---\n",
        outline_page=outline_page,
        research_page=research_page,
    )

    assert "观众可见文字来源契约（所有页型，强制）" in prompt
    assert "页眉、页脚、角标、徽章、状态条、导航标签和装饰性文字" in prompt
    assert "内部核对结果或生成流程" in prompt
    assert "编号必须来自章节顺序语义" in prompt
    assert "非章节页不得自行创建章节导航信息" in prompt
    assert "PART XX" not in prompt
    assert "数据已核验" not in prompt
    assert "CASE 02" not in prompt


@pytest.mark.unit
def test_page_prompt_preserves_chapter_label_supplied_by_outline() -> None:
    outline_page = (
        "### P6: PART 01 现状分析\n"
        "- **类型**: chapter\n"
        "- **研究需求**: ❌"
    )

    prompt = ppg._build_page_prompt(
        6,
        style_id="elegant-narrative",
        style_text="---\nfont-family: Arial\n---\n",
        outline_page=outline_page,
        research_page="",
    )

    assert "PART 01 现状分析" in prompt
    assert "编号必须来自章节顺序语义" in prompt


@pytest.mark.unit
def test_visible_page_marker_normalization_preserves_main_content_and_metadata() -> None:
    html = """<!DOCTYPE html>
<html><body><div class="ppt-slide">
<header><h1>标题</h1><span>P02 / 08</span></header>
<main><span>P3</span><p>产品 P3 型号</p></main>
<footer><span>第 10 页 / 共 10 页</span><span>v1.0</span><span>2026Q1</span></footer>
</div></body></html>"""

    normalized = ppg._strip_visible_page_markers(html)

    assert "P02 / 08" not in normalized
    assert "第 10 页 / 共 10 页" not in normalized
    assert "<span>P3</span>" in normalized
    assert "产品 P3 型号" in normalized
    assert "v1.0" in normalized
    assert "2026Q1" in normalized


@pytest.mark.unit
def test_page_worker_removes_page_marker_without_extra_llm_call() -> None:
    marked_html = _VALID_HTML.replace(
        "<h1>历史文化介绍</h1>",
        "<header><h1>历史文化介绍</h1><span>P01 / 10</span></header>",
    )
    llm_calls: list[str] = []
    written_contents: list[str] = []
    node = _configure_worker(
        [marked_html],
        llm_calls=llm_calls,
        tool_calls=[],
        written_contents=written_contents,
    )

    result = asyncio.run(node._execute(_worker_inputs()))

    assert llm_calls == ["p8_1_page_1"]
    assert result["missing_pages"] == []
    assert len(written_contents) == 1
    assert "P01 / 10" not in written_contents[0]


@pytest.mark.unit
def test_page_worker_inserts_consistent_page_numbers_without_extra_llm_calls() -> None:
    llm_calls: list[str] = []
    written_contents: list[str] = []
    node = _configure_worker(
        [_VALID_HTML, _VALID_HTML, _VALID_HTML],
        llm_calls=llm_calls,
        tool_calls=[],
        written_contents=written_contents,
    )

    result = asyncio.run(
        node._execute(
            _worker_inputs(
                page_count=3,
                query="请在右下角添加页码",
                total_pages=3,
            )
        )
    )

    assert sorted(llm_calls) == ["p8_1_page_1", "p8_1_page_2", "p8_1_page_3"]
    assert result["missing_pages"] == []
    assert len(written_contents) == 3
    combined_html = "\n".join(written_contents)
    assert all(
        combined_html.count(f">{marker}</span>") == 1
        for marker in ("1 / 3", "2 / 3", "3 / 3")
    )
    assert all(
        content.count('data-skill-turbo-page-number="true"') == 1
        for content in written_contents
    )


@pytest.mark.unit
def test_page_worker_retries_only_when_generated_html_is_invalid() -> None:
    llm_calls: list[str] = []
    tool_calls: list[str] = []
    node = _configure_worker(
        ["invalid html", _VALID_HTML],
        llm_calls=llm_calls,
        tool_calls=tool_calls,
    )

    result = asyncio.run(
        node._execute(_worker_inputs(gen_retry_round=1))
    )

    assert llm_calls == ["p8_1_page_1", "p8_1_page_1"]
    assert tool_calls == ["write_file"]
    assert result["page_files"] == ["page-1.pptx.html"]
    assert result["missing_pages"] == []


@pytest.mark.unit
def test_page_worker_caps_generation_attempts_at_pptx_craft_limit() -> None:
    llm_calls: list[str] = []
    tool_calls: list[str] = []
    node = _configure_worker(
        ["invalid html", "invalid html", "invalid html"],
        llm_calls=llm_calls,
        tool_calls=tool_calls,
    )

    result = asyncio.run(
        node._execute(_worker_inputs(gen_retry_round=99))
    )

    assert llm_calls == ["p8_1_page_1"] * 3
    assert tool_calls == []
    assert result["page_files"] == []
    assert result["missing_pages"] == [1]


@pytest.mark.unit
@pytest.mark.parametrize(
    "error",
    [AbortError(reason="cancelled"), asyncio.CancelledError()],
)
def test_page_worker_propagates_control_flow_interrupts(error: BaseException) -> None:
    node = _configure_worker(
        [error],
        llm_calls=[],
        tool_calls=[],
    )

    with pytest.raises(type(error)):
        asyncio.run(node._execute(_worker_inputs()))


@pytest.mark.unit
def test_qa_fix_runs_official_fix_without_layout_llm_repair(monkeypatch: pytest.MonkeyPatch) -> None:
    node = ppg.QAFixNode()
    fix_calls: list[tuple[list[int], str, str, str]] = []
    llm_calls: list[str] = []

    async def _check_completeness(
        _pages_dir: str,
        _page_count: int,
    ) -> tuple[bool, list[str]]:
        return True, ["page-1.pptx.html", "page-2.pptx.html"]

    async def _fix_pages(
        page_nums: list[int],
        *,
        pages_dir: str,
        pptx_root: str,
        style_file_path: str,
    ) -> list[tuple[int, bool, str]]:
        fix_calls.append((page_nums, pages_dir, pptx_root, style_file_path))
        return [(page_num, True, "ok") for page_num in page_nums]

    async def _stream_llm(
        _prompt: str,
        _system_prompt: str = "",
        node_name: str | None = None,
        **_: Any,
    ) -> AsyncIterator[str]:
        llm_calls.append(str(node_name or ""))
        yield _VALID_HTML

    monkeypatch.setattr(node, "_check_completeness", _check_completeness)
    monkeypatch.setattr(node, "_fix_pages", _fix_pages)
    node.set_runtime_callbacks(stream_llm=_stream_llm)

    result = asyncio.run(
        node._execute(
            {
                "pages_dir": "D:/workspace/pages",
                "page_count": 2,
                "total_pages": 2,
                "pptx_root": "D:/skills/pptx-craft",
                "style_file_path": "D:/workspace/style.md",
            }
        )
    )

    assert fix_calls == [
        (
            [1, 2],
            "D:/workspace/pages",
            "D:/skills/pptx-craft",
            "D:/workspace/style.md",
        )
    ]
    assert llm_calls == []
    assert result["qa_status"] == "ok"
    assert result["final_page_files"] == ["page-1.pptx.html", "page-2.pptx.html"]
    assert result["fix_report"] == "fix=page-1: ok,page-2: ok"


@pytest.mark.unit
def test_qa_fix_command_uses_fix_with_style_and_never_check_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    node = ppg.QAFixNode()
    commands: list[str] = []

    async def _run_bash(
        _node: ppg.PlanNode,
        command: str,
        **_: Any,
    ) -> BashResult:
        commands.append(command)
        await asyncio.sleep(0)
        return BashResult(exit_code=0, stdout="ok", stderr="", raw="ok")

    monkeypatch.setattr(
        ppg,
        "cli_path",
        lambda subcommand, _root: f"node cli.js {subcommand}",
    )
    monkeypatch.setattr(ppg, "run_bash", _run_bash)

    results = asyncio.run(
        node._fix_pages(
            [1, 2],
            pages_dir="D:/workspace/pages",
            pptx_root="D:/skills/pptx-craft",
            style_file_path="D:/workspace/style.md",
        )
    )

    assert results == [(1, True, "ok"), (2, True, "ok")]
    assert len(commands) == 2
    assert all(" fix " in command for command in commands)
    assert all("--fix" in command for command in commands)
    assert all("--style \"D:/workspace/style.md\"" in command for command in commands)
    assert all("--pages" in command for command in commands)
    assert all("check-layout" not in command for command in commands)
