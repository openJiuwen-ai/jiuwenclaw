# coding: utf-8
"""Tests for JiuClawQABlockFreezeRail freeze-produce scheduling."""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock, patch

from openjiuwen.core.context_engine.qa_block.freezer import FreezeCommitResult
from openjiuwen.core.context_engine.qa_block.schema import QABlockEntry

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "jiuwenclaw"
    / "agentserver"
    / "deep_agent"
    / "rails"
    / "qa_block_freeze_rail.py"
)
assert _MODULE_PATH.exists(), f"rail module path does not exist: {_MODULE_PATH}"
_spec = importlib.util.spec_from_file_location("qa_block_freeze_rail_under_test", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
JiuClawQABlockFreezeRail = _module.JiuClawQABlockFreezeRail


def _make_commit(*, qa_id: str = "qa_001") -> FreezeCommitResult:
    entry = QABlockEntry(qa_id=qa_id, qa_index=1, status="completed")
    return FreezeCommitResult(entry=entry, native_messages=[SimpleNamespace(content="msg")])


async def _schedule_freeze_artifact_produce_async(rail: Any, **kwargs: Any) -> None:
    await getattr(rail, "_schedule_freeze_artifact_produce_async")(**kwargs)


def _on_freeze_commit(rail: Any, session: Any, context: Any, commit: FreezeCommitResult) -> None:
    getattr(rail, "_on_freeze_commit")(session, context, commit)


class TestQABlockFreezeRailProduceSchedule(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.rail = JiuClawQABlockFreezeRail()
        self.rail.workspace = SimpleNamespace(root_path="/tmp/ws")
        self.mgr = MagicMock()
        self.mgr.schedule_freeze_artifact_produce = MagicMock(return_value=True)
        self.rail.attach_qa_artifact(self.mgr)

    async def test_schedule_async_calls_mgr_with_processor_ctx(self) -> None:
        context = MagicMock()
        context.workspace_dir.return_value = "/tmp/ws"
        context.get_session_ref.return_value = SimpleNamespace(get_session_id=lambda: "session-1")
        setattr(context, "_sys_operation", None)
        session = SimpleNamespace()
        messages = [SimpleNamespace(content="native")]

        await _schedule_freeze_artifact_produce_async(
            self.rail,
            _session=session,
            context=context,
            qa_id="qa_002",
            native_messages=messages,
        )

        self.mgr.schedule_freeze_artifact_produce.assert_called_once()
        call_kwargs = self.mgr.schedule_freeze_artifact_produce.call_args.kwargs
        self.assertEqual(call_kwargs["qa_id"], "qa_002")
        self.assertIs(call_kwargs["native_messages"], messages)
        self.assertIs(call_kwargs["workspace"], self.rail.workspace)
        artifact_ctx = self.mgr.schedule_freeze_artifact_produce.call_args.args[0]
        self.assertIs(artifact_ctx.context, context)
        self.assertEqual(artifact_ctx.workspace.root_path, "/tmp/ws")

    async def test_schedule_async_skips_when_mgr_missing(self) -> None:
        self.rail.attach_qa_artifact(None)
        await _schedule_freeze_artifact_produce_async(
            self.rail,
            _session=object(),
            context=MagicMock(),
            qa_id="qa_001",
            native_messages=[],
        )
        self.mgr.schedule_freeze_artifact_produce.assert_not_called()

    async def test_schedule_async_skips_when_workspace_missing(self) -> None:
        self.rail.workspace = None
        await _schedule_freeze_artifact_produce_async(
            self.rail,
            _session=object(),
            context=MagicMock(),
            qa_id="qa_001",
            native_messages=[],
        )
        self.mgr.schedule_freeze_artifact_produce.assert_not_called()

    async def test_on_freeze_commit_dispatches_async_task(self) -> None:
        commit = _make_commit(qa_id="qa_003")
        context = MagicMock()
        session = SimpleNamespace()
        schedule_attr = "_schedule_freeze_artifact_produce_async"

        with patch.object(self.rail, schedule_attr, autospec=True) as mock_async:
            _on_freeze_commit(self.rail, session, context, commit)
            await asyncio.sleep(0)

        mock_async.assert_awaited_once_with(
            _session=session,
            context=context,
            qa_id="qa_003",
            native_messages=commit.native_messages,
        )

    def test_on_freeze_commit_without_running_loop_is_noop(self) -> None:
        commit = _make_commit()
        with patch.object(asyncio, "get_running_loop", side_effect=RuntimeError):
            _on_freeze_commit(self.rail, object(), MagicMock(), commit)
        self.mgr.schedule_freeze_artifact_produce.assert_not_called()


if __name__ == "__main__":
    unittest.main()
