# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import json
import time
from pathlib import Path

import pytest

from jiuwenclaw.perf.accumulator import RequestMeta, RequestSummaryAccumulator
from jiuwenclaw.perf.collector import PerfCollector
from jiuwenclaw.perf.config import init_perf_summary_config
from jiuwenclaw.perf.events import LlmPerfEvent, TaskPerfEvent, ToolPerfEvent
from jiuwenclaw.perf.extract import tool_status_from_result
from jiuwenclaw.perf.stats import ms_to_s, percentile_s
from jiuwenclaw.perf.writer import append_request_summary, request_summaries_file


def test_request_summary_rail_imports_required_symbols() -> None:
    """Regression: ensure request_summary_rail imports all runtime dependencies."""
    import ast

    src = Path(__file__).resolve().parents[3] / "jiuwenclaw" / "perf" / "request_summary_rail.py"
    tree = ast.parse(src.read_text(encoding="utf-8"))
    imported: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom) and node.module and node.module.startswith("jiuwenclaw.perf"):
            for alias in node.names:
                imported.add(alias.name)
    for required in ("get_perf_collector", "get_perf_summary_config"):
        assert required in imported, f"missing import: {required}"


def test_tool_status_uses_success_and_status_fields() -> None:
    assert tool_status_from_result({"success": True, "result": "done"}) == "ok"
    assert tool_status_from_result({"success": False, "error": "spawn failed"}) == "error"
    assert tool_status_from_result({"status": "ok", "note": "no error occurred"}) == "ok"
    assert tool_status_from_result({"message": "Successfully updated 2 task(s)"}) == "ok"
    assert tool_status_from_result("Successfully created 9 task(s)") == "ok"
    assert tool_status_from_result(
        "shell operation execution error, reason: execution timeout after 180 seconds"
    ) == "error"
    assert tool_status_from_result("Traceback (most recent call last):\nValueError") == "error"


def test_percentile_s_basic() -> None:
    assert percentile_s([10000, 20000, 30000, 40000, 100000], 0.90) == 100.0
    assert percentile_s([5000], 0.99) == 5.0
    assert ms_to_s(1500) == 1.5


def test_bottleneck_top_n_zero_disables_tracking(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.config.get_config",
        lambda: {"perf": {"summary": {"bottleneck_top_n": 0}}},
    )
    from jiuwenclaw.perf.config import load_perf_summary_config

    cfg = load_perf_summary_config()
    assert cfg.bottleneck_top_n == 0


def test_accumulator_finalize_schema() -> None:
    acc = RequestSummaryAccumulator(
        meta=RequestMeta(
            session_id="sess-1",
            request_id="req-1",
            channel_id="web",
            mode="plan",
            trace_id=None,
            started_at=1000.0,
        ),
        _bottleneck_top_n=3,
    )
    acc.record_llm(
        LlmPerfEvent(
            llm_call_id="llm-1",
            duration_ms=1000.0,
            model="deepseek-chat",
            iteration=1,
            input_tokens=100,
            output_tokens=50,
            status="ok",
            agent_id="main_agent",
            task_id="todo:1",
        )
    )
    acc.record_tool(
        ToolPerfEvent(
            tool_call_id="tool-1",
            name="WebSearch",
            duration_ms=500.0,
            status="ok",
            agent_id="main_agent",
            task_id="todo:1",
            iteration=1,
        )
    )
    summary = acc.finalize(status="ok", ended_at=1002.5)

    assert summary["schema_version"] == 1
    assert summary["meta"]["request_id"] == "req-1"
    assert summary["summary"]["total_s"] == 2.5
    assert summary["summary"]["stats"]["llm"]["count"] == 1
    assert summary["summary"]["stats"]["llm"]["p90_duration_s"] == 1.0
    assert summary["summary"]["stats"]["tool"]["max_s"] == 0.5
    assert summary["summary"]["stats"]["tool"]["list"] == [{"name": "WebSearch", "count": 1}]
    assert "subagent" not in summary["summary"]["stats"]
    assert "llm_call_id" not in summary["bottleneck"]["llm"][0]
    assert summary["bottleneck"]["tool"][0]["name"] == "WebSearch"
    assert "errors" not in summary


def test_accumulator_omits_errors_when_disabled() -> None:
    acc = RequestSummaryAccumulator(
        meta=RequestMeta(
            session_id="sess-1",
            request_id="req-1",
            channel_id="web",
            mode="plan",
            trace_id=None,
            started_at=1000.0,
        ),
        _include_errors=False,
    )
    acc.record_tool(
        ToolPerfEvent(
            tool_call_id="call-err",
            name="bash",
            duration_ms=100.0,
            status="error",
            error_message="command failed",
        )
    )

    summary = acc.finalize(status="ok", ended_at=1001.0)

    assert "errors" not in summary
    assert summary["summary"]["stats"]["tool"]["fail_count"] == 1


