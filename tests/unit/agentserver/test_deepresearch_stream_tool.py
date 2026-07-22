# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""deepresearch_stream tool 集成单测(mock subprocess + 凭据桥接单测)。

测试用 `deepresearch_stream._func(...)` 直接 await 原始 async 函数,绕过 LocalFunction
的 schema/trigger 机制,聚焦 spawn+route+outcome 逻辑。
"""
import asyncio
import base64
import hashlib
import json

import os
import sys

import pytest
from unittest.mock import AsyncMock, patch

from jiuwenclaw.agentserver.tools import deepresearch_tools as dt


class _Proc:
    """假 subprocess:stdout 是 async generator,returncode/terminate/kill/wait 齐全。"""

    def __init__(self, lines, stderr_lines=None):
        self._lines = [l.encode() for l in lines]
        self._stderr = b"".join(s.encode() for s in (stderr_lines or []))
        self.returncode = 0

    @property
    def stdout(self):
        async def gen():
            for b in self._lines:
                yield b

        return gen()

    @property
    def stderr(self):
        data = self._stderr

        class _SR:
            def __init__(self):
                self._done = False

            async def read(self, _size=-1):
                if self._done:
                    return b""
                self._done = True
                return data

        return _SR()

    async def wait(self):
        return 0

    def terminate(self):
        pass

    def kill(self):
        pass


class _RunningProc(_Proc):
    """Process stays running until stdout is consumed to EOF."""

    def __init__(self, lines):
        super().__init__(lines)
        self.returncode = None
        self.stdout_exhausted = False
        self.terminated = False

    @property
    def stdout(self):
        async def gen():
            for b in self._lines:
                yield b
            self.stdout_exhausted = True
            self.returncode = 0

        return gen()

    def terminate(self):
        self.terminated = True
        self.returncode = -15


class _StderrBackpressureProc(_Proc):
    """stdout cannot finish until the parent starts draining stderr."""

    def __init__(self):
        super().__init__([])
        self.returncode = None
        self.stderr_drained = asyncio.Event()

    @property
    def stdout(self):
        async def gen():
            await self.stderr_drained.wait()
            yield json.dumps({
                "__deepsearch_status__": "completed",
                "conversation_id": "C1",
                "final_result": {"response_content": "done"},
            }).encode()
            self.returncode = 0

        return gen()

    @property
    def stderr(self):
        event = self.stderr_drained

        class _SR:
            def __init__(self):
                self._done = False

            async def read(self, _size=-1):
                if self._done:
                    return b""
                self._done = True
                event.set()
                return b"collector diagnostics"

        return _SR()


class _LargeStdoutLineProc(_Proc):
    """Expose a real StreamReader with an NDJSON line above its 64 KiB limit."""

    def __init__(self, report_content):
        super().__init__([])
        self._stdout = asyncio.StreamReader()
        self._stdout.feed_data((json.dumps({
            "__deepsearch_status__": "completed",
            "conversation_id": "C1",
            "final_result": {"response_content": report_content},
        }) + "\n").encode())
        self._stdout.feed_eof()

    @property
    def stdout(self):
        return self._stdout


def _patch_env(tool_lines):
    """统一 patch:Python/script 解析、route(空,触发 _send 早退)、subprocess、transport。"""
    return [
        patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"),
        patch.object(dt, "_resolve_run_script", return_value="/s"),
        patch.object(dt, "_get_route", return_value={"request_id": "", "channel_id": "", "session_id": ""}),
        patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(tool_lines))),
        patch("jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport"),
    ]


def _task_updates(payloads):
    return [payload for payload in payloads if payload.get("event_type") == "task.update"]


def _active_stage(update):
    active = [
        index for index, task in enumerate(update["tasks"], start=1)
        if task["status"] == "in_progress"
    ]
    return active[0] if active else None


@pytest.mark.asyncio
async def test_tool_sends_nested_section_reasoning_without_task_snapshots():
    raw_process = "原始检索过程第一行\n\n原始检索过程第二行" + "完整内容" * 40
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({
            "agent": "collector_info_retrieval",
            "section_idx": "1",
            "section_title": "真实标题",
            "section_total": 1,
            "event": "start",
            "content": raw_process,
        }),
        json.dumps({
            "agent": "sub_reporter",
            "section_idx": "1",
            "section_title": "真实章节标题",
            "section_total": 1,
            "event": "done",
            "content": "SUCCESS",
        }),
        json.dumps({
            "__deepsearch_status__": "completed",
            "conversation_id": "C1",
            "final_result": {"response_content": "done"},
        }),
    ]
    push = AsyncMock()
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={
             "request_id": "R1", "channel_id": "CH1", "session_id": "S1"
         }), \
         patch.object(dt, "_write_report_markdown", return_value="/tmp/r.md"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(lines))), \
         patch(
             "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
             return_value=push,
         ):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")

    payloads = [call.args[0]["payload"] for call in push.send_push.await_args_list]
    reasoning = [
        payload for payload in payloads if payload.get("event_type") == "chat.reasoning"
    ]
    task_updates = _task_updates(payloads)
    assert json.loads(result)["status"] == "completed"
    assert reasoning == [
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_stage_3",
            "task_content": "真实标题",
            "task_index": 1,
            "total_tasks": 1,
            "stream_source_id": "deepresearch_section_1",
            "content": "资料检索开始\n",
        },
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_stage_3",
            "task_content": "真实标题",
            "task_index": 1,
            "total_tasks": 1,
            "stream_source_id": "deepresearch_section_1",
            "content": raw_process,
        },
        {
            "event_type": "chat.reasoning",
            "task_id": "deepresearch_stage_3",
            "task_content": "真实章节标题",
            "task_index": 1,
            "total_tasks": 1,
            "stream_source_id": "deepresearch_section_1",
            "content": "章节撰写完成\n",
        },
    ]
    assert [_active_stage(update) for update in task_updates] == [1, 3, 6, None]
    assert all(task["status"] == "completed" for task in task_updates[-1]["tasks"])
    assert any(
        payload.get("event_type") == "chat.processing_status"
        and payload.get("is_processing") is True
        for payload in payloads
    )


def test_write_report_markdown_builds_inference_bundle_and_strips_internal_markers(tmp_path):
    final_result = {
        "response_content": (
            "# 报告\n\n"
            "[观点](#inference:7)"
            "[checked_citation:3][[1]](https://example.com/source)\n"
        ),
        "infer_messages": [{
            "id": "7",
            "html_base64": base64.b64encode(b"<html>trace</html>").decode("ascii"),
        }],
        "chart_messages": [{
            "chart_id": "chart-1",
            "chart_title": "趋势图",
            "base64": base64.b64encode(b"png-bytes").decode("ascii"),
        }],
        "request_metadata": {"trace_id": "trace-1"},
        "citation_messages": {
            "code": 0,
            "msg": "success",
            "data": [{
                "id": 3,
                "reference_index": 1,
                "url": "https://example.com/source",
                "title": "Source",
                "content": "evidence",
                "chunk": "evidence chunk",
                "source": "web",
                "publish_time": "2026-07-15",
                "score": 0.9,
            }],
        },
    }
    with patch(
        "jiuwenclaw.agentserver.tools.subagent_executor.context_vars.get_effective_request_output_dir",
        return_value=str(tmp_path),
    ):
        report_path = dt._write_report_markdown(final_result, "研究报告.md", "C1")

    assert report_path == str(tmp_path / "研究报告.md")
    report = (tmp_path / "研究报告.md").read_text(encoding="utf-8")
    assert "checked_citation" not in report
    assert "[观点](研究报告_infer/inference_7.html)" in report
    assert "[[1]](https://example.com/source)" in report
    assert (tmp_path / "研究报告_infer" / "inference_7.html").read_bytes() == b"<html>trace</html>"
    provenance = json.loads((tmp_path / "研究报告.provenance.json").read_text(encoding="utf-8"))
    snapshot_path = tmp_path / "研究报告.final-result.json"
    snapshot = json.loads(snapshot_path.read_text(encoding="utf-8"))
    assert snapshot["response_content"] == final_result["response_content"]
    assert snapshot["citation_messages"] == final_result["citation_messages"]
    assert snapshot["request_metadata"] == {"trace_id": "trace-1"}
    assert "html_base64" not in snapshot["infer_messages"][0]
    assert snapshot["infer_messages"][0]["artifact_path"] == "研究报告_infer/inference_7.html"
    assert "base64" not in snapshot["chart_messages"][0]
    assert snapshot["chart_messages"][0]["artifact_path"] == "研究报告_charts/chart-1.png"
    assert provenance["schema_version"] == 2
    assert provenance["document_id"].startswith("doc_")
    assert provenance["revision_id"].startswith("rev_")
    assert provenance["parent_revision_id"] is None
    assert provenance["conversation_id"] == "C1"
    assert provenance["content_sha256"] == hashlib.sha256(report.encode("utf-8")).hexdigest()
    assert provenance["final_result_path"] == snapshot_path.name
    assert provenance["final_result_sha256"] == hashlib.sha256(snapshot_path.read_bytes()).hexdigest()
    assert provenance["citations"] == final_result["citation_messages"]["data"]
    assert provenance["inference_manifest"][0]["id"] == "7"
    assert "html_base64" not in json.dumps(provenance)


def test_write_report_artifacts_keeps_rewrite_sidecars_hidden(tmp_path):
    final_result = {
        "response_content": "# 报告\n\n正文",
        "infer_messages": [],
        "chart_messages": [],
    }

    def _convert(_markdown_path, html_path):
        with open(html_path, "w", encoding="utf-8") as stream:
            stream.write("<html>report</html>")

    with patch(
        "jiuwenclaw.agentserver.tools.subagent_executor.context_vars.get_effective_request_output_dir",
        return_value=str(tmp_path),
    ), patch(
        "jiuwenclaw.agentserver.tools.deepresearch_plugin.convert_html_offline.convert_md_to_html",
        side_effect=_convert,
    ):
        artifacts = dt._write_report_artifacts_stream(final_result, "研究报告.md", "C1")

    assert artifacts == {
        "md": str(tmp_path / "研究报告.md"),
        "html": str(tmp_path / "研究报告.html"),
    }
    assert (tmp_path / "研究报告.final-result.json").is_file()
    assert (tmp_path / "研究报告.provenance.json").is_file()


def test_write_report_artifacts_keeps_rewrite_sidecars_when_html_fails(tmp_path):
    final_result = {
        "response_content": "# 报告\n\n正文",
        "infer_messages": [],
        "chart_messages": [],
    }

    with patch(
        "jiuwenclaw.agentserver.tools.subagent_executor.context_vars.get_effective_request_output_dir",
        return_value=str(tmp_path),
    ), patch(
        "jiuwenclaw.agentserver.tools.deepresearch_plugin.convert_html_offline.convert_md_to_html",
        side_effect=RuntimeError("converter unavailable"),
    ):
        artifacts = dt._write_report_artifacts_stream(final_result, "研究报告.md", "C1")

    assert artifacts == {"md": str(tmp_path / "研究报告.md")}
    assert (tmp_path / "研究报告.final-result.json").is_file()
    assert (tmp_path / "研究报告.provenance.json").is_file()


@pytest.mark.asyncio
async def test_completed_report_is_delivered_as_markdown_file_without_entering_tool_outcome():
    report_content = "# 最终报告\n\n完整正文"
    final_result = {"response_content": report_content, "infer_messages": [], "chart_messages": []}
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({
            "__deepsearch_status__": "completed",
            "conversation_id": "C1",
            "final_result": final_result,
        }),
    ]
    push = AsyncMock()
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={
             "request_id": "R1", "channel_id": "CH1", "session_id": "S1"
         }), \
         patch.object(dt, "_write_report_markdown", return_value="/tmp/r.md") as write_report, \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(lines))), \
         patch(
             "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
             return_value=push,
         ):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")

    payloads = [call.args[0]["payload"] for call in push.send_push.await_args_list]
    report_frames = [payload for payload in payloads if payload.get("event_type") == "chat.delta"]
    assert report_frames == []
    write_report.assert_called_once_with(final_result, "r", "C1")
    assert {"event_type": "chat.file", "files": [{"path": "/tmp/r.md", "name": "r.md"}]} in payloads
    assert [_active_stage(update) for update in _task_updates(payloads)] == [1, 6, None]
    assert json.loads(result) == {
        "status": "completed",
        "conversation_id": "C1",
        "report_delivered": True,
        "report_chars": len(report_content),
    }


@pytest.mark.asyncio
async def test_completed_report_does_not_fall_back_to_chat_when_file_delivery_fails():
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({
            "__deepsearch_status__": "completed",
            "conversation_id": "C1",
            "final_result": {"response_content": "完整报告"},
        }),
    ]
    push = AsyncMock()

    async def _fail_file(message):
        if message["payload"].get("event_type") == "chat.file":
            raise RuntimeError("push failed")

    push.send_push.side_effect = _fail_file
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={
             "request_id": "R1", "channel_id": "CH1", "session_id": "S1"
         }), \
         patch.object(dt, "_write_report_markdown", return_value="/tmp/r.md"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(lines))), \
         patch(
             "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
             return_value=push,
         ):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")

    outcome = json.loads(result)
    assert outcome["status"] == "error"
    assert outcome["error_code"] == "report_file_delivery_failed"
    assert "report_content" not in outcome
    payloads = [call.args[0]["payload"] for call in push.send_push.await_args_list]
    assert [_active_stage(update) for update in _task_updates(payloads)] == [1, 6]


@pytest.mark.asyncio
async def test_tool_keeps_current_workflow_stage_in_progress_when_research_fails():
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({
            "agent": "collector_info_retrieval",
            "section_idx": "1",
            "section_title": "真实章节标题",
            "section_total": 1,
        }),
        json.dumps({
            "__deepsearch_status__": "error",
            "conversation_id": "C1",
            "error": "search failed",
        }),
    ]
    push = AsyncMock()
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={
             "request_id": "R1", "channel_id": "CH1", "session_id": "S1"
         }), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(lines))), \
         patch(
             "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
             return_value=push,
         ):
        await dt.deepresearch_stream._func(action="start", query="X", file_name="r")

    payloads = [call.args[0]["payload"] for call in push.send_push.await_args_list]
    assert [_active_stage(update) for update in _task_updates(payloads)] == [1, 3]


@pytest.mark.asyncio
async def test_start_returns_interrupted_outcome_from_marker():
    # started → outline 内容 → interrupt chunk(带 raw prompt)→ interrupted marker(透传 content)
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({"agent": "outline", "content": "累积的旧大纲"}),
        json.dumps({"agent": "outline_interaction", "message_type": "interrupt",
                    "content": "请审批大纲", "conversation_id": "C1"}),
        json.dumps({"__deepsearch_status__": "interrupted", "agent": "outline_interaction",
                    "conversation_id": "C1", "content": "第一章 来自marker\n第二章 来自marker",
                    "prompt": "请审阅大纲"}),
    ]
    push = AsyncMock()
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={
             "request_id": "R1", "channel_id": "CH1", "session_id": "S1"
         }), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(lines))), \
         patch(
             "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
             return_value=push,
         ):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")
    out = json.loads(result)
    assert out["status"] == "interrupted"
    assert out["conversation_id"] == "C1"
    assert out["node_id"] == "outline_interaction"
    # marker 结构化透传:agent 按通用中断规则读 marker.content。
    assert out["marker"]["content"] == "第一章 来自marker\n第二章 来自marker"
    assert out["marker"]["prompt"] == "请审阅大纲"
    assert out["marker"]["conversation_id"] == "C1"
    assert "__deepsearch_status__" not in out["marker"]  # 内部标记已剥除
    # outcome.prompt:marker.content 优先(不退到累积的"累积的旧大纲")
    assert "来自marker" in out["prompt"]
    assert "累积的旧大纲" not in out["prompt"]
    payloads = [call.args[0]["payload"] for call in push.send_push.await_args_list]
    assert [_active_stage(update) for update in _task_updates(payloads)] == [1, 2]


@pytest.mark.asyncio
async def test_feedback_interrupt_injects_cached_questions_when_marker_has_none():
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({
            "agent": "question_generator",
            "message_type": "message_chunk",
            "message_id": "Q1",
            "content": "1. 市场？\n2. 时间？",
        }, ensure_ascii=False),
        json.dumps({
            "__deepsearch_status__": "interrupted",
            "agent": "feedback_handler",
            "conversation_id": "C1",
            "content": "Enter your feedback:",
        }),
    ]
    patches = _patch_env(lines)
    for active_patch in patches:
        active_patch.start()
    try:
        result = await dt.deepresearch_stream._func(
            action="start", query="X", file_name="r",
        )
    finally:
        for active_patch in patches:
            active_patch.stop()

    assert json.loads(result)["marker"]["questions"] == "1. 市场？\n2. 时间？"


@pytest.mark.asyncio
async def test_feedback_interrupt_preserves_native_questions():
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({
            "agent": "question_generator",
            "message_type": "message_chunk",
            "message_id": "Q1",
            "content": "缓存问题",
        }, ensure_ascii=False),
        json.dumps({
            "__deepsearch_status__": "interrupted",
            "agent": "feedback_handler",
            "conversation_id": "C1",
            "questions": ["原生问题"],
        }, ensure_ascii=False),
    ]
    patches = _patch_env(lines)
    for active_patch in patches:
        active_patch.start()
    try:
        result = await dt.deepresearch_stream._func(
            action="start", query="X", file_name="r",
        )
    finally:
        for active_patch in patches:
            active_patch.stop()

    assert json.loads(result)["marker"]["questions"] == ["原生问题"]


@pytest.mark.asyncio
async def test_outline_marker_injects_accumulated_outline_when_sdk_omits_it():
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({"agent": "outline", "content": "第一章 累积大纲"}),
        json.dumps({"agent": "outline_interaction", "message_type": "interrupt",
                    "content": "请审批大纲", "conversation_id": "C1"}),
        json.dumps({"__deepsearch_status__": "interrupted", "agent": "outline_interaction",
                    "conversation_id": "C1"}),
    ]
    patches = _patch_env(lines)
    for p in patches:
        p.start()
    try:
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")
    finally:
        for p in patches:
            p.stop()

    out = json.loads(result)
    assert out["marker"]["outline"] == "第一章 累积大纲"
    assert "第一章 累积大纲" in out["prompt"]


@pytest.mark.asyncio
async def test_outline_marker_replaces_sdk_status_placeholder_with_accumulated_outline():
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({"agent": "outline", "content": "# 第一章\n累积大纲正文"}),
        json.dumps({"agent": "outline_interaction", "message_type": "interrupt",
                    "content": "Round 1: waiting for user feedback.", "conversation_id": "C1"}),
        json.dumps({"__deepsearch_status__": "interrupted", "agent": "outline_interaction",
                    "conversation_id": "C1", "content": "Round 1: waiting for user feedback."}),
    ]
    patches = _patch_env(lines)
    for p in patches:
        p.start()
    try:
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")
    finally:
        for p in patches:
            p.stop()

    out = json.loads(result)
    assert out["marker"]["outline"] == "# 第一章\n累积大纲正文"
    assert "累积大纲正文" in out["prompt"]
    assert "Round 1: waiting for user feedback." not in out["prompt"]


@pytest.mark.asyncio
async def test_outline_titles_are_reused_by_section_stream_after_resume():
    outline = json.dumps({
        "title": "主流 RAG 框架深度对比",
        "sections": [
            {"title": "RAG技术演进与主流框架全景概览"},
            {"title": "核心架构设计与检索增强能力深度对比"},
        ],
    }, ensure_ascii=False)
    start_lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({"agent": "outline", "content": outline}),
        json.dumps({"agent": "outline_interaction", "message_type": "interrupt",
                    "content": "请审批大纲", "conversation_id": "C1"}),
        json.dumps({"__deepsearch_status__": "interrupted", "agent": "outline_interaction",
                    "conversation_id": "C1", "outline": outline}),
    ]
    resume_lines = [
        json.dumps({"__deepsearch_status__": "resuming", "conversation_id": "C1"}),
        json.dumps({
            "agent": "plan_reasoning",
            "section_idx": "1",
            "event": "message",
            "content": json.dumps({
                "title": "RAG技术演进阶段与里程碑及十大框架全景画像信息采集",
                "thought": "完整规划过程",
            }, ensure_ascii=False),
        }),
        json.dumps({"__deepsearch_status__": "completed", "conversation_id": "C1",
                    "final_result": {"response_content": "done"}}),
    ]
    route = {"request_id": "R1", "channel_id": "CH1", "session_id": "S1"}
    push = AsyncMock()
    spawn = AsyncMock(side_effect=[_Proc(start_lines), _Proc(resume_lines)])
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value=route), \
         patch.object(dt, "_write_report_markdown", return_value="/tmp/r.md"), \
         patch("asyncio.create_subprocess_exec", new=spawn), \
         patch(
             "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
             return_value=push,
         ):
        interrupted = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")
        completed = await dt.deepresearch_stream._func(
            action="resume", conversation_id="C1", node="outline_interaction",
            feedback='{"interrupt_feedback":"accepted"}',
        )

    assert json.loads(interrupted)["status"] == "interrupted"
    assert json.loads(completed)["status"] == "completed"
    section_payloads = [
        call.args[0]["payload"]
        for call in push.send_push.await_args_list
        if call.args[0]["payload"].get("task_id") == "deepresearch_stage_3"
        and call.args[0]["payload"].get("stream_source_id") == "deepresearch_section_1"
    ]
    assert section_payloads
    assert all(
        payload["task_content"] == "RAG技术演进与主流框架全景概览"
        for payload in section_payloads
    )
    payloads = [call.args[0]["payload"] for call in push.send_push.await_args_list]
    assert [_active_stage(update) for update in _task_updates(payloads)] == [
        1, 2, 2, 3, 6, None,
    ]


@pytest.mark.asyncio
async def test_outline_titles_are_reused_when_workflow_continues_without_interrupt():
    outline = json.dumps({
        "title": "AI Agent 入门",
        "sections": [
            {"id": "1", "title": "AI Agent 概念定义与核心区分"},
            {"id": "2", "title": "AI Agent 技术架构与工作原理"},
        ],
    }, ensure_ascii=False)
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({"agent": "outline", "content": outline}),
        json.dumps({
            "agent": "plan_reasoning",
            "section_idx": "2",
            "event": "message",
            "content": "章节规划过程",
        }),
        json.dumps({"__deepsearch_status__": "error", "conversation_id": "C1",
                    "error": "stop after section evidence"}),
    ]
    push = AsyncMock()
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={
             "request_id": "R1", "channel_id": "CH1", "session_id": "S1"
         }), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(lines))), \
         patch(
             "jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport",
             return_value=push,
         ):
        await dt.deepresearch_stream._func(action="start", query="X", file_name="r")

    section_payloads = [
        call.args[0]["payload"]
        for call in push.send_push.await_args_list
        if call.args[0]["payload"].get("stream_source_id") == "deepresearch_section_2"
    ]
    assert section_payloads
    assert all(
        payload["task_content"] == "AI Agent 技术架构与工作原理"
        for payload in section_payloads
    )


@pytest.mark.asyncio
async def test_interrupted_marker_waits_for_runner_to_exit_naturally():
    proc = _RunningProc([
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({"__deepsearch_status__": "interrupted", "agent": "feedback_handler",
                    "conversation_id": "C1", "content": "请输入反馈"}),
        "runner cleanup complete",
    ])
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={"request_id": "", "channel_id": "", "session_id": ""}), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)), \
         patch("jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport"):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")

    assert json.loads(result)["status"] == "interrupted"
    assert proc.stdout_exhausted is True
    assert proc.terminated is False


@pytest.mark.asyncio
async def test_stderr_is_drained_while_subprocess_is_running():
    proc = _StderrBackpressureProc()
    push = AsyncMock()
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={"request_id": "R1", "channel_id": "CH1", "session_id": "S1"}), \
         patch.object(dt, "_write_report_markdown", return_value="/tmp/r.md"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)), \
         patch("jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport", return_value=push):
        result = await asyncio.wait_for(
            dt.deepresearch_stream._func(action="start", query="X", file_name="r"),
            timeout=0.2,
        )

    assert json.loads(result)["status"] == "completed"
    assert proc.stderr_drained.is_set()


@pytest.mark.asyncio
async def test_completed_marker_can_exceed_asyncio_stream_line_limit():
    report_content = "报告正文" * 20000
    proc = _LargeStdoutLineProc(report_content)
    push = AsyncMock()
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={"request_id": "R1", "channel_id": "CH1", "session_id": "S1"}), \
         patch.object(dt, "_write_report_markdown", return_value="/tmp/r.md"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)), \
         patch("jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport", return_value=push):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")

    outcome = json.loads(result)
    assert outcome["status"] == "completed"
    assert outcome["report_chars"] == len(report_content)


@pytest.mark.asyncio
async def test_start_returns_completed_outcome():
    final_result = {"response_content": "最终报告正文"}
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({"agent": "reporter", "content": "最终报告正文"}),
        json.dumps({"__deepsearch_status__": "completed", "conversation_id": "C1",
                    "final_result": final_result}),
    ]
    push = AsyncMock()
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={"request_id": "R1", "channel_id": "CH1", "session_id": "S1"}), \
         patch.object(dt, "_write_report_markdown", return_value="/tmp/r.md"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(lines))), \
         patch("jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport", return_value=push):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")
    out = json.loads(result)
    assert out["status"] == "completed"
    assert out["report_chars"] == len("最终报告正文")
    assert "report_content" not in out


@pytest.mark.asyncio
async def test_completed_marker_rejects_legacy_report_content():
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({
            "__deepsearch_status__": "completed",
            "conversation_id": "C1",
            "report_content": "legacy report",
        }),
    ]
    write_report = AsyncMock()
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch.object(dt, "_get_route", return_value={"request_id": "R1", "channel_id": "CH1", "session_id": "S1"}), \
         patch.object(dt, "_write_report_markdown", write_report), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=_Proc(lines))), \
         patch("jiuwenclaw.agentserver.gateway_push.transport.WebSocketGatewayPushTransport", return_value=AsyncMock()):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")

    out = json.loads(result)
    assert out["status"] == "error"
    assert out["error_code"] == "empty_report"
    write_report.assert_not_awaited()


@pytest.mark.asyncio
async def test_start_returns_explicit_error_marker():
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({
            "__deepsearch_status__": "error",
            "conversation_id": "C1",
            "error": "workflow ended without report content",
        }),
    ]
    patches = _patch_env(lines)
    for p in patches:
        p.start()
    try:
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(result)
    assert out["status"] == "error"
    assert out["conversation_id"] == "C1"
    assert out["error"] == "workflow ended without report content"


@pytest.mark.asyncio
async def test_ufp_marker_injects_accumulated_report():
    # UFP 中断:marker 不带 report(report 不在 key 列表),tool 注入累积 report_parts[:6000]
    lines = [
        json.dumps({"__deepsearch_status__": "started", "conversation_id": "C1"}),
        json.dumps({"agent": "reporter", "content": "R" * 7000}),
        json.dumps({"__deepsearch_status__": "interrupted", "agent": "user_feedback_processor",
                    "conversation_id": "C1", "prompt": "请选择后续操作"}),
    ]
    patches = _patch_env(lines)
    for p in patches:
        p.start()
    try:
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(result)
    assert out["status"] == "interrupted"
    assert out["node_id"] == "user_feedback_processor"
    assert "完整报告见最终产物" in out["marker"]["report"]
    assert len(out["marker"]["report"]) < 6200


@pytest.mark.asyncio
async def test_resume_requires_conversation_id_and_node():
    patches = _patch_env([])
    for p in patches:
        p.start()
    try:
        result = await dt.deepresearch_stream._func(action="resume", query="X")
    finally:
        for p in patches:
            p.stop()
    out = json.loads(result)
    assert out["status"] == "error"
    assert "conversation_id and node" in out["error"]


@pytest.mark.asyncio
async def test_missing_run_script_returns_error():
    # runner 脚本解析失败 → 早返 error(不 spawn)
    with patch.object(dt, "_resolve_run_script", return_value=""):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")
    out = json.loads(result)
    assert out["status"] == "error"
    assert "run_deepsearch.py" in out["error"]


@pytest.mark.asyncio
async def test_no_terminal_marker_captures_stderr():
    # 子进程 stdout 无 status marker → "no terminal marker" + stderr 尾部进 outcome
    lines = [
        json.dumps({"agent": "info_collector", "content": "部分进度"}),  # 非 marker,被路由
        # 没有 started/interrupted/completed marker → loop 结束,默认 error
    ]
    proc = _Proc(lines, stderr_lines=["KeyError: 'LLM_API_KEY'", "Traceback (most recent call last)"])
    with patch.object(dt, "_resolve_jiuwenclaw_python", return_value="/p"), \
         patch.object(dt, "_resolve_run_script", return_value="/s"), \
         patch("asyncio.create_subprocess_exec", new=AsyncMock(return_value=proc)), \
         patch.object(dt, "_get_route", return_value={"request_id": "", "channel_id": "", "session_id": ""}):
        result = await dt.deepresearch_stream._func(action="start", query="X", file_name="r")
    out = json.loads(result)
    assert out["status"] == "error"
    assert out["error"] == "no terminal marker"
    assert out["returncode"] == 0
    assert "stderr_tail" in out
    assert "LLM_API_KEY" in out["stderr_tail"]
    assert "Traceback" in out["stderr_tail"]


def test_build_bridge_env_maps_global_to_deepsearch_names():
    env = dt._build_bridge_env({
        "API_KEY": "sk-b354", "MODEL_NAME": "glm-5.2", "API_BASE": "https://api.example/v2",
        "MODEL_PROVIDER": "dashscope", "BOCHA_API_KEY": "bkey",
    })
    assert env["LLM_API_KEY"] == "sk-b354"
    assert env["LLM_MODEL_NAME"] == "glm-5.2"
    assert env["LLM_BASE_URL"] == "https://api.example/v2"
    assert env["LLM_MODEL_TYPE"] == "qwen"  # dashscope → qwen
    assert env["WEB_SEARCH_API_KEY"] == "bkey"
    assert env["WEB_SEARCH_ENGINE_NAME"] == "bocha"
    assert "WEB_SEARCH_URL" not in env  # bocha 无显式 url,不设(有值才设),脚本侧管默认
    assert env["LLM_SSL_VERIFY"] == "false"  # 子进程默认 true 会触发 ssl_cert required
    assert env["TOOL_SSL_VERIFY"] == "false"  # Petal 同样默认 true 且要求 TOOL_SSL_CERT


def test_build_bridge_env_respects_explicit_tool_ssl_verify():
    env = dt._build_bridge_env({"MODEL_NAME": "m", "TOOL_SSL_VERIFY": "true"})

    assert env["TOOL_SSL_VERIFY"] == "true"


def test_build_bridge_env_petal_requires_explicit_url_and_search_key():
    headers = '{"Authorization":"Basic session"}'
    env = dt._build_bridge_env({
        "API_KEY": "sk-x",
        "MODEL_NAME": "m",
        "API_BASE": "https://dashscope.example/v1",
        "WEB_SEARCH_ENGINE_NAME": "petal",
        "WEB_SEARCH_URL": "https://petal.example/v1/ai-tools/web-search",
        "WEB_SEARCH_API_KEY": headers,
    })
    assert env["WEB_SEARCH_ENGINE_NAME"] == "petal"
    assert env["WEB_SEARCH_URL"] == "https://petal.example/v1/ai-tools/web-search"
    assert env["WEB_SEARCH_API_KEY"] == headers


@pytest.mark.parametrize(
    ("invalid_name", "invalid_value"),
    [
        ("WEB_SEARCH_URL", None),
        ("WEB_SEARCH_API_KEY", None),
        ("WEB_SEARCH_URL", "   "),
        ("WEB_SEARCH_API_KEY", "   "),
    ],
)
def test_build_bridge_env_rejects_partial_petal_config(invalid_name, invalid_value):
    source = {
        "WEB_SEARCH_ENGINE_NAME": "petal",
        "WEB_SEARCH_URL": "https://petal.example/v1/ai-tools/web-search",
        "WEB_SEARCH_API_KEY": '{"Authorization":"Basic session"}',
    }
    if invalid_value is None:
        source.pop(invalid_name)
    else:
        source[invalid_name] = invalid_value
    env = dt._build_bridge_env(source)
    assert "WEB_SEARCH_ENGINE_NAME" not in env
    assert "WEB_SEARCH_API_KEY" not in env
    assert "WEB_SEARCH_URL" not in env


def test_build_bridge_env_accepts_provider_specific_petal_key():
    env = dt._build_bridge_env({
        "WEB_SEARCH_ENGINE_NAME": "petal",
        "WEB_SEARCH_URL": "https://petal.example/v1/ai-tools/web-search",
        "PETAL_API_KEY": "petal-key",
    })
    assert env["WEB_SEARCH_ENGINE_NAME"] == "petal"
    assert env["WEB_SEARCH_API_KEY"] == "petal-key"


def test_build_bridge_env_uses_independent_petal_url_with_custom_llm():
    headers = '{"Authorization":"Basic search-session"}'
    env = dt._build_bridge_env({
        "API_KEY": "custom-llm-key",
        "MODEL_NAME": "glm-5.2",
        "API_BASE": "https://dashscope.example/compatible-mode/v1",
        "default_headers": '{"Authorization":"Bearer custom-llm-key"}',
        "PETAL_API_KEY": headers,
        "PETAL_API_URL": "https://client-claw.example/v1/ai-tools/web-search",
    })

    assert env["LLM_BASE_URL"] == "https://dashscope.example/compatible-mode/v1"
    assert env["WEB_SEARCH_ENGINE_NAME"] == "petal"
    assert env["WEB_SEARCH_API_KEY"] == headers
    assert env["WEB_SEARCH_URL"] == "https://client-claw.example/v1/ai-tools/web-search"


def test_build_bridge_env_reuses_run_task_petal_fallback():
    source = {
        "API_KEY": "sk-x",
        "MODEL_NAME": "m",
        "API_BASE": "https://client-claw.example/v2",
        "default_headers": '{"Authorization":"Basic session"}',
    }

    resolved = dt._get_task_manager_cls()._load_config(source)
    env = dt._build_bridge_env(source)

    assert env["LLM_API_KEY"] == resolved["LLM_API_KEY"]
    assert env["LLM_MODEL_NAME"] == resolved["LLM_MODEL_NAME"]
    assert env["LLM_BASE_URL"] == resolved["LLM_BASE_URL"]
    assert env["WEB_SEARCH_ENGINE_NAME"] == resolved["WEB_SEARCH_ENGINE_NAME"] == "petal"
    assert env["WEB_SEARCH_API_KEY"] == resolved["WEB_SEARCH_API_KEY"]
    assert env["WEB_SEARCH_URL"] == resolved["WEB_SEARCH_URL"]
    assert env["WEB_SEARCH_URL"] == "https://client-claw.example/v1/ai-tools/web-search"


def test_build_bridge_env_empty_value_not_set():
    # 无 API_KEY → 不设 LLM_API_KEY,让 .env 兜底
    env = dt._build_bridge_env({"MODEL_NAME": "m"})
    assert "LLM_API_KEY" not in env
    assert env["LLM_MODEL_NAME"] == "m"


def test_child_env_enables_hitl_for_interactive_request(monkeypatch):
    monkeypatch.setattr(dt, "_build_bridge_env", lambda _env: {"BASE": "1"})

    assert dt._build_deepresearch_child_env({}, interactive_ask=True) == {
        "BASE": "1",
        "DEEPSEARCH_HITL": "true",
        "PYTHONUNBUFFERED": "1",
    }


def test_child_env_disables_hitl_and_overrides_stale_parent(monkeypatch):
    monkeypatch.setattr(
        dt,
        "_build_bridge_env",
        lambda _env: {"DEEPSEARCH_HITL": "true"},
    )

    env = dt._build_deepresearch_child_env(
        {"DEEPSEARCH_HITL": "true"},
        interactive_ask=False,
    )

    assert env["DEEPSEARCH_HITL"] == "false"
    assert env["PYTHONUNBUFFERED"] == "1"


def _make_fake_skill(parent: str) -> str:
    """在 parent/deepsearch-research/scripts/run_deepsearch.py 建假 skill,返回 skill dir。"""
    import os
    skill_dir = os.path.join(parent, "deepsearch-research")
    os.makedirs(os.path.join(skill_dir, "scripts"), exist_ok=True)
    with open(os.path.join(skill_dir, "scripts", "run_deepsearch.py"), "w", encoding="utf-8") as f:
        f.write("# fake")
    return skill_dir


def test_resolve_skill_root_from_env(tmp_path, monkeypatch):
    # sidecar cwd 不含 office-claw-skills → 必须靠 JIUWENCLAW_SHARED_SKILLS_DIRS 命中
    skill_parent = str(tmp_path / "shared-skills")
    os.makedirs(skill_parent)
    skill_dir = _make_fake_skill(skill_parent)
    monkeypatch.setenv("JIUWENCLAW_SHARED_SKILLS_DIRS", skill_parent)
    elsewhere = str(tmp_path / "elsewhere")
    os.makedirs(elsewhere)
    monkeypatch.chdir(elsewhere)  # cwd 不含 skill
    assert dt._resolve_skill_root() == skill_dir
    assert os.path.basename(dt._resolve_run_script()) == "run_deepsearch.py"


def test_deepresearch_python_uses_current_jiuwenclaw_interpreter():
    assert dt._resolve_jiuwenclaw_python() == sys.executable


def test_get_deepresearch_tools_exposes_stream_and_rewrite_tools(monkeypatch):
    monkeypatch.setattr(dt, "enable_deepresearch", lambda: True)
    monkeypatch.setattr(dt, "_deepresearch_dependency_available", lambda: True)

    from jiuwenclaw.agentserver.tools import deepresearch_rewrite_tools as rt

    assert dt.get_deepresearch_tools() == [
        dt.deepresearch_stream,
        rt.deepresearch_prepare_rewrite,
        rt.deepresearch_commit_rewrite,
    ]


def test_resolve_skill_root_env_uses_platform_path_separator(tmp_path, monkeypatch):
    p1 = str(tmp_path / "d1"); os.makedirs(p1); sd1 = _make_fake_skill(p1)
    p2 = str(tmp_path / "d2"); os.makedirs(p2)
    monkeypatch.setenv("JIUWENCLAW_SHARED_SKILLS_DIRS", os.pathsep.join((p1, p2)))
    monkeypatch.chdir(tmp_path)
    assert dt._resolve_skill_root() == sd1  # 命中第一个含 skill 的


def test_resolve_skill_root_preserves_windows_drive_letter(tmp_path, monkeypatch):
    windows_parent = r"C:\shared-skills"
    skill_dir = _make_fake_skill(str(tmp_path / windows_parent))
    monkeypatch.setattr(dt.os, "pathsep", ";")
    monkeypatch.setenv(
        "JIUWENCLAW_SHARED_SKILLS_DIRS",
        rf"{windows_parent};D:\other-skills",
    )
    monkeypatch.chdir(tmp_path)

    assert dt._resolve_skill_root() == os.path.join(windows_parent, "deepsearch-research")
    assert os.path.samefile(dt._resolve_skill_root(), skill_dir)


def test_resolve_skill_root_falls_back_to_cwd(tmp_path, monkeypatch):
    # 无 env → cwd/office-claw-skills/deepsearch-research
    monkeypatch.delenv("JIUWENCLAW_SHARED_SKILLS_DIRS", raising=False)
    skill_dir = _make_fake_skill(str(tmp_path / "office-claw-skills"))
    monkeypatch.chdir(tmp_path)
    assert dt._resolve_skill_root() == skill_dir


def test_resolve_skill_root_empty_when_not_found(tmp_path, monkeypatch):
    monkeypatch.delenv("JIUWENCLAW_SHARED_SKILLS_DIRS", raising=False)
    monkeypatch.chdir(str(tmp_path))  # 无 office-claw-skills
    assert dt._resolve_skill_root() == ""
    assert dt._resolve_run_script() == ""
