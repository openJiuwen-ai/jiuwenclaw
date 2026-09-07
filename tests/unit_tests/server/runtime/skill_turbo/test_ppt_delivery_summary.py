from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import pytest

from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.delivery import DeliveryNode
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.delivery_summary import (
    DELIVERY_SUMMARY_START,
    build_delivery_summary_skeleton,
    is_backup_listing_path,
    parse_outline_pages,
)
from jiuwenswarm.server.runtime.skill_turbo.skill_codes.ppt.ppt_gen_root import PPTGenRootNode
from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
    _wrap_skill_turbo_result,
    clear_pending_ppt_delivery_summary,
    emit_pending_ppt_delivery_summary,
    take_pending_ppt_delivery_summary,
)

_SAMPLE_OUTLINE = """# 大纲：杭州旅游

**受众**：游客
**总页数**：3
**叙事主线**：一日游路线与必看景点
**输入类型**：topic
**搜索模式**：off

## 页面规划

### P1: 封面
- **类型**：cover
- **研究需求**：❌
- **标题**：杭州一日游
- **内容概要**：西湖与城市印象
- **研究查询**：-
- **数据需求**：-

### P2: 行程
- **类型**：agenda
- **研究需求**：❌
- **标题**：上午西湖下午灵隐
- **内容概要**：按时间排列的游览顺序
- **研究查询**：-
- **数据需求**：-

### P3: 结束
- **类型**：ending
- **研究需求**：❌
- **标题**：感谢聆听
- **内容概要**：欢迎再次到访
- **研究查询**：-
- **数据需求**：-
"""


def test_backup_listing_path_detects_nested_backup_html() -> None:
    assert is_backup_listing_path("pages/_backup/page-1.pptx.html") is True
    assert is_backup_listing_path(r"pages\\_backup\\page-1.pptx.html") is True
    assert is_backup_listing_path("pages/page-1.pptx.html") is False
    assert is_backup_listing_path("_backup") is True


def test_parse_listing_drops_backup_pages() -> None:
    node = DeliveryNode()
    files = node._parse_listing(
        [
            "pages/page-1.pptx.html",
            "pages/_backup/page-1.pptx.html",
            "pages/_backup/page-2.pptx.html",
            "pages/_backup/page-3.pptx.html",
            "pages/page-2.pptx.html",
            "pages/page-3.pptx.html",
        ]
    )
    assert files == ["page-1.pptx.html", "page-2.pptx.html", "page-3.pptx.html"]


@pytest.mark.asyncio
async def test_check_pages_ignores_backup_html(monkeypatch: pytest.MonkeyPatch) -> None:
    node = DeliveryNode()
    monkeypatch.setattr(node, "has_tool", lambda name: name == "glob")

    async def call_tool(_name: str, **_kwargs: Any) -> list[str]:
        return [
            "pages/page-1.pptx.html",
            "pages/_backup/page-1.pptx.html",
            "pages/_backup/page-2.pptx.html",
            "pages/_backup/page-3.pptx.html",
            "pages/page-2.pptx.html",
            "pages/page-3.pptx.html",
        ]

    monkeypatch.setattr(node, "call_tool", call_tool)
    assert await node._check_pages("/tmp/pages", 3) is True


def test_parse_outline_pages_reads_titles() -> None:
    pages = parse_outline_pages(_SAMPLE_OUTLINE)
    assert pages[0] == (1, "杭州一日游", "西湖与城市印象")
    assert pages[-1][1] == "感谢聆听"


def test_build_delivery_summary_skeleton_uses_literal_start_and_real_pages() -> None:
    text = build_delivery_summary_skeleton(
        pptx_filename="杭州旅游.pptx",
        total_pages=3,
        delivery_status="ok",
        send_file_status="sent",
        pages_ok=True,
        outline_text=_SAMPLE_OUTLINE,
        topic="杭州旅游",
        style_id="business-classic",
        speaker_notes_status="skipped",
        need_speaker_notes=False,
        has_documents=False,
        image_map_path="",
        export_status="ok",
    )
    assert text.startswith(DELIVERY_SUMMARY_START)
    assert "《杭州旅游.pptx》" in text
    assert "共 3 页" in text
    assert "- P1：杭州一日游 - 西湖与城市印象" in text
    assert "未请求演讲备注" in text
    assert "文件位置" not in text
    assert "D:/" not in text and "C:/" not in text


def test_build_delivery_summary_keeps_only_real_pages_when_fewer_than_three() -> None:
    outline = """### P1: 封面
- **标题**：仅一页
- **内容概要**：杭州
"""
    text = build_delivery_summary_skeleton(
        pptx_filename="杭州旅游.pptx",
        total_pages=1,
        delivery_status="ok",
        send_file_status="sent",
        pages_ok=True,
        outline_text=outline,
        topic="杭州旅游",
        style_id="tech-minimal",
        export_status="ok",
    )
    assert "- P1：仅一页 - 杭州" in text
    assert "- P2：" not in text
    assert "- P3：" not in text


