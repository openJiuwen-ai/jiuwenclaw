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
def test_strip_unsupported_fullpage_overlay_removes_scanlines_cover() -> None:
    """全页 inset:0 + repeating-linear-gradient + mix-blend-mode 的空遮罩须被移除。

    复现 page-12“透明罩”：.scanlines 被 html-to-pptx 栅格化为覆盖整页的图片。
    """
    html = """<div class="ppt-slide" type="content">
<style>
.scanlines{position:absolute;inset:0;pointer-events:none;z-index:5;
  background:repeating-linear-gradient(0deg,rgba(0,255,255,0.04) 0px,rgba(0,255,255,0.04) 1px,transparent 1px,transparent 4px);
  mix-blend-mode:screen;}
.cyber-card{background:#15151F;border:1px solid #00FFFF;}
</style>
  <div class="scanlines"></div>
  <div class="content-safe flex flex-col" style="position:relative;z-index:10;">
    <h1>标题</h1><p>正文内容</p>
  </div>
</div>"""

    result = ppg._strip_unsupported_fullpage_overlays(html)

    assert '<div class="scanlines"></div>' not in result
    assert "正文内容" in result
    assert "cyber-card" in result  # 无关规则与内容不受影响


@pytest.mark.unit
def test_strip_unsupported_fullpage_overlay_keeps_individual_scanline_bars() -> None:
    """独立小尺寸 solid-color scanline 条（非全页、无不支持属性）不得被误删。"""
    html = """<div class="ppt-slide" type="content">
<style>.scanline{position:absolute;left:0;right:0;height:1px;opacity:0.4;}</style>
  <div class="scanline" style="top:100px;background:#00FFFF;"></div>
  <div class="scanline" style="top:640px;background:#FF00FF;"></div>
  <div class="content-safe"><h1>标题</h1></div>
</div>"""

    result = ppg._strip_unsupported_fullpage_overlays(html)

    assert result.count('class="scanline"') == 2
    assert "标题" in result


@pytest.mark.unit
def test_strip_unsupported_fullpage_overlay_handles_inline_style_cover() -> None:
    """内联 style 的全页栅格化遮罩同样须被移除。"""
    html = """<div class="ppt-slide" type="content">
  <div style="position:absolute;inset:0;background:repeating-linear-gradient(0deg,rgba(0,0,0,0.04) 0,rgba(0,0,0,0.04) 1px,transparent 1px,transparent 4px);mix-blend-mode:screen;"></div>
  <div class="content-safe"><h1>标题</h1></div>
</div>"""

    result = ppg._strip_unsupported_fullpage_overlays(html)

    assert "repeating-linear-gradient" not in result
    assert '<div style="position:absolute;inset:0;' not in result
    assert "标题" in result


@pytest.mark.unit
def test_strip_unsupported_fullpage_overlay_skips_non_overlay_empty_div() -> None:
    """无不支持属性的空 div（如 spacer）不得被误删。"""
    html = """<div class="ppt-slide" type="content">
  <div class="spacer"></div>
  <div class="content-safe"><h1>标题</h1></div>
</div>"""

    result = ppg._strip_unsupported_fullpage_overlays(html)

    assert '<div class="spacer"></div>' in result
    assert "标题" in result


@pytest.mark.unit
def test_strip_unsupported_fullpage_overlay_skips_overlay_div_with_content() -> None:
    """有内容子节点的遮罩 div 不得被删（只删空 div，保证安全）。"""
    html = """<div class="ppt-slide" type="content">
<style>.overlay{position:absolute;inset:0;background:repeating-linear-gradient(0deg,#fff 0,#fff 1px,transparent 1px,transparent 4px);}</style>
  <div class="overlay"><p>遮罩内文案</p></div>
  <div class="content-safe"><h1>标题</h1></div>
</div>"""

    result = ppg._strip_unsupported_fullpage_overlays(html)

    assert "遮罩内文案" in result


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
    # P8.2 fix 前后会探测 read_file；本用例只断言 bash 命令，不走 DOM 回退读盘。
    node.set_runtime_callbacks(has_tool=lambda _name: False)

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


_AGENDA_OUTLINE = (
    "### P2:\n"
    "- **类型**：agenda\n"
    "- **研究需求**：❌\n"
    "- **标题**：目录\n"
    "- **内容概要**：\n"
    "  - 历史背景：（P3-P4）\n"
    "  - 事件经过：（P5-P9）\n"
    "  - 历史意义：（P10）\n"
    "  - 影响与评价：（P11-P12）\n"
)

