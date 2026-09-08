# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo 轻量 check-layout 接入单测。"""

from __future__ import annotations

import json
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen import (
    PageWorkerNode,
    _collect_check_layout_page_nums,
    _page_qualifies_for_check_layout,
    _parse_check_layout_hard_failures,
    _run_check_layout,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.utils.bash_utils import (
    BashResult,
)


@pytest.mark.parametrize(
    ("page_type", "expected"),
    [
        ("content", True),
        ("data", True),
        ("agenda", True),
        ("cover", False),
        ("intro", False),
        ("section", False),
        ("ending", False),
    ],
)
def test_page_qualifies_for_check_layout(page_type: str, expected: bool) -> None:
    assert _page_qualifies_for_check_layout(page_type) is expected


def test_collect_check_layout_page_nums_includes_content_and_agenda_only() -> None:
    outline_pages = {
        1: "**类型**：cover\n**标题**：封面",
        2: "**类型**：agenda\n**标题**：目录",
        3: "**类型**：content\n**标题**：正文",
        4: "**类型**：section\n**标题**：章节",
    }
    selected = _collect_check_layout_page_nums([1, 2, 3, 4], outline_pages)
    assert selected == [2, 3]


def test_parse_check_layout_hard_failures_text_output() -> None:
    output = """
Page 3 FAIL
  overflow: body text exceeds card
  h-whitespace-warning: ignored
Page 2
  chart-label-overlap: series labels collide
"""
    failures = _parse_check_layout_hard_failures(output)
    assert failures[3] == ["overflow"]
    assert failures[2] == ["chart-label-overlap"]


def test_parse_check_layout_hard_failures_json_output() -> None:
    payload = {
        "pages": {
            "5": {"issues": ["footer-intrusion", "v-gap-warning"]},
            "6": ["slide-boundary-overflow"],
        }
    }
    failures = _parse_check_layout_hard_failures(json.dumps(payload))
    assert failures[5] == ["footer-intrusion"]
    assert failures[6] == ["slide-boundary-overflow"]


@pytest.mark.asyncio
async def test_run_check_layout_builds_pages_and_density_args() -> None:
    node = PageWorkerNode()
    bash_mock = AsyncMock(
        return_value=BashResult(exit_code=0, stdout="all ok", stderr="", raw="")
    )
    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen.cli_path",
        return_value='node "/tmp/pptx-craft/packages/cli/dist/cli.js" check-layout',
    ), patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen.run_bash",
        bash_mock,
    ):
        failures, skipped = await _run_check_layout(
            node,
            pages_dir="/tmp/pages",
            pptx_root="/tmp/pptx-craft",
            page_nums=[2, 3, 5],
            density="lean",
        )
    assert failures == {}
    assert skipped is False
    cmd = bash_mock.await_args.args[1]
    assert "check-layout" in cmd
    assert "--pages 2,3,5" in cmd
    assert "--density lean" in cmd
    assert '"/tmp/pages"' in cmd


@pytest.mark.asyncio
async def test_run_check_layout_cli_unavailable_skips_without_blocking() -> None:
    node = PageWorkerNode()
    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen.run_bash",
        AsyncMock(side_effect=RuntimeError("bash down")),
    ):
        failures, skipped = await _run_check_layout(
            node,
            pages_dir="/tmp/pages",
            pptx_root="/tmp/pptx-craft",
            page_nums=[2],
        )
    assert failures == {}
    assert skipped is True


@pytest.mark.asyncio
async def test_apply_check_layout_pass_retries_once_and_warns_without_missing() -> None:
    node = PageWorkerNode()
    node._read_file = AsyncMock(return_value="<html>old</html>")  # type: ignore[method-assign]
    node._generate_one = AsyncMock(return_value=("<html>new</html>", "", ""))  # type: ignore[method-assign]
    node._write_file = AsyncMock(return_value=True)  # type: ignore[method-assign]

    initial_failures = {3: ["overflow"]}
    recheck_failures = {3: ["overflow"]}
    calls = {"n": 0}

    async def fake_run_check_layout(_node, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return initial_failures, False
        return recheck_failures, False

    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_check_layout",
        side_effect=fake_run_check_layout,
    ):
        result = await node._apply_check_layout_pass(
            pages_dir="/tmp/pages",
            pptx_root="/tmp/pptx-craft",
            successful_pages=[1, 2, 3],
            outline_pages={
                1: "**类型**：cover\n**标题**：封面",
                2: "**类型**：agenda\n**标题**：目录",
                3: "**类型**：content\n**标题**：正文",
            },
            research_pages={},
            outline_full="outline",
            style_id="business-classic",
            style_text="style",
            image_map={},
            designer_md_text="",
            user_query="",
            total_pages=3,
        )

    assert result["layout_retry_pages"] == [3]
    assert result["layout_warning_pages"] == [3]
    assert result["layout_check_skipped"] is False
    node._generate_one.assert_awaited_once()
    assert node._write_file.await_count >= 1