@pytest.mark.asyncio
async def test_p10_emits_skeleton_when_send_succeeds(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pptx = tmp_path / "杭州旅游.pptx"
    pptx.write_bytes(b"dummy-pptx")
    (tmp_path / "outline.md").write_text(_SAMPLE_OUTLINE, encoding="utf-8")
    pages_dir = tmp_path / "pages"
    pages_dir.mkdir()
    node = DeliveryNode()
    monkeypatch.setattr(
        node,
        "has_tool",
        lambda name: name in {"send_file_to_user", "glob", "read_file"},
    )

    async def call_tool(name: str, **kwargs: Any) -> Any:
        if name == "send_file_to_user":
            return "成功发送 1 个文件"
        if name == "glob":
            return [
                str(pages_dir / "page-1.pptx.html"),
                str(pages_dir / "_backup" / "page-1.pptx.html"),
            ]
        if name == "read_file":
            return SimpleNamespace(success=True, data={"content": _SAMPLE_OUTLINE})
        raise AssertionError(name)

    monkeypatch.setattr(node, "call_tool", call_tool)
    result = await node._execute(
        {
            "output_dir": str(tmp_path),
            "pages_dir": str(pages_dir),
            "pptx_path": str(pptx),
            "pptx_filename": "杭州旅游.pptx",
            "export_status": "ok",
            "page_count": 0,
            "total_pages": 1,
            "topic": "杭州旅游",
            "style_id": "business-classic",
        }
    )
    assert result["delivery_status"] == "ok"
    assert result["send_file_status"] == "sent"
    assert str(result["delivery_summary"]).startswith(DELIVERY_SUMMARY_START)
    assert result["__artifact__"]["delivery_summary"].startswith(DELIVERY_SUMMARY_START)


@pytest.mark.asyncio
async def test_p10_does_not_emit_skeleton_when_send_fails(
    tmp_path, monkeypatch: pytest.MonkeyPatch
) -> None:
    pptx = tmp_path / "杭州旅游.pptx"
    pptx.write_bytes(b"dummy-pptx")
    node = DeliveryNode()
    monkeypatch.setattr(node, "has_tool", lambda name: name in {"send_file_to_user", "glob"})

    async def call_tool(name: str, **_kwargs: Any) -> Any:
        if name == "send_file_to_user":
            return "发送文件失败"
        if name == "glob":
            return ["page-1.pptx.html"]
        raise AssertionError(name)

    monkeypatch.setattr(node, "call_tool", call_tool)
    result = await node._execute(
        {
            "output_dir": str(tmp_path),
            "pages_dir": str(tmp_path / "pages"),
            "pptx_path": str(pptx),
            "pptx_filename": "杭州旅游.pptx",
            "export_status": "ok",
            "page_count": 1,
            "total_pages": 1,
        }
    )
    assert result["send_file_status"] == "failed"
    assert result["delivery_summary"] == ""


@pytest.mark.asyncio
async def test_root_stream_does_not_emit_summary_delta_after_flow_complete(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """骨架不得在计划结束后以无 task_id 的 chat.delta 发出（会与过程尾同桶）。"""
    root = PPTGenRootNode()
    summary = f"{DELIVERY_SUMMARY_START}\n\n✅ 已完成：PPT 生成\n"

    async def should_skip(_subplan, _inputs) -> bool:
        return False

    async def run_subplan_stream(subplan, inputs, results, **_kwargs):
        if subplan is root._p1:
            inputs["has_documents"] = True
        if subplan is root._p3:
            inputs["doc_parse_ok"] = True
        if subplan is root._delivery:
            inputs["delivery_summary"] = summary
        result = {"node": subplan.plan_name, "status": "ok"}
        results.append({"node": subplan.plan_name, "status": "ok", "result": result})
        yield result

    monkeypatch.setattr(root, "should_skip_subplan", should_skip)
    monkeypatch.setattr(root, "_run_subplan_stream", run_subplan_stream)

    chunks = [chunk async for chunk in root._execute_stream({})]
    assert chunks[-1]["message"] == "PPT生成任务流执行完成"
    assert all(chunk.get("event_type") != "chat.delta" for chunk in chunks)
    assert all(
        DELIVERY_SUMMARY_START not in str(chunk.get("content") or "")
        for chunk in chunks
    )


def test_wrap_skill_turbo_result_queues_ppt_skeleton_for_post_tool_emit() -> None:
    clear_pending_ppt_delivery_summary()
    skeleton = f"{DELIVERY_SUMMARY_START}\n\n✅ 已完成：PPT 生成\n"
    wrapped = _wrap_skill_turbo_result(
        {"success": True, "result": "任务已完成"},
        {
            "p10_delivery": {
                "info": {"send_file_status": "sent", "delivery_summary_emitted": True},
                "files": [],
                "delivery_summary": skeleton,
            }
        },
    )
    assert DELIVERY_SUMMARY_START not in wrapped["result"]
    assert "流式通道" in wrapped["result"]
    assert "无需在本回合重复输出" in wrapped["result"]
    assert "You should now summarize" not in wrapped["result"]
    assert take_pending_ppt_delivery_summary() == skeleton.strip()
    assert take_pending_ppt_delivery_summary() == ""


def test_wrap_skill_turbo_result_keeps_generic_hint_without_ppt_summary() -> None:
    clear_pending_ppt_delivery_summary()
    wrapped = _wrap_skill_turbo_result({"success": True, "result": "任务已完成"}, {})
    assert "You should now summarize" in wrapped["result"]
    assert "逐字输出" not in wrapped["result"]
    assert take_pending_ppt_delivery_summary() == ""


@pytest.mark.asyncio
async def test_emit_pending_ppt_delivery_summary_writes_llm_output() -> None:
    clear_pending_ppt_delivery_summary()
    skeleton = f"{DELIVERY_SUMMARY_START}\n\n✅ 已完成：PPT 生成\n"
    from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
        set_pending_ppt_delivery_summary,
    )

    set_pending_ppt_delivery_summary(skeleton)
    written: list[Any] = []

    class _Session:
        async def write_stream(self, output: Any) -> None:
            written.append(output)

    assert await emit_pending_ppt_delivery_summary(_Session()) is True
    assert len(written) == 1
    assert written[0].type == "llm_output"
    assert written[0].payload["content"].startswith(DELIVERY_SUMMARY_START)
    assert take_pending_ppt_delivery_summary() == ""


def _make_tool_call_ctx(
    *,
    tool_name: str,
    tool_result: Any = None,
    session: Any = None,
    exception: Exception | None = None,
) -> Any:
    from openjiuwen.core.single_agent.rail.base import ToolCallInputs

    return SimpleNamespace(
        inputs=ToolCallInputs(
            tool_call=None,
            tool_name=tool_name,
            tool_args={},
            tool_result=tool_result,
            tool_msg=None,
        ),
        session=session,
        exception=exception,
    )


@pytest.mark.asyncio
async def test_delivery_summary_rail_emits_after_skill_acceleration_exec() -> None:
    from jiuwenswarm.server.runtime.skill_turbo.rails.delivery_summary_rail import (
        SkillTurboDeliverySummaryRail,
    )
    from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
        set_pending_ppt_delivery_summary,
    )

    clear_pending_ppt_delivery_summary()
    skeleton = f"{DELIVERY_SUMMARY_START}\n\n✅ 已完成：PPT 生成\n"
    set_pending_ppt_delivery_summary(skeleton)
    written: list[Any] = []

    class _Session:
        async def write_stream(self, output: Any) -> None:
            written.append(output)

    rail = SkillTurboDeliverySummaryRail()
    assert rail.priority > 80
    await rail.after_tool_call(
        _make_tool_call_ctx(
            tool_name="skill_acceleration_exec",
            tool_result={"success": True},
            session=_Session(),
        )
    )
    assert len(written) == 1
    assert written[0].payload["content"].startswith(DELIVERY_SUMMARY_START)
    assert take_pending_ppt_delivery_summary() == ""


