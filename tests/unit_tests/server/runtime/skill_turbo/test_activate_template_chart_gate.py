# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""SkillTurbo activate-template-chart 图表 gate 单测。"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen import (
    PageWorkerNode,
    _fix_chart_scaffold_activation,
    _html_requires_activate_template_chart,
    _page_qualifies_for_chart_gate,
    _run_activate_template_chart_page,
    _validate_chart_mount_references,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.utils.bash_utils import (
    BashExecError,
    BashResult,
)


@pytest.mark.parametrize(
    ("page_type", "expected"),
    [
        ("content", True),
        ("data", True),
        ("agenda", False),
        ("cover", False),
        ("intro", False),
        ("section", False),
        ("ending", False),
    ],
)
def test_page_qualifies_for_chart_gate(page_type: str, expected: bool) -> None:
    assert _page_qualifies_for_chart_gate(page_type) is expected


@pytest.mark.asyncio
async def test_delete_page_file_uses_rm_bash() -> None:
    node = PageWorkerNode()
    bash_mock = AsyncMock(return_value=BashResult(exit_code=0, stdout="", stderr="", raw=""))
    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen.run_bash",
        bash_mock,
    ):
        await node._delete_page_file("/tmp/pages/page-3.pptx.html")
    cmd = bash_mock.await_args.kwargs.get("command") or bash_mock.await_args.args[1]
    assert "rm -f" in cmd
    assert "page-3.pptx.html" in cmd


@pytest.mark.asyncio
async def test_run_activate_template_chart_page_builds_per_page_args() -> None:
    node = PageWorkerNode()
    bash_mock = AsyncMock(
        return_value=BashResult(exit_code=0, stdout="ok", stderr="", raw="")
    )
    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen.cli_path",
        return_value="node /mock/cli.js activate-template-chart",
    ), patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen.run_bash",
        bash_mock,
    ):
        passed, detail, skipped = await _run_activate_template_chart_page(
            node,
            pages_dir="/tmp/pages",
            pptx_root="/tmp/pptx",
            page_num=3,
        )
    assert passed is True
    assert detail == ""
    assert skipped is False
    cmd = bash_mock.await_args.kwargs.get("command") or bash_mock.await_args.args[1]
    assert "--file" in cmd
    assert "page-3.pptx.html" in cmd
    assert "--pages" not in cmd


def test_html_requires_activate_template_chart_dormant_comment_is_false() -> None:
    html = """<!-- CHART_SCAFFOLD_BEGIN
<script data-pptx-chart-scaffold="v1">
const option = null;
</script>
CHART_SCAFFOLD_END -->"""
    assert _html_requires_activate_template_chart(html) is False


def test_html_requires_activate_template_chart_dormant_comment_plus_active_canonical() -> None:
    """注释块均为 dormant 时，仍须检出页内已暴露的 canonical active scaffold。"""
    html = """<!-- CHART_SCAFFOLD_BEGIN
<script>
const option = null;
</script>
CHART_SCAFFOLD_END -->
<script data-pptx-chart-scaffold="v1">const option = { series: [] };</script>"""
    assert _html_requires_activate_template_chart(html) is True


def test_html_requires_activate_template_chart_populated_comment_is_true() -> None:
    html = """<!-- CHART_SCAFFOLD_BEGIN
<script data-pptx-chart-scaffold="v1">
const option = { series: [] };
</script>
CHART_SCAFFOLD_END -->"""
    assert _html_requires_activate_template_chart(html) is True


def test_html_requires_activate_template_chart_active_canonical_is_true() -> None:
    html = '<script data-pptx-chart-scaffold="v1">const option = {};</script>'
    assert _html_requires_activate_template_chart(html) is True


def test_html_requires_activate_template_chart_plain_echarts_is_false() -> None:
    html = "<script>const option = { series: [] };</script>"
    assert _html_requires_activate_template_chart(html) is False


def test_html_requires_partial_dormant_comment_with_chart_container() -> None:
    """有容器但 option=null：dormant，不调 CLI（CLI 对 null option 会 exit 1）。"""
    html = """<div id="chart-1" class="w-full h-full"></div>
<!-- CHART_SCAFFOLD_BEGIN
<script>
const el = document.getElementById("chart-1");
const option = null;
</script>
CHART_SCAFFOLD_END -->"""
    assert _html_requires_activate_template_chart(html) is False