def test_accumulator_merges_subagent_into_total_stats() -> None:
    acc = RequestSummaryAccumulator(
        meta=RequestMeta(
            session_id="sess-1",
            request_id="req-1",
            channel_id="web",
            mode="plan",
            trace_id=None,
            started_at=1000.0,
        ),
        _include_errors=True,
    )
    iteration = 3
    acc.record_tool(
        ToolPerfEvent(
            tool_call_id="call-a",
            name="web_search",
            duration_ms=100.0,
            status="ok",
            iteration=iteration,
        )
    )
    acc.record_tool(
        ToolPerfEvent(
            tool_call_id="call-b",
            name="bash",
            duration_ms=200.0,
            status="error",
            iteration=iteration,
            error_message="command failed",
            agent_id="subagent_abc",
        )
    )
    acc.record_llm(
        LlmPerfEvent(
            llm_call_id="llm-err",
            duration_ms=50.0,
            model="glm",
            iteration=2,
            input_tokens=1,
            output_tokens=0,
            status="error",
            error_message="rate limited",
            agent_id="subagent_abc",
        )
    )

    summary = acc.finalize(status="ok", ended_at=1002.0)
    tool_stats = summary["summary"]["stats"]["tool"]
    llm_stats = summary["summary"]["stats"]["llm"]

    assert "subagent" not in summary["summary"]["stats"]
    assert tool_stats["count"] == 2
    assert tool_stats["round_count"] == 1
    assert tool_stats["list"] == [{"name": "bash", "count": 1}, {"name": "web_search", "count": 1}]
    assert llm_stats["count"] == 1
    assert len(summary["errors"]) == 2
    assert all(err["status"] == "error" for err in summary["errors"])
    assert "call_id" not in summary["errors"][1]
    assert summary["errors"][0]["duration_s"] == 0.2


def test_parallel_tool_timing_dict() -> None:
    from jiuwenclaw.perf.context import pop_tool_start, set_tool_start

    set_tool_start("call-1", "tool_a", 10.0)
    set_tool_start("call-2", "tool_b", 20.0)
    first = pop_tool_start("call-1")
    second = pop_tool_start("call-2")
    assert first == ("tool_a", 10.0)
    assert second == ("tool_b", 20.0)


def test_parallel_tool_timing_no_false_anon_match() -> None:
    from jiuwenclaw.perf.context import pop_tool_start, set_tool_start

    set_tool_start("", "bash", 10.0)
    set_tool_start("", "bash", 20.0)
    assert pop_tool_start("", "bash") is None

    set_tool_start("call-1", "tool_a", 10.0)
    set_tool_start("call-2", "tool_b", 20.0)
    assert pop_tool_start("", "tool_a") is None


def test_subagent_executor_uses_record_only_rail() -> None:
    executor_src = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "tools"
        / "subagent_executor"
        / "executor.py"
    )
    interface_src = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "deep_agent"
        / "interface_deep.py"
    )
    executor_text = executor_src.read_text(encoding="utf-8")
    interface_text = interface_src.read_text(encoding="utf-8")
    assert executor_text.count("RequestSummaryRail(record_only=True)") >= 2
    assert "RequestSummaryRail(record_only=True)" in interface_text


def test_extract_react_iteration_from_inputs() -> None:
    from types import SimpleNamespace

    from jiuwenclaw.perf.context import increment_react_iteration, reset_react_iteration
    from jiuwenclaw.perf.extract import extract_react_iteration

    reset_react_iteration()
    increment_react_iteration()
    increment_react_iteration()
    assert extract_react_iteration(SimpleNamespace(inputs=None)) == 2

    reset_react_iteration()
    ctx = SimpleNamespace(inputs=SimpleNamespace(iteration=3))
    assert extract_react_iteration(ctx) == 3
    assert extract_react_iteration(SimpleNamespace(inputs={"step": 5})) == 5


def test_extract_agent_id_prefers_subagent_and_card() -> None:
    from types import SimpleNamespace

    from jiuwenclaw.perf.extract import extract_agent_id

    card = SimpleNamespace(id="spawn_researcher", name="spawn_researcher")
    deep = SimpleNamespace(card=card)
    inner = SimpleNamespace(id="262923614bdb45709ece791ffb3d3409", card=None)

    assert extract_agent_id(inner, deep_agent=deep) == "spawn_researcher"
    assert extract_agent_id(inner, deep_agent=None) == ""


def test_get_request_context_returns_none_without_binding() -> None:
    from jiuwenclaw.perf.context import clear_request_context, get_request_context

    clear_request_context()
    assert get_request_context() is None


def test_accumulator_record_task_with_attributed_stats() -> None:
    acc = RequestSummaryAccumulator(
        meta=RequestMeta(
            session_id="sess-1",
            request_id="req-1",
            channel_id="web",
            mode="plan",
            trace_id=None,
            started_at=1000.0,
        ),
    )
    task_id = "todo:abc"
    acc.record_llm(
        LlmPerfEvent(
            llm_call_id="llm-1",
            duration_ms=100.0,
            model="glm",
            iteration=1,
            input_tokens=10,
            output_tokens=5,
            status="ok",
            task_id=task_id,
        )
    )
    acc.record_tool(
        ToolPerfEvent(
            tool_call_id="call-1",
            name="web_search",
            duration_ms=50.0,
            status="ok",
            task_id=task_id,
        )
    )
    acc.record_task(
        TaskPerfEvent(
            task_id=task_id,
            task_content="Stage 1",
            source="todo",
            started_at=1000.0,
            ended_at=1001.0,
            duration_ms=1000.0,
            status="succeeded",
        )
    )

    summary = acc.finalize(status="ok", ended_at=1002.0)
    assert summary["summary"]["stats"]["task"]["count"] == 1
    assert summary["summary"]["stats"]["unattributed_s"] == 0.0
    assert summary["tasks"][0]["stats"]["llm"]["count"] == 1
    assert summary["tasks"][0]["duration_s"] == 1.0