@pytest.mark.asyncio
async def test_delivery_summary_rail_ignores_other_tools() -> None:
    from jiuwenswarm.server.runtime.skill_turbo.rails.delivery_summary_rail import (
        SkillTurboDeliverySummaryRail,
    )
    from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
        set_pending_ppt_delivery_summary,
    )

    clear_pending_ppt_delivery_summary()
    skeleton = f"{DELIVERY_SUMMARY_START}\n\n✅ 已完成：PPT 生成\n"
    set_pending_ppt_delivery_summary(skeleton)

    await SkillTurboDeliverySummaryRail().after_tool_call(
        _make_tool_call_ctx(tool_name="bash", tool_result={"ok": True}, session=object())
    )
    assert take_pending_ppt_delivery_summary() == skeleton.strip()


@pytest.mark.asyncio
async def test_delivery_summary_rail_clears_pending_on_tool_interrupt() -> None:
    from jiuwenswarm.server.runtime.skill_turbo.rails.delivery_summary_rail import (
        SkillTurboDeliverySummaryRail,
    )
    from jiuwenswarm.server.runtime.skill_turbo.skill_turbo_tools import (
        set_pending_ppt_delivery_summary,
    )

    class ToolInterruptException(Exception):
        def __init__(self) -> None:
            self.request = object()

    clear_pending_ppt_delivery_summary()
    set_pending_ppt_delivery_summary(f"{DELIVERY_SUMMARY_START}\n\n✅ 已完成\n")
    written: list[Any] = []

    class _Session:
        async def write_stream(self, output: Any) -> None:
            written.append(output)

    await SkillTurboDeliverySummaryRail().after_tool_call(
        _make_tool_call_ctx(
            tool_name="skill_acceleration_exec",
            tool_result=ToolInterruptException(),
            session=_Session(),
        )
    )
    assert written == []
    assert take_pending_ppt_delivery_summary() == ""
