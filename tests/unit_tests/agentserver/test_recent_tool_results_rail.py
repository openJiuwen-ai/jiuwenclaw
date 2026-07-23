# coding: utf-8
"""Unit tests for RecentToolResultsRail."""
import asyncio
import unittest
from types import SimpleNamespace

from openjiuwen.core.single_agent.rail.base import ToolCallInputs
from openjiuwen.core.session.recent_tool_results import (
    get_recent_results,
    record_tool_result,
)

from jiuwenclaw.agentserver.deep_agent.rails.recent_tool_results_rail import (
    RecentToolResultsRail,
    _build_entry,
    _format_results_section,
    _format_entry,
    DEFAULT_WHITELIST,
)


class MockSession:
    """Minimal session stub with get_state / update_state."""

    def __init__(self):
        self._state: dict = {}

    def get_state(self, key=None):
        if key is None:
            return dict(self._state)
        return self._state.get(key)

    def update_state(self, data: dict):
        self._state.update(data)


def _make_ctx(session, tool_name, tool_args=None, tool_result=None, exception=None):
    """Build a lightweight ctx for after_tool_call / before_model_call."""
    return SimpleNamespace(
        session=session,
        inputs=ToolCallInputs(
            tool_name=tool_name,
            tool_args=tool_args,
            tool_result=tool_result,
            tool_msg=None,
        ),
        exception=exception,
        extra={},
    )


class TestAfterToolCallRecord(unittest.IsolatedAsyncioTestCase):
    async def test_record_whitelisted_success(self):
        sess = MockSession()
        rail = RecentToolResultsRail()
        ctx = _make_ctx(
            sess,
            tool_name="bash",
            tool_args={"command": "ls"},
            tool_result="file1\nfile2",
        )
        await rail.after_tool_call(ctx)
        results = get_recent_results(sess)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tool"], "bash")
        self.assertEqual(results[0]["status"], "success")
        self.assertEqual(results[0]["result"], "file1\nfile2")
        self.assertIsNone(results[0]["error"])

    async def test_record_whitelist_bash(self):
        sess = MockSession()
        rail = RecentToolResultsRail()
        ctx = _make_ctx(
            sess,
            tool_name="bash",
            tool_args={"command": "ls"},
            tool_result="file1\nfile2",
        )
        await rail.after_tool_call(ctx)
        self.assertEqual(len(get_recent_results(sess)), 1)

    async def test_skip_non_whitelisted(self):
        sess = MockSession()
        rail = RecentToolResultsRail()
        ctx = _make_ctx(
            sess,
            tool_name="search",
            tool_args={"query": "auth"},
            tool_result="found 3 items",
        )
        await rail.after_tool_call(ctx)
        self.assertEqual(get_recent_results(sess), [])

    async def test_record_failed_whitelisted(self):
        sess = MockSession()
        rail = RecentToolResultsRail()
        exc = RuntimeError("connection timeout")
        ctx = _make_ctx(
            sess,
            tool_name="bash",
            tool_args={"command": "invalid_cmd"},
            tool_result=None,
            exception=exc,
        )
        await rail.after_tool_call(ctx)
        results = get_recent_results(sess)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["status"], "failed")
        self.assertIsNone(results[0]["result"])
        self.assertIn("connection timeout", results[0]["error"])

    async def test_custom_whitelist(self):
        sess = MockSession()
        rail = RecentToolResultsRail(whitelist=frozenset({"read_file"}))
        ctx = _make_ctx(
            sess,
            tool_name="read_file",
            tool_args={"path": "/foo"},
            tool_result="content",
        )
        await rail.after_tool_call(ctx)
        results = get_recent_results(sess)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["tool"], "read_file")

        ctx2 = _make_ctx(
            sess,
            tool_name="bash",
            tool_args={"command": "ls"},
            tool_result="file1\nfile2",
        )
        await rail.after_tool_call(ctx2)
        # bash not in custom whitelist, should be skipped
        self.assertEqual(len(get_recent_results(sess)), 1)

    async def test_child_after_tool_call_noop(self):
        """Child agent (parent_session set) does not record own calls."""
        parent_sess = MockSession()
        child_sess = MockSession()
        rail = RecentToolResultsRail(parent_session=parent_sess)
        ctx = _make_ctx(
            child_sess,
            tool_name="bash",
            tool_args={"command": "ls"},
            tool_result="file1\nfile2",
        )
        await rail.after_tool_call(ctx)
        # Child should NOT have recorded anything
        self.assertEqual(get_recent_results(child_sess), [])
        # Parent should also NOT have been written to
        self.assertEqual(get_recent_results(parent_sess), [])