_AGENDA_SEED_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{{PAGE_TITLE}}</title>
<style>.ppt-slide{width:1280px;height:720px;overflow:hidden}</style>
</head>
<body>
<div class="ppt-slide agenda-stage" type="agenda">
  <h1>{{PAGE_TITLE}}</h1>
  <p>{{AGENDA_DESC}}</p>
  <div>
    <p>{{AGENDA_1_TITLE}}</p><p>{{AGENDA_1_DESC}}</p>
    <p>{{AGENDA_2_TITLE}}</p><p>{{AGENDA_2_DESC}}</p>
    <p>{{AGENDA_3_TITLE}}</p><p>{{AGENDA_3_DESC}}</p>
    <p>{{AGENDA_4_TITLE}}</p><p>{{AGENDA_4_DESC}}</p>
  </div>
  <p>{{PAGE_FOOTER}}</p>
</div>
</body>
</html>
"""

_AGENDA_FILLED_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>目录</title>
<style>.ppt-slide{width:1280px;height:720px;overflow:hidden}</style>
</head>
<body>
<div class="ppt-slide agenda-stage" type="agenda">
  <h1>目录</h1>
  <p>沿文明长河读中华故事</p>
  <div>
    <p>历史背景</p><p>中国历史文化脉络（P3-P4）</p>
    <p>事件经过</p><p>唐朝与四大发明（P5-P9）</p>
    <p>历史意义</p><p>中华文明价值（P10）</p>
    <p>影响与评价</p><p>世界影响（P11-P12）</p>
  </div>
  <p>历史课 · 文化分享</p>
</div>
</body>
</html>
"""


def _write_agenda_template(tmp_path, style_id: str, content: str = _AGENDA_SEED_HTML) -> str:
    return _write_style_template(tmp_path, style_id, "agenda", content)


def _write_style_template(
    tmp_path,
    style_id: str,
    template_page_type: str,
    content: str,
) -> str:
    template_dir = tmp_path / "references" / "styles" / style_id
    template_dir.mkdir(parents=True, exist_ok=True)
    (template_dir / f"{template_page_type}-template.html").write_text(content, encoding="utf-8")
    return str(tmp_path)


_ENDING_OUTLINE = (
    "### P10:\n"
    "- **类型**：ending\n"
    "- **研究需求**：❌\n"
    "- **标题**：感谢聆听\n"
    "- **内容概要**：结束页，展示感谢语与一句总结\n"
)

_ENDING_SEED_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>{{ENDING_TITLE}}</title>
<style>.ppt-slide{width:1280px;height:720px;overflow:hidden}</style>
</head>
<body>
<div class="ppt-slide ending-stage" type="ending">
  <h1>{{ENDING_TITLE}}</h1>
  <p>{{ENDING_SUBTITLE}}</p>
  <p>{{PAGE_FOOTER_LEFT}}</p>
</div>
</body>
</html>
"""

_ENDING_FILLED_HTML = """<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="UTF-8">
<title>感谢聆听</title>
<style>.ppt-slide{width:1280px;height:720px;overflow:hidden}</style>
</head>
<body>
<div class="ppt-slide ending-stage" type="ending">
  <h1>感谢聆听</h1>
  <p>把握高质量发展机遇</p>
  <p>金融行业趋势分析</p>
