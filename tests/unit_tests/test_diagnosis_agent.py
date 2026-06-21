# Copyright (c) Huawei Technologies, Co., Ltd. 2026. All rights reserved.
"""Tests for DiagnosisAgent — models, tools, and core functionality."""

import pytest
import json

from jiuwenswarm.evolve.diagnosis.models import DiagnosisIssue, DiagnosisResult, ALLOWED_ISSUE_TYPES
from jiuwenswarm.evolve.diagnosis.tools import DiagnosisToolExecutor, _truncate_tool_output


class TestDiagnosisIssue:
    def test_basic_issue(self):
        issue = DiagnosisIssue(
            issue_type="工具错误",
            summary="bash command not found",
            evidence="span #7: name='gen_ai.tool.execute: bash'",
            trace_id="abc123",
            span_index=7,
            root_cause="Skill missing path specification",
            suggested_fix="Add full path to experience",
        )
        assert issue.issue_type == "工具错误"
        assert issue.span_index == 7
        assert issue.root_cause == "Skill missing path specification"

    def test_all_valid_issue_types(self):
        for it in ALLOWED_ISSUE_TYPES:
            issue = DiagnosisIssue(
                issue_type=it, summary="test", evidence="e",
                trace_id="abc", span_index=0,
            )
            assert issue.issue_type == it

    def test_invalid_issue_type_raises(self):
        with pytest.raises(ValueError):
            DiagnosisIssue(
                issue_type="invalid", summary="test", evidence="e",
                trace_id="abc", span_index=0,
            )


class TestDiagnosisResult:
    def test_diagnose_mode(self):
        result = DiagnosisResult(
            mode="diagnose",
            issues=[],
            response="No issues found",
            iterations=5,
            budget_exceeded=False,
        )
        assert result.mode == "diagnose"
        assert result.proposals is None

    def test_budget_exceeded(self):
        result = DiagnosisResult(
            mode="diagnose",
            issues=[],
            response="[budget-exceeded] partial analysis",
            iterations=20,
            budget_exceeded=True,
        )
        assert result.budget_exceeded is True

    def test_with_issues(self):
        issue = DiagnosisIssue(
            issue_type="幻觉", summary="LLM fabricated result",
            evidence="span #42: assistant claims API returned 200",
            trace_id="def456", span_index=42,
        )
        result = DiagnosisResult(
            mode="diagnose",
            issues=[issue],
            response="Found 1 hallucination",
            iterations=8,
        )
        assert len(result.issues) == 1
        assert result.issues[0].issue_type == "幻觉"


class TestTruncateToolOutput:
    def test_short_output_not_truncated(self):
        result = _truncate_tool_output("short content", max_chars=1000)
        assert result == "short content"

    def test_long_output_truncated(self):
        lines = [f"line {i} with lots of data content padding here" for i in range(500)]
        content = "\n".join(lines)
        result = _truncate_tool_output(content, max_chars=5000, head_lines=50, tail_lines=30)
        assert "[...truncated" in result
        assert "lines omitted" in result

    def test_exact_boundary_not_truncated(self):
        content = "x" * 9999
        result = _truncate_tool_output(content, max_chars=10000)
        assert result == content

    def test_truncation_preserves_head_and_tail(self):
        lines = [f"HEADER line {i}" for i in range(60)] + \
                [f"MIDDLE line {i}" for i in range(100)] + \
                [f"TAIL line {i}" for i in range(40)]
        content = "\n".join(lines)
        result = _truncate_tool_output(content, max_chars=5000, head_lines=5, tail_lines=5)
        assert "HEADER line 0" in result
        assert "TAIL line 39" in result
        assert "MIDDLE" not in result