def test_html_requires_activated_plain_script_with_container_skips_cli() -> None:
    """普通 content-template 激活成功后无 data-pptx，不得因仅有容器再调 CLI。"""
    html = """<div id="chart-1" class="w-full h-full"></div>
<script>
const el = document.getElementById("chart-1");
const option = { series: [{ data: [1] }] };
</script>"""
    assert _html_requires_activate_template_chart(html) is False


def test_fix_chart_scaffold_keeps_null_option_commented() -> None:
    html = """<div id="chart-1"></div>
<!-- CHART_SCAFFOLD_BEGIN
<script>
const el = document.getElementById("chart-1");
const option = null;
</script>
CHART_SCAFFOLD_END -->"""
    fixed = _fix_chart_scaffold_activation(html)
    assert "CHART_SCAFFOLD_BEGIN" in fixed
    assert "const option = null" in fixed


def test_fix_chart_scaffold_unwraps_populated_option_with_container() -> None:
    html = """<div id="chart-1"></div>
<!-- CHART_SCAFFOLD_BEGIN
<script>
const el = document.getElementById("chart-1");
const option = { series: [{ data: [1] }] };
</script>
CHART_SCAFFOLD_END -->"""
    fixed = _fix_chart_scaffold_activation(html)
    assert "CHART_SCAFFOLD_BEGIN" not in fixed
    assert "const option = { series: [{ data: [1] }] }" in fixed


def test_fix_chart_scaffold_unwraps_when_template_instruction_mentions_null() -> None:
    """模板块注释含「const option = null」说明时，已填 option 仍应激活。"""
    html = """<div id="chart-1" class="w-full h-full"></div>
<!-- CHART_SCAFFOLD_BEGIN
<script>
/*
 *   3) 把下方 const option = null 的 null 替换为图表 option 对象。
 */
const el = document.getElementById("chart-1");
const option = { series: [{ data: [1, 2, 3], type: "bar" }] };
</script>
CHART_SCAFFOLD_END -->"""
    fixed = _fix_chart_scaffold_activation(html)
    assert "CHART_SCAFFOLD_BEGIN" not in fixed
    assert 'getElementById("chart-1")' in fixed
    assert _html_requires_activate_template_chart(html) is True


def test_fix_chart_scaffold_keeps_null_when_instruction_mentions_null() -> None:
    """说明文字含 const option = null，但可执行代码仍为 null 时保持 dormant。"""
    html = """<div id="chart-1"></div>
<!-- CHART_SCAFFOLD_BEGIN
<script>
/*
 *   3) 把下方 const option = null 的 null 替换为图表 option 对象。
 */
const el = document.getElementById("chart-1");
const option = null;
</script>
CHART_SCAFFOLD_END -->"""
    fixed = _fix_chart_scaffold_activation(html)
    assert "CHART_SCAFFOLD_BEGIN" in fixed
    assert _html_requires_activate_template_chart(html) is False


def test_fix_chart_scaffold_keeps_populated_option_without_container() -> None:
    html = """<!-- CHART_SCAFFOLD_BEGIN
<script>
const el = document.getElementById("chart-1");
const option = { series: [{ data: [1] }] };
</script>
CHART_SCAFFOLD_END -->"""
    fixed = _fix_chart_scaffold_activation(html)
    assert "CHART_SCAFFOLD_BEGIN" in fixed


@pytest.mark.asyncio
async def test_run_activate_template_chart_page_failure_returns_detail() -> None:
    node = PageWorkerNode()
    bash_mock = AsyncMock(
        return_value=BashResult(
            exit_code=1,
            stdout="",
            stderr="const option = null",
            raw="",
        )
    )
    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen.cli_path",
        return_value="node /mock/cli.js activate-template-chart",
    ), patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen.run_bash",
        bash_mock,
    ):
        passed, detail, skipped = await _run_activate_template_chart_page(
            node,
            pages_dir="/tmp/pages",
            pptx_root="/tmp/pptx",
            page_num=3,
        )
    assert passed is False
    assert "option = null" in detail
    assert skipped is False


@pytest.mark.asyncio
async def test_run_activate_template_chart_page_cli_unavailable_skips() -> None:
    node = PageWorkerNode()
    with patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen.cli_path",
        side_effect=BashExecError("cli missing"),
    ):
        passed, detail, skipped = await _run_activate_template_chart_page(
            node,
            pages_dir="/tmp/pages",
            pptx_root="/tmp/pptx",
            page_num=3,
        )
    assert passed is True
    assert skipped is True