</div>
</body>
</html>
"""


def _agenda_template_path(pptx_root: str, style_id: str) -> str:
    return ppg._resolve_style_page_template_path(pptx_root, style_id, page_type="agenda")


def _configure_agenda_worker(
    llm_responses: list[str | BaseException],
    *,
    pptx_root: str,
    style_id: str,
    llm_calls: list[str],
    tool_calls: list[str],
    written_contents: list[str] | None = None,
    seed_html: str = _AGENDA_SEED_HTML,
    template_page_type: str = "agenda",
) -> ppg.PageWorkerNode:
    node = ppg.PageWorkerNode()
    queue = list(llm_responses)
    template_path = ppg._resolve_style_page_template_path(
        pptx_root,
        style_id,
        page_type=template_page_type,
    )

    async def _stream_llm(
        _prompt: str,
        _system_prompt: str = "",
        node_name: str | None = None,
        **_: Any,
    ) -> AsyncIterator[str]:
        llm_calls.append(str(node_name or ""))
        response = queue.pop(0)
        if isinstance(response, BaseException):
            raise response
        yield response

    async def _use_tool(tool_name: str, **kwargs: Any) -> dict[str, Any]:
        tool_calls.append(tool_name)
        if tool_name == "read_file":
            file_path = str(kwargs.get("file_path") or "")
            if file_path.replace("\\", "/") == template_path.replace("\\", "/"):
                return {"content": seed_html}
            return {"content": ""}
        if tool_name == "write_file":
            if written_contents is not None:
                written_contents.append(str(kwargs.get("content") or ""))
            return {"success": True}
        raise AssertionError(f"unexpected tool call: {tool_name}")

    node.set_runtime_callbacks(
        has_tool=lambda name: name in {"read_file", "write_file"},
        use_tool=_use_tool,
        stream_llm=_stream_llm,
    )
    return node


@pytest.mark.unit
@pytest.mark.parametrize(
    "style_id",
    sorted(ppg._AGENDA_TEMPLATE_FILL_STYLE_IDS),
)
def test_uses_agenda_template_fill_for_preset_and_custom(style_id: str) -> None:
    assert ppg._uses_agenda_template_fill(style_id, "agenda")
    assert not ppg._uses_agenda_template_fill(style_id, "data")
    assert ppg._uses_structural_template_fill(style_id, "cover")
    assert ppg._uses_structural_template_fill(style_id, "ending")
    assert not ppg._uses_structural_template_fill(style_id, "data")


@pytest.mark.unit
def test_has_unfilled_placeholders_detects_stage6_soft_gate() -> None:
    assert ppg._has_unfilled_placeholders(_AGENDA_SEED_HTML)
    assert not ppg._has_unfilled_placeholders(_AGENDA_FILLED_HTML)
    assert not ppg._has_unfilled_placeholders("{{lowercase_ignored}}")


@pytest.mark.unit
@pytest.mark.parametrize(
    "style_id",
    [
        "business-classic",
        "tech-minimal",
        "elegant-narrative",
        "industrial-tech",
        "custom",
    ],
)
def test_agenda_fill_prompt_is_seed_fill_not_freeform(style_id: str) -> None:
    prompt = ppg._build_agenda_template_fill_prompt(
        page_number=2,
        style_id=style_id,
        style_text="---\nfont-family: Test\n---\n",
        outline_page=_AGENDA_OUTLINE,
        outline_full="# outline\n" + _AGENDA_OUTLINE,
        seed_html=_AGENDA_SEED_HTML,
    )

    assert "只填槽" in prompt or "只替换" in prompt or "脚手架" in prompt
    assert "{{PAGE_TITLE}}" in prompt
    assert "agenda-stage" in prompt
    assert "推荐布局（agenda 类型" not in prompt
    assert "禁止" in prompt
    if style_id == "custom":
        assert "Stage 6 §3.6" in prompt
        assert "{{PAGE_CONTENT}}" in prompt
    else:
        assert "Stage 6 §3.5" in prompt
        assert "字面拷贝" in prompt
        assert "自创装饰" in prompt or "四章" in prompt


@pytest.mark.unit
@pytest.mark.parametrize(
    "style_id",
    [
        "business-classic",
        "tech-minimal",
        "elegant-narrative",
        "industrial-tech",
        "custom",
    ],
)
def test_page_worker_agenda_uses_template_fill_not_free_generation(
    tmp_path,
    style_id: str,
) -> None:
    pptx_root = _write_agenda_template(tmp_path, style_id)
    llm_calls: list[str] = []
    tool_calls: list[str] = []
    written_contents: list[str] = []
    node = _configure_agenda_worker(
        [_AGENDA_FILLED_HTML],
        pptx_root=pptx_root,
        style_id=style_id,
        llm_calls=llm_calls,
        tool_calls=tool_calls,
        written_contents=written_contents,
    )

    result = asyncio.run(
        node._execute(
            _worker_inputs(
                page_count=1,
                style_id=style_id,
                pptx_root=pptx_root,
                all_pages=[2],
                outline_pages={2: _AGENDA_OUTLINE},
                outline_text="# outline\n" + _AGENDA_OUTLINE,
            )
        )
    )

    assert llm_calls == ["p8_1_agenda_fill_2"]
    assert tool_calls == ["read_file", "write_file"]
    assert result["page_files"] == ["page-2.pptx.html"]
    assert result["missing_pages"] == []
    assert not ppg._has_unfilled_placeholders(written_contents[0])
    assert 'type="agenda"' in written_contents[0]
    assert "四章 · 十二节" not in written_contents[0]


@pytest.mark.unit
def test_page_worker_agenda_rejects_unfilled_placeholders(tmp_path) -> None:
    pptx_root = _write_agenda_template(tmp_path, "elegant-narrative")
    llm_calls: list[str] = []
    tool_calls: list[str] = []

    node = _configure_agenda_worker(
        [_AGENDA_SEED_HTML, _AGENDA_SEED_HTML],
        pptx_root=pptx_root,
        style_id="elegant-narrative",
        llm_calls=llm_calls,
        tool_calls=tool_calls,
    )

    result = asyncio.run(
        node._execute(
            _worker_inputs(
                page_count=1,
                style_id="elegant-narrative",
                pptx_root=pptx_root,
                all_pages=[2],
                outline_pages={2: _AGENDA_OUTLINE},
                outline_text=_AGENDA_OUTLINE,
            )
        )
    )

    assert llm_calls == ["p8_1_agenda_fill_2", "p8_1_agenda_fill_2"]
    assert tool_calls == ["read_file", "read_file"]
    assert result["page_files"] == []
    assert result["missing_pages"] == [2]


@pytest.mark.unit
def test_page_worker_content_page_still_uses_free_generation(tmp_path) -> None:
    pptx_root = _write_agenda_template(tmp_path, "business-classic")
    llm_calls: list[str] = []
    tool_calls: list[str] = []
    node = _configure_worker(
        [_VALID_HTML],
        llm_calls=llm_calls,
        tool_calls=tool_calls,
    )

    result = asyncio.run(
        node._execute(
            _worker_inputs(
                page_count=1,
                style_id="business-classic",
                pptx_root=pptx_root,
                outline_pages={
                    1: "### P1:\n- **类型**：data\n- **研究需求**：✅\n- **标题**：正文",
                },
                research_pages={1: "### P1:\n正文素材"},
            )
        )
    )

    assert llm_calls == ["p8_1_page_1"]
    assert result["missing_pages"] == []
    assert result["page_files"] == ["page-1.pptx.html"]


@pytest.mark.unit
def test_resolve_style_page_template_path_returns_string_without_pathlib() -> None:
    path = ppg._resolve_style_page_template_path(
        r"D:\skills\pptx-craft",
        "elegant-narrative",
        page_type="agenda",
    )
    assert isinstance(path, str)
    assert path.endswith("references/styles/elegant-narrative/agenda-template.html")


@pytest.mark.unit
def test_build_page_prompt_no_longer_injects_agenda_layout_authority() -> None:
    prompt = ppg._build_page_prompt(
        2,
        style_id="elegant-narrative",
        style_text="---\nfont-family: Test\n---\n",
        outline_page=_AGENDA_OUTLINE,
        research_page="",
    )
    assert "推荐布局（agenda 类型" not in prompt
    assert "agenda" not in ppg._PAGE_LAYOUT_TEMPLATES


@pytest.mark.unit
def test_ending_fill_prompt_forbids_content_page_layout() -> None:
    prompt = ppg._build_structural_template_fill_prompt(
        page_number=10,
        page_type="ending",
        template_page_type="ending",
        style_id="business-classic",
        style_text="---\nfont-family: Arial\n---\n",
        outline_page=_ENDING_OUTLINE,
        outline_full="",
        seed_html=_ENDING_SEED_HTML,
    )
    assert "ending-template.html" in prompt
    assert "禁止内容页元素" in prompt
    assert "感谢聆听" in prompt
    assert "推荐布局（ending 类型" not in prompt


@pytest.mark.unit
def test_page_worker_ending_uses_template_fill_not_free_generation(tmp_path) -> None:
    pptx_root = _write_style_template(tmp_path, "business-classic", "ending", _ENDING_SEED_HTML)
    llm_calls: list[str] = []
    written_contents: list[str] = []
    node = _configure_agenda_worker(
        [_ENDING_FILLED_HTML],
        llm_calls=llm_calls,
        tool_calls=[],
        written_contents=written_contents,
        pptx_root=pptx_root,
        style_id="business-classic",
        seed_html=_ENDING_SEED_HTML,
        template_page_type="ending",
    )

    result = asyncio.run(
        node._execute(
            _worker_inputs(
                page_count=8,
                style_id="business-classic",
                pptx_root=pptx_root,
                total_pages=10,
                outline_pages={10: _ENDING_OUTLINE},
                all_pages=[10],
            )
        )
    )

    assert llm_calls == ["p8_1_ending_fill_10"]
    assert result["missing_pages"] == []
    assert result["page_files"] == ["page-10.pptx.html"]
    assert "感谢聆听" in written_contents[0]
    assert "echarts" not in written_contents[0].lower()


_GOOD_CONTENT_HTML = """<!DOCTYPE html>
<html><body>
<div class="ppt-slide">
  <header><h1>标题</h1></header>
  <main><section>正文</section></main>
  <footer>页脚</footer>