class MockStore:
    """Minimal mock store for testing DiagnosisToolExecutor."""

    def read_spans(self, trace_id):
        return [
            {"name": "gen_ai.chat", "span_id": "s1", "trace_id": trace_id,
             "attributes": '{"gen_ai.span.type": "model"}', "events": '[]',
             "start_time_ns": 100, "status_code": "OK", "status_description": ""},
            {"name": "gen_ai.tool.execute: bash", "span_id": "s2", "trace_id": trace_id,
             "attributes": '{"gen_ai.span.type": "tool", "gen_ai.tool.name": "bash"}', "events": '[]',
             "start_time_ns": 200, "status_code": "ERROR", "status_description": "command not found"},
        ]

    def get_recent_trace_ids(self, limit=20):
        return ["abc123", "def456"]

    def get_trace_ids_since(self, since, limit=100):
        return ["abc123"]

    def query_by_trace_id(self, trace_id):
        return {"trace_id": trace_id, "proposals": [], "decision_results": [], "apply_records": []}

    def get_batch(self, batch_id):
        return {"batch_id": batch_id, "proposals": []}

    _traces_db_path = "traces.db"


class TestDiagnosisToolExecutor:
    def test_read_spans_basic(self):
        executor = DiagnosisToolExecutor(store=MockStore())
        result = executor.execute("read_spans", {"trace_id": "abc123", "limit": 10})
        assert result["trace_id"] == "abc123"
        assert result["total_spans"] == 2
        assert "spans" in result

    def test_read_spans_pagination(self):
        executor = DiagnosisToolExecutor(store=MockStore())
        result = executor.execute("read_spans", {"trace_id": "abc123", "offset": 0, "limit": 1})
        assert result["returned"] == 1
        assert result["total_spans"] == 2

    def test_read_spans_name_filter(self):
        executor = DiagnosisToolExecutor(store=MockStore())
        result = executor.execute("read_spans", {"trace_id": "abc123", "name_filter": "bash"})
        assert result["returned"] == 1
        assert "bash" in result["spans"][0]["name"]

    def test_search_spans(self):
        executor = DiagnosisToolExecutor(store=MockStore())
        result = executor.execute("search_spans", {"trace_id": "abc123", "pattern": "error"})
        assert len(result["matches"]) > 0

    def test_list_traces(self):
        executor = DiagnosisToolExecutor(store=MockStore())
        result = executor.execute("list_traces", {"limit": 5})
        assert len(result["traces"]) == 2

    def test_query_evolve_records(self):
        executor = DiagnosisToolExecutor(store=MockStore())
        result = executor.execute("query_evolve_records", {"trace_id": "abc123"})
        assert result["trace_id"] == "abc123"

    def test_unknown_tool_returns_error(self):
        executor = DiagnosisToolExecutor(store=MockStore())
        result = executor.execute("unknown_tool", {})
        assert "error" in result

    def test_submit_result(self):
        executor = DiagnosisToolExecutor(store=MockStore())
        result = executor.execute("submit_result", {"result": '{"mode": "diagnose"}'})
        assert result == "TASK_COMPLETED"


class TestToolCallParsing:
    """Test DiagnosisAgent._parse_tool_calls static method."""

    def test_json_tool_call(self):
        from jiuwenswarm.evolve.diagnosis.agent import DiagnosisAgent
        content = '{"tool_name": "read_spans", "arguments": {"trace_id": "abc123"}}'
        result = DiagnosisAgent._parse_tool_calls(content)
        assert len(result) == 1
        assert result[0]["name"] == "read_spans"

    def test_no_tool_call(self):
        from jiuwenswarm.evolve.diagnosis.agent import DiagnosisAgent
        content = "I think the trace shows a bash command error."
        result = DiagnosisAgent._parse_tool_calls(content)
        assert len(result) == 0

    def test_submit_result_detection(self):
        from jiuwenswarm.evolve.diagnosis.agent import DiagnosisAgent
        content = '{"tool_name": "submit_result", "arguments": {"result": "{\\"mode\\": \\"diagnose\\"}"}}'
        result = DiagnosisAgent._parse_tool_calls(content)
        assert len(result) == 1
        assert result[0]["name"] == "submit_result"