class TestBeforeModelCallInject(unittest.IsolatedAsyncioTestCase):
    async def test_parent_before_model_call_noop(self):
        """Parent agent (parent_session=None) does not inject into own prompt."""
        sess = MockSession()
        rail = RecentToolResultsRail()
        record_tool_result(sess, {
            "tool": "bash", "args": {"command": "ls"}, "result": "r1",
            "status": "success", "error": None, "timestamp": "t1",
        })
        ctx = _make_ctx(sess, tool_name="")
        await rail.before_model_call(ctx)
        self.assertNotIn("environment_context", ctx.extra)

    async def test_child_inject_parent_only(self):
        """Child agent only injects parent's results, not own."""
        parent_sess = MockSession()
        child_sess = MockSession()
        rail = RecentToolResultsRail(parent_session=parent_sess)

        record_tool_result(parent_sess, {
            "tool": "bash", "args": {"command": "ls"}, "result": "p1",
            "status": "success", "error": None, "timestamp": "t1",
        })
        ctx = _make_ctx(child_sess, tool_name="")
        await rail.before_model_call(ctx)

        env_ctx = ctx.extra.get("environment_context", [])
        self.assertEqual(len(env_ctx), 1)
        content = env_ctx[0]["content"]
        self.assertIn("父 agent", content)
        self.assertIn("bash", content)

    async def test_empty_parent_no_inject(self):
        """Child agent does not inject when parent has no records."""
        parent_sess = MockSession()
        child_sess = MockSession()
        rail = RecentToolResultsRail(parent_session=parent_sess)
        ctx = _make_ctx(child_sess, tool_name="")
        await rail.before_model_call(ctx)
        self.assertNotIn("environment_context", ctx.extra)

    async def test_child_inject_parent_with_records(self):
        parent_sess = MockSession()
        child_sess = MockSession()
        rail = RecentToolResultsRail(parent_session=parent_sess)

        record_tool_result(parent_sess, {
            "tool": "search", "args": {}, "result": "r",
            "status": "success", "error": None, "timestamp": "t",
        })
        ctx = _make_ctx(child_sess, tool_name="")
        await rail.before_model_call(ctx)

        env_ctx = ctx.extra.get("environment_context", [])
        self.assertEqual(len(env_ctx), 1)
        content = env_ctx[0]["content"]
        self.assertIn("父 agent", content)
        self.assertIn("search", content)

    async def test_inject_marker_format(self):
        """Verify ✓ marker appears for successful tool results in child injection."""
        parent_sess = MockSession()
        child_sess = MockSession()
        rail = RecentToolResultsRail(parent_session=parent_sess)
        record_tool_result(parent_sess, {
            "tool": "bash", "args": {"command": "ls"}, "result": "ok",
            "status": "success", "error": None, "timestamp": "t",
        })
        ctx = _make_ctx(child_sess, tool_name="")
        await rail.before_model_call(ctx)
        content = ctx.extra["environment_context"][0]["content"]
        self.assertIn("✓", content)
        self.assertIn("bash", content)


class TestParentChildUnidirectional(unittest.IsolatedAsyncioTestCase):
    async def test_child_does_not_write_parent(self):
        """Child agent does not record own calls (after_tool_call is no-op)."""
        parent_sess = MockSession()
        child_sess = MockSession()
        rail = RecentToolResultsRail(parent_session=parent_sess)

        ctx = _make_ctx(
            child_sess,
            tool_name="bash",
            tool_args={"command": "ls"},
            tool_result="child result",
        )
        await rail.after_tool_call(ctx)

        # Child does not record own calls
        self.assertEqual(len(get_recent_results(child_sess)), 0)
        self.assertEqual(len(get_recent_results(parent_sess)), 0)

    async def test_parent_does_not_read_child(self):
        parent_sess = MockSession()
        child_sess = MockSession()
        parent_rail = RecentToolResultsRail()
        child_rail = RecentToolResultsRail(parent_session=parent_sess)

        record_tool_result(child_sess, {
            "tool": "run_test", "args": {}, "result": "passed",
            "status": "success", "error": None, "timestamp": "t",
        })
        ctx = _make_ctx(parent_sess, tool_name="")
        await parent_rail.before_model_call(ctx)

        env_ctx = ctx.extra.get("environment_context", [])
        self.assertEqual(len(env_ctx), 0)

    async def test_child_reads_parent_live(self):
        parent_sess = MockSession()
        child_sess = MockSession()
        child_rail = RecentToolResultsRail(parent_session=parent_sess)

        ctx1 = _make_ctx(child_sess, tool_name="")
        await child_rail.before_model_call(ctx1)
        self.assertNotIn("environment_context", ctx1.extra)

        record_tool_result(parent_sess, {
            "tool": "search", "args": {}, "result": "parent result",
            "status": "success", "error": None, "timestamp": "t",
        })

        ctx2 = _make_ctx(child_sess, tool_name="")
        await child_rail.before_model_call(ctx2)
        env_ctx = ctx2.extra.get("environment_context", [])
        self.assertEqual(len(env_ctx), 1)
        self.assertIn("parent result", env_ctx[0]["content"])


class TestBuildEntry(unittest.TestCase):
    def test_success_entry(self):
        sess = MockSession()
        ctx = _make_ctx(
            sess,
            tool_name="search",
            tool_args={"q": "test"},
            tool_result={"hits": 3},
        )
        entry = _build_entry(ctx)
        self.assertEqual(entry["tool"], "search")
        self.assertEqual(entry["status"], "success")
        self.assertIsNone(entry["error"])
        self.assertIn("hits", entry["result"])

    def test_failed_entry(self):
        sess = MockSession()
        ctx = _make_ctx(
            sess,
            tool_name="search",
            tool_args={"q": "x"},
            exception=ValueError("bad query"),
        )
        entry = _build_entry(ctx)
        self.assertEqual(entry["status"], "failed")
        self.assertIsNone(entry["result"])
        self.assertIn("bad query", entry["error"])


class TestFormatEntry(unittest.TestCase):
    def test_success_format(self):
        entry = {
            "tool": "search", "args": {"q": "x"}, "result": "ok",
            "status": "success", "error": None,
        }
        line = _format_entry(1, entry)
        self.assertIn("[1]", line)
        self.assertIn("search", line)
        self.assertIn("✓", line)

    def test_failed_format(self):
        entry = {
            "tool": "bash", "args": {"cmd": "ls"}, "result": None,
            "status": "failed", "error": "timeout",
        }
        line = _format_entry(2, entry)
        self.assertIn("[2]", line)
        self.assertIn("bash", line)
        self.assertIn("✗", line)
        self.assertIn("timeout", line)


if __name__ == "__main__":
    unittest.main()
