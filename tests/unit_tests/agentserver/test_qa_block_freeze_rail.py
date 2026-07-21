# coding: utf-8
"""Tests for JiuClawQABlockFreezeRail freeze-produce scheduling."""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.context_engine.qa_block.freezer import FreezeCommitResult
from openjiuwen.core.context_engine.qa_block.schema import QABlockEntry, QABlockRegistry
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import INTERRUPTION_KEY
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs

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


def _make_freeze_ctx(
    *,
    query: Any = None,
    interruption: Any = None,
) -> tuple[AgentCallbackContext, Any, Any]:
    state: dict[str, Any] = {}
    if interruption is not None:
        state[INTERRUPTION_KEY] = interruption

    def get_state(key: str, default: Any = None) -> Any:
        return state.get(key, default)

    def update_state(updates: dict[str, Any]) -> None:
        state.update(updates)

    session = SimpleNamespace(
        get_session_id=lambda: "session-1",
        get_state=get_state,
        update_state=update_state,
        _state=state,
    )
    context = MagicMock()
    context.context_id.return_value = "ctx-1"
    context_engine = MagicMock()
    context_engine.get_context.return_value = context
    context_engine.get_history_qa_buffer.return_value = []
    context_engine.save_contexts = AsyncMock()
    agent = SimpleNamespace()
    if query is None:
        query = InteractiveInput()
    ctx = AgentCallbackContext(
        agent=agent,
        inputs=InvokeInputs(query=query),
        session=session,
    )
    return ctx, context_engine, session


def _make_interactive_freeze_ctx(
    *, interruption: Any = None
) -> tuple[AgentCallbackContext, Any, Any]:
    return _make_freeze_ctx(interruption=interruption)


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


class TestQABlockFreezeRailInteractiveResume(unittest.IsolatedAsyncioTestCase):
    """InteractiveInput resume: freeze only when interruption is settled."""

    def setUp(self) -> None:
        self.rail = JiuClawQABlockFreezeRail()
        self.rail.workspace = SimpleNamespace(root_path="/tmp/ws")
        self.freeze_mock = AsyncMock(return_value=_make_commit().entry)
        self.rail._freezer = SimpleNamespace(freeze=self.freeze_mock)
        self.rail._maybe_await_overview_before_freeze = AsyncMock()

    async def test_interactive_resume_with_none_result_freezes_when_no_interrupt(self) -> None:
        ctx, context_engine, _session = _make_interactive_freeze_ctx()
        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "clear_assembly_committed_qa_id"), patch.object(
            _module, "QABlockStore", return_value=MagicMock()
        ), patch.object(_module, "post_agent_execute_for_session", new_callable=AsyncMock):
            await self.rail.after_invoke(ctx)

        self.freeze_mock.assert_awaited_once()

    async def test_interactive_resume_skips_when_session_still_interrupted(self) -> None:
        ctx, context_engine, _session = _make_interactive_freeze_ctx(
            interruption=SimpleNamespace(original_query="paused"),
        )
        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "QABlockStore", return_value=MagicMock()):
            await self.rail.after_invoke(ctx)

        self.freeze_mock.assert_not_awaited()

    async def test_interactive_resume_skips_when_result_type_interrupt(self) -> None:
        ctx, context_engine, _session = _make_interactive_freeze_ctx()
        ctx.inputs.result = {"result_type": "interrupt", "interrupt_ids": ["id-1"]}
        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "QABlockStore", return_value=MagicMock()):
            await self.rail.after_invoke(ctx)

        self.freeze_mock.assert_not_awaited()

    async def test_failed_freeze_clears_empty_current_qa_id(self) -> None:
        ctx, context_engine, session = _make_interactive_freeze_ctx()
        registry = QABlockRegistry(session_id="session-1", current_qa_id="qa_stale", next_qa_index=2)
        self.freeze_mock.return_value = None

        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "clear_assembly_committed_qa_id"), patch.object(
            _module, "QABlockStore", return_value=MagicMock()
        ), patch.object(_module, "load_registry", return_value=registry), patch.object(
            _module, "save_registry"
        ) as mock_save, patch.object(
            _module, "post_agent_execute_for_session", new_callable=AsyncMock
        ) as mock_flush:
            await self.rail.after_invoke(ctx)

        self.freeze_mock.assert_awaited_once()
        self.assertIsNone(registry.current_qa_id)
        mock_save.assert_called_once()
        mock_flush.assert_awaited_once()
        context_engine.save_contexts.assert_awaited()

    async def test_failed_freeze_skips_save_contexts_when_persist_context_false(self) -> None:
        context_engine = MagicMock()
        context_engine.get_context.return_value = MagicMock(context_id=lambda: "ctx-1")
        context_engine.get_history_qa_buffer.return_value = []
        context_engine.save_contexts = AsyncMock()
        session = SimpleNamespace(get_session_id=lambda: "session-1", get_state=lambda *_a, **_k: None)
        registry = QABlockRegistry(session_id="session-1", current_qa_id="qa_stale", next_qa_index=2)
        self.freeze_mock.return_value = None
        agent = SimpleNamespace()

        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "clear_assembly_committed_qa_id"), patch.object(
            _module, "QABlockStore", return_value=MagicMock()
        ), patch.object(_module, "load_registry", return_value=registry), patch.object(
            _module, "save_registry"
        ), patch.object(_module, "post_agent_execute_for_session", new_callable=AsyncMock):
            await self.rail.freeze_current_qa_sync(
                "session-1",
                agent=agent,
                session=session,
                persist_context=False,
            )

        self.assertIsNone(registry.current_qa_id)
        context_engine.save_contexts.assert_not_awaited()