@pytest.mark.asyncio
async def test_run_page_pipeline_chart_gate_failure_deletes_file_and_retries(
    tmp_path: Path,
) -> None:
    node = PageWorkerNode()
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    page_path = pages_dir / "page-3.pptx.html"
    outline_page = "**类型**：data\n**标题**：测试页"
    html_ok = """<!-- CHART_SCAFFOLD_BEGIN
<script data-pptx-chart-scaffold="v1">const option = { series: [] };</script>
CHART_SCAFFOLD_END -->"""

    async def _write_side_effect(path: str, content: str) -> bool:
        Path(path).write_text(content, encoding="utf-8")
        return True

    generate_mock = AsyncMock(
        side_effect=[
            (html_ok, html_ok, ""),
            (html_ok, html_ok, ""),
        ]
    )
    write_mock = AsyncMock(side_effect=_write_side_effect)
    chart_mock = AsyncMock(
        side_effect=[
            (False, "const option = null", False),
            (True, "", False),
        ]
    )

    with patch.object(node, "_generate_one", generate_mock), patch.object(
        node, "_write_file", write_mock
    ), patch.object(node, "_delete_page_file", AsyncMock()) as delete_mock, patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_activate_template_chart_page",
        chart_mock,
    ):
        result = await node._run_page_pipeline(
            page_num=3,
            pages_dir=str(pages_dir),
            style_id="business-classic",
            style_text="",
            outline_page=outline_page,
            research_page="",
            outline_is_full=False,
            gen_retry_round=1,
            image_map={},
            pptx_root="/tmp/pptx",
        )

    assert result["missing"] is False
    assert generate_mock.await_count == 2
    assert chart_mock.await_count == 2
    assert delete_mock.await_count == 1
    assert page_path.exists()


@pytest.mark.asyncio
async def test_run_page_pipeline_chart_gate_exhausted_marks_missing_and_deletes_file(
    tmp_path: Path,
) -> None:
    node = PageWorkerNode()
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    outline_page = "**类型**：data\n**标题**：测试页"
    html_bad = """<!-- CHART_SCAFFOLD_BEGIN
<script data-pptx-chart-scaffold="v1">const option = { series: [] };</script>
CHART_SCAFFOLD_END -->"""
    delete_mock = AsyncMock()

    with patch.object(
        node,
        "_generate_one",
        AsyncMock(return_value=(html_bad, html_bad, "")),
    ), patch.object(node, "_write_file", AsyncMock(return_value=True)), patch.object(
        node, "_delete_page_file", delete_mock
    ), patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_activate_template_chart_page",
        AsyncMock(return_value=(False, "const option = null", False)),
    ):
        result = await node._run_page_pipeline(
            page_num=3,
            pages_dir=str(pages_dir),
            style_id="business-classic",
            style_text="",
            outline_page=outline_page,
            research_page="",
            outline_is_full=False,
            gen_retry_round=0,
            image_map={},
            pptx_root="/tmp/pptx",
        )

    assert result["missing"] is True
    assert delete_mock.await_count >= 1


@pytest.mark.asyncio
async def test_run_page_pipeline_skips_chart_gate_for_agenda(tmp_path: Path) -> None:
    node = PageWorkerNode()
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    outline_page = "**类型**：agenda\n**标题**：目录"
    html_ok = "<html><body><main>agenda</main></body></html>"
    chart_mock = AsyncMock()

    with patch.object(
        node,
        "_generate_one",
        AsyncMock(return_value=(html_ok, html_ok, "")),
    ), patch.object(node, "_write_file", AsyncMock(return_value=True)), patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_activate_template_chart_page",
        chart_mock,
    ):
        result = await node._run_page_pipeline(
            page_num=2,
            pages_dir=str(pages_dir),
            style_id="business-classic",
            style_text="",
            outline_page=outline_page,
            research_page="",
            outline_is_full=False,
            gen_retry_round=0,
            image_map={},
            pptx_root="/tmp/pptx",
        )

    assert result["missing"] is False
    chart_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_page_pipeline_skips_chart_gate_for_dormant_scaffold(
    tmp_path: Path,
) -> None:
    node = PageWorkerNode()
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    outline_page = "**类型**：data\n**标题**：纯文字页"
    html_ok = """<!-- CHART_SCAFFOLD_BEGIN
<script data-pptx-chart-scaffold="v1">const option = null;</script>
CHART_SCAFFOLD_END -->"""
    chart_mock = AsyncMock()

    with patch.object(
        node,
        "_generate_one",
        AsyncMock(return_value=(html_ok, html_ok, "")),
    ), patch.object(node, "_write_file", AsyncMock(return_value=True)), patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_activate_template_chart_page",
        chart_mock,
    ):
        result = await node._run_page_pipeline(
            page_num=3,
            pages_dir=str(pages_dir),
            style_id="business-classic",
            style_text="",
            outline_page=outline_page,
            research_page="",
            outline_is_full=False,
            gen_retry_round=0,
            image_map={},
            pptx_root="/tmp/pptx",
        )

    assert result["missing"] is False
    chart_mock.assert_not_called()