@pytest.mark.asyncio
async def test_apply_check_layout_pass_retries_failed_pages_concurrently() -> None:
    """失败页再填槽须 asyncio.gather 并发，避免串行叠墙钟。"""
    import asyncio

    node = PageWorkerNode()
    node._read_file = AsyncMock(return_value="<html>old</html>")  # type: ignore[method-assign]
    node._write_file = AsyncMock(return_value=True)  # type: ignore[method-assign]

    in_flight = 0
    max_in_flight = 0
    lock = asyncio.Lock()

    async def slow_generate(*_args, **_kwargs):
        nonlocal in_flight, max_in_flight
        async with lock:
            in_flight += 1
            max_in_flight = max(max_in_flight, in_flight)
        await asyncio.sleep(0.05)
        async with lock:
            in_flight -= 1
        return ("<html>new</html>", "", "")

    node._generate_one = AsyncMock(side_effect=slow_generate)  # type: ignore[method-assign]

    calls = {"n": 0}

    async def fake_run_check_layout(_node, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {2: ["overflow"], 3: ["v-gap"]}, False
        return {}, False

    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_check_layout",
        side_effect=fake_run_check_layout,
    ):
        result = await node._apply_check_layout_pass(
            pages_dir="/tmp/pages",
            pptx_root="/tmp/pptx-craft",
            successful_pages=[1, 2, 3],
            outline_pages={
                1: "**类型**：cover\n**标题**：封面",
                2: "**类型**：content\n**标题**：A",
                3: "**类型**：content\n**标题**：B",
            },
            research_pages={},
            outline_full="outline",
            style_id="business-classic",
            style_text="style",
            image_map={},
            designer_md_text="",
            user_query="",
            total_pages=3,
        )

    assert result["layout_retry_pages"] == [2, 3]
    assert result["layout_warning_pages"] == []
    assert node._generate_one.await_count == 2
    assert max_in_flight >= 2


@pytest.mark.asyncio
async def test_layout_rewrite_chart_gate_failure_reverts_previous_html() -> None:
    """再填写盘后 chart CLI 失败须回退旧 HTML，不进 missing / 不进 retry 复检。"""
    node = PageWorkerNode()
    previous = "<html>old-ok-chart</html>"
    rewritten = (
        '<script data-pptx-chart-scaffold="v1">const option = { series: [] };</script>'
    )
    node._read_file = AsyncMock(return_value=previous)  # type: ignore[method-assign]
    node._generate_one = AsyncMock(return_value=(rewritten, "", ""))  # type: ignore[method-assign]
    written: list[str] = []

    async def capture_write(_path: str, content: str) -> bool:
        written.append(content)
        return True

    node._write_file = AsyncMock(side_effect=capture_write)  # type: ignore[method-assign]

    calls = {"n": 0}

    async def fake_run_check_layout(_node, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return {3: ["overflow"]}, False
        return {}, False

    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_check_layout",
        side_effect=fake_run_check_layout,
    ), patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_activate_template_chart_page",
        AsyncMock(return_value=(False, "option invalid", False)),
    ) as chart_mock:
        result = await node._apply_check_layout_pass(
            pages_dir="/tmp/pages",
            pptx_root="/tmp/pptx-craft",
            successful_pages=[1, 3],
            outline_pages={
                1: "**类型**：cover\n**标题**：封面",
                3: "**类型**：content\n**标题**：正文",
            },
            research_pages={},
            outline_full="outline",
            style_id="business-classic",
            style_text="style",
            image_map={},
            designer_md_text="",
            user_query="",
            total_pages=3,
        )

    chart_mock.assert_awaited_once()
    assert result["layout_retry_pages"] == []
    assert result["layout_warning_pages"] == [3]
    assert written[0] == rewritten
    assert written[-1] == previous
    # 初检 1 次；再填失败不进入复检
    assert calls["n"] == 1


@pytest.mark.asyncio
async def test_apply_check_layout_pass_skips_when_no_eligible_pages() -> None:
    node = PageWorkerNode()
    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_check_layout",
        AsyncMock(),
    ) as run_mock:
        result = await node._apply_check_layout_pass(
            pages_dir="/tmp/pages",
            pptx_root="/tmp/pptx-craft",
            successful_pages=[1, 4],
            outline_pages={1: "**类型**：cover\n**标题**：封面", 4: "**类型**：ending\n**标题**：结尾"},
            research_pages={},
            outline_full="outline",
            style_id="business-classic",
            style_text="style",
            image_map={},
            designer_md_text="",
            user_query="",
            total_pages=4,
        )
    run_mock.assert_not_awaited()
    assert result == {
        "layout_check_skipped": False,
        "layout_warning_pages": [],
        "layout_retry_pages": [],
    }
