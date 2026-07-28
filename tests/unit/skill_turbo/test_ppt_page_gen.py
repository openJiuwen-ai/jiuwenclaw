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

    async def _use_tool(tool_name: str, **_: Any) -> dict[str, Any]:
        tool_calls.append(tool_name)
        if tool_name != "write_file":
            raise AssertionError(f"unexpected tool call: {tool_name}")
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