</div>
</body></html>
"""

_BROKEN_CONTENT_HTML = """<!DOCTYPE html>
<html><body>
<div class="ppt-slide">
  <header><h1>标题</h1></header></div></div>
  <main><section>正文</section></main>
</div>
</body></html>
"""


def test_validate_slide_dom_accepts_normal_content_page() -> None:
    assert ppg._validate_slide_dom(_GOOD_CONTENT_HTML)


def test_validate_slide_dom_rejects_main_outside_slide() -> None:
    assert not ppg._validate_slide_dom(_BROKEN_CONTENT_HTML)
    assert not ppg._is_slide_exportable(_BROKEN_CONTENT_HTML)


def test_validate_slide_dom_rejects_malformed_llm_tokens() -> None:
    html = _GOOD_CONTENT_HTML.replace(
        "<header>",
        '<header class="border@none" style=".>',
    ).replace("</header>", "</.></header>")
    assert not ppg._validate_slide_dom(html)


def test_is_slide_exportable_ignores_malformed_tokens_when_structure_ok() -> None:
    html = _GOOD_CONTENT_HTML.replace(
        "<header>",
        '<header class="border@none" style=".>',
    )
    assert not ppg._validate_slide_dom(html)
    assert ppg._is_slide_exportable(html)


def test_extract_backup_timestamp() -> None:
    assert ppg._extract_backup_timestamp(
        "D:/pages/_backup/20260803065245/page-17.pptx.html"
    ) == "20260803065245"


_CHART_HEIGHT_BAD_HTML = """<!DOCTYPE html>
<html><body>
<div class="ppt-slide">
  <main class="flex-1 min-h-0 flex gap-4">
    <section class="flex-[3] min-h-0 flex flex-col gap-3">
      <div class="border border-gray3 bg-white flex flex-col" style="padding:10px;">
        <h3>氢能需求时序预测</h3>
        <div id="h2-chart" class="flex-1 min-h-0 w-full"></div>
      </div>
    </section>
  </main>