@pytest.mark.asyncio
async def test_run_page_pipeline_skips_chart_gate_for_partial_dormant_container(
    tmp_path: Path,
) -> None:
    """partial dormant（容器 + option=null 注释）不调 CLI，避免 exit 1 导致缺页。"""
    node = PageWorkerNode()
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    outline_page = "**类型**：data\n**标题**：图表页"
    html_partial = """<div id="chart-1"></div>
<!-- CHART_SCAFFOLD_BEGIN
<script>
const el = document.getElementById("chart-1");
const option = null;
</script>
CHART_SCAFFOLD_END -->"""
    chart_mock = AsyncMock(return_value=(True, "", False))

    with patch.object(
        node,
        "_generate_one",
        AsyncMock(return_value=(html_partial, html_partial, "")),
    ), patch.object(node, "_write_file", AsyncMock(return_value=True)), patch(
        "jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_page_gen._run_activate_template_chart_page",
        chart_mock,
    ):
        result = await node._run_page_pipeline(
            page_num=5,
            pages_dir=str(pages_dir),
            style_id="tech-minimal",
            style_text="",
            outline_page=outline_page,
            research_page="",
            outline_is_full=False,
            gen_retry_round=0,
            image_map={},
            pptx_root="/tmp/pptx",
        )

    assert result["missing"] is False
    chart_mock.assert_not_called()


def _chart_page_html(*, container_id: str, get_element_id: str) -> str:
    return f"""<div id="{container_id}" class="w-full h-full"></div>
<script>
const el = document.getElementById("{get_element_id}");
const chart = echarts.init(el, null, {{renderer:'svg'}});
const option = {{ series: [] }};
chart.setOption(option);
</script>"""


def test_validate_chart_mount_references_matching_ids() -> None:
    html = _chart_page_html(container_id="chart-1", get_element_id="chart-1")
    assert _validate_chart_mount_references(html) is True


def test_validate_chart_mount_references_id_mismatch() -> None:
    html = _chart_page_html(container_id="chart-market", get_element_id="chart-1")
    assert _validate_chart_mount_references(html) is False


def test_validate_chart_mount_references_no_echarts_passes() -> None:
    html = "<div><p>纯文字页</p></div>"
    assert _validate_chart_mount_references(html) is True


def test_validate_chart_mount_references_skips_commented_scaffold() -> None:
    html = """<div id="chart-1"></div>
<!-- CHART_SCAFFOLD_BEGIN
<script>
const el = document.getElementById("chart-missing");
const chart = echarts.init(el);
</script>
CHART_SCAFFOLD_END -->"""
    assert _validate_chart_mount_references(html) is True


def test_validate_chart_mount_references_dual_charts_both_match() -> None:
    html = """<div id="chart-1"></div><div id="chart-2"></div>
<script>
const c1 = echarts.init(document.getElementById("chart-1"));
const c2 = echarts.init(document.getElementById("chart-2"));
c1.setOption({}); c2.setOption({});
</script>"""
    assert _validate_chart_mount_references(html) is True


def test_validate_chart_mount_references_dual_charts_one_mismatch() -> None:
    html = """<div id="chart-1"></div><div id="chart-market"></div>
<script>
const c1 = echarts.init(document.getElementById("chart-1"));
const c2 = echarts.init(document.getElementById("chart-2"));
c1.setOption({}); c2.setOption({});
</script>"""
    assert _validate_chart_mount_references(html) is False