class TestQABlockFreezeRailFirstAskPlainQuery(unittest.IsolatedAsyncioTestCase):
    """First permission ASK on a normal user query must not freeze (same-QA keep)."""

    def setUp(self) -> None:
        self.rail = JiuClawQABlockFreezeRail()
        self.rail.workspace = SimpleNamespace(root_path="/tmp/ws")
        self.freeze_mock = AsyncMock(return_value=_make_commit().entry)
        self.rail._freezer = SimpleNamespace(freeze=self.freeze_mock)
        self.rail._maybe_await_overview_before_freeze = AsyncMock()

    async def test_plain_query_skips_when_session_interrupted(self) -> None:
        ctx, context_engine, _session = _make_freeze_ctx(
            query="请读取桌面文件并总结",
            interruption=SimpleNamespace(original_query="paused"),
        )
        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "QABlockStore", return_value=MagicMock()):
            await self.rail.after_invoke(ctx)

        self.freeze_mock.assert_not_awaited()

    async def test_plain_query_skips_when_result_type_interrupt(self) -> None:
        ctx, context_engine, _session = _make_freeze_ctx(query="请发送文件给我")
        ctx.inputs.result = {"result_type": "interrupt", "interrupt_ids": ["call_1"]}
        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "QABlockStore", return_value=MagicMock()):
            await self.rail.after_invoke(ctx)

        self.freeze_mock.assert_not_awaited()

    async def test_plain_query_freezes_when_not_interrupted(self) -> None:
        ctx, context_engine, _session = _make_freeze_ctx(query="你好")
        ctx.inputs.result = {"result_type": "answer", "output": "hi"}
        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "clear_assembly_committed_qa_id"), patch.object(
            _module, "QABlockStore", return_value=MagicMock()
        ), patch.object(_module, "post_agent_execute_for_session", new_callable=AsyncMock):
            await self.rail.after_invoke(ctx)

        self.freeze_mock.assert_awaited_once()

    async def test_settled_result_with_stale_key_freezes_and_clears_key(self) -> None:
        """Explicit answer + leftover INTERRUPTION_KEY → freeze and clear (not mis-skip)."""
        stale = SimpleNamespace(original_query="stale")
        ctx, context_engine, session = _make_freeze_ctx(
            query="今天天气怎么样",
            interruption=stale,
        )
        ctx.inputs.result = {"result_type": "answer", "output": "sunny"}
        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "clear_assembly_committed_qa_id"), patch.object(
            _module, "QABlockStore", return_value=MagicMock()
        ), patch.object(_module, "post_agent_execute_for_session", new_callable=AsyncMock):
            await self.rail.after_invoke(ctx)

        self.freeze_mock.assert_awaited_once()
        self.assertIsNone(session.get_state(INTERRUPTION_KEY))

    async def test_secondary_ask_keeps_interrupt_key(self) -> None:
        """Second ASK during resume: skip freeze and do NOT clear INTERRUPTION_KEY."""
        pending = SimpleNamespace(original_query="still waiting")
        ctx, context_engine, session = _make_freeze_ctx(
            query=InteractiveInput(),
            interruption=pending,
        )
        ctx.inputs.result = {"result_type": "interrupt", "interrupt_ids": ["call_2"]}
        with patch.object(_module, "resolve_context_engine", return_value=context_engine), patch.object(
            _module, "resolve_summarizer_model", return_value=None
        ), patch.object(_module, "QABlockStore", return_value=MagicMock()):
            await self.rail.after_invoke(ctx)

        self.freeze_mock.assert_not_awaited()
        self.assertIs(session.get_state(INTERRUPTION_KEY), pending)


if __name__ == "__main__":
    unittest.main()