def test_collector_restores_accumulator_on_write_failure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_perf_summary_config()
    collector = PerfCollector()
    collector.begin_request(
        session_id="sess-z",
        request_id="req-z",
        channel_id="web",
        mode="agent.plan",
        started_at=1000.0,
    )

    write_attempts = {"count": 0}

    def _append_once_fail(*_args, **_kwargs) -> None:
        write_attempts["count"] += 1
        if write_attempts["count"] == 1:
            raise OSError("disk full")

    monkeypatch.setattr("jiuwenclaw.perf.collector.append_request_summary", _append_once_fail)
    monkeypatch.setattr("jiuwenclaw.perf.guard.logger.warning", lambda *_a, **_k: None)
    collector.finalize_request("req-z", status="ok", ended_at=1001.0)

    restored = collector.get_accumulator("req-z")
    assert restored is not None
    assert restored.flushed is False

    collector.finalize_request("req-z", status="ok", ended_at=1001.0)
    assert write_attempts["count"] == 2
    assert collector.get_accumulator("req-z") is None


def test_collector_does_not_prune_active_long_running_request() -> None:
    init_perf_summary_config()
    collector = PerfCollector()
    stale_started_at = time.time() - PerfCollector._STALE_ORPHAN_AGE_S - 60.0

    collector.begin_request(
        session_id="sess-long",
        request_id="req-long",
        channel_id="web",
        mode="agent.plan",
        started_at=stale_started_at,
    )
    collector.record_llm(
        "req-long",
        LlmPerfEvent(
            llm_call_id="llm-long",
            duration_ms=100.0,
            model="gpt-4",
            iteration=1,
            input_tokens=1,
            output_tokens=1,
            status="ok",
        ),
    )

    collector.begin_request(
        session_id="sess-other",
        request_id="req-other",
        channel_id="web",
        mode="agent.plan",
    )

    assert collector.get_accumulator("req-long") is not None
    assert "req-long" in collector._active_request_ids

    summary = collector.get_accumulator("req-long").finalize(status="ok")
    assert summary["summary"]["stats"]["llm"]["count"] == 1


def test_collector_prunes_inactive_orphan_accumulator(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    init_perf_summary_config()
    collector = PerfCollector()
    stale_started_at = time.time() - PerfCollector._STALE_ORPHAN_AGE_S - 60.0
    monkeypatch.setattr("jiuwenclaw.perf.collector.logger.warning", lambda *_a, **_k: None)

    collector.begin_request(
        session_id="sess-orphan",
        request_id="req-orphan",
        channel_id="web",
        mode="agent.plan",
        started_at=stale_started_at,
    )
    collector._active_request_ids.discard("req-orphan")

    collector.begin_request(
        session_id="sess-trigger",
        request_id="req-trigger",
        channel_id="web",
        mode="agent.plan",
    )

    assert collector.get_accumulator("req-orphan") is None
    assert collector.get_accumulator("req-trigger") is not None


def test_collector_finalize_writes_jsonl(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    init_perf_summary_config()
    monkeypatch.setattr(
        "jiuwenclaw.perf.writer.get_agent_sessions_dir",
        lambda: tmp_path,
    )

    collector = PerfCollector()
    collector.begin_request(
        session_id="sess-x",
        request_id="req-x",
        channel_id="web",
        mode="agent.plan",
        started_at=1000.0,
    )
    collector.record_llm(
        "req-x",
        LlmPerfEvent(
            llm_call_id="llm-x",
            duration_ms=800.0,
            model="gpt-4",
            iteration=1,
            input_tokens=10,
            output_tokens=5,
            status="ok",
        ),
    )
    collector.finalize_request("req-x", status="ok", ended_at=1001.0)
    from jiuwenclaw.perf.writer import flush_request_summary_writer

    flush_request_summary_writer()

    out_path = request_summaries_file("sess-x", str(tmp_path))
    assert out_path.exists()
    payload = json.loads(out_path.read_text(encoding="utf-8").strip())
    assert payload["schema_version"] == 1
    assert payload["summary"]["stats"]["llm"]["total_s"] == 0.8


def test_append_request_summary_sync_fallback(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.perf.writer.get_agent_sessions_dir",
        lambda: tmp_path,
    )
    append_request_summary("sess-y", {"schema_version": 5, "meta": {"request_id": "req-y"}})
    from jiuwenclaw.perf.writer import flush_request_summary_writer

    flush_request_summary_writer()
    out_path = request_summaries_file("sess-y", str(tmp_path))
    assert "req-y" in out_path.read_text(encoding="utf-8")