</div>
<script>echarts.init(document.getElementById('h2-chart'), null, {renderer:'svg'});</script>
</body></html>
"""

_CHART_HEIGHT_GOOD_HTML = """<!DOCTYPE html>
<html><body>
<div class="ppt-slide">
  <main class="flex-1 min-h-0 flex gap-4">
    <section class="flex-[3] min-h-0 flex flex-col gap-3">
      <div class="flex-1 min-h-0 border border-gray3 bg-white p-3 flex flex-col">
        <h3>减排贡献结构</h3>
        <div id="chart-decarbon" class="flex-1 min-h-0 w-full"></div>
      </div>
    </section>
  </main>
</div>
<script>echarts.init(document.getElementById('chart-decarbon'), null, {renderer:'svg'});</script>
</body></html>
"""

_CHART_HEIGHT_ENDING_HTML = """<!DOCTYPE html>
<html><body>
<div class="ppt-slide">
  <main class="flex-1 min-h-0 flex flex-col">
    <div class="border border-gray3 bg-white p-3 flex flex-col min-h-0">
      <span>双碳路径愿景</span>
      <div id="carbonPathChart" class="flex-1 min-h-0 w-full"></div>
    </div>
  </main>
</div>
<script>echarts.init(document.getElementById('carbonPathChart'), null, {renderer:'svg'});</script>
</body></html>
"""


def test_validate_chart_height_chain_rejects_collapsed_wrapper() -> None:
    assert not ppg._validate_chart_height_chain(_CHART_HEIGHT_BAD_HTML)


def test_validate_chart_height_chain_accepts_flex1_wrapper() -> None:
    assert ppg._validate_chart_height_chain(_CHART_HEIGHT_GOOD_HTML)


def test_validate_chart_height_chain_accepts_min_h0_wrapper() -> None:
    assert ppg._validate_chart_height_chain(_CHART_HEIGHT_ENDING_HTML)


def test_validate_chart_height_chain_skips_non_chart_page() -> None:
    assert ppg._validate_chart_height_chain(_GOOD_CONTENT_HTML)
