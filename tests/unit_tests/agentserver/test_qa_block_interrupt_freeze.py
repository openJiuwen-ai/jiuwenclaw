# coding: utf-8
"""Tests for QA block freeze before interrupt (supplement/cancel)."""

from __future__ import annotations

import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter
from jiuwenclaw.schema.agent import AgentRequest


def _make_adapter() -> JiuWenClawDeepAdapter:
    adapter = JiuWenClawDeepAdapter()
    setattr(adapter, "_instance", SimpleNamespace(card=SimpleNamespace(id="agent-1")))
    setattr(adapter, "_task_planning_rail", MagicMock())
    setattr(
        adapter,
        "_qa_block_freeze_rail",
        SimpleNamespace(freeze_current_qa_sync=AsyncMock()),
    )
    return adapter


async def _freeze_qa_block_before_abort(adapter: JiuWenClawDeepAdapter, *args, **kwargs) -> None:
    await getattr(adapter, "_freeze_qa_block_before_abort")(*args, **kwargs)


class TestQABlockInterruptFreeze(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.adapter = _make_adapter()

    async def test_freeze_helper_noop_without_plan_mode(self) -> None:
        setattr(self.adapter, "_task_planning_rail", None)
        await _freeze_qa_block_before_abort(self.adapter, "session-1", reason="test")
        getattr(self.adapter, "_qa_block_freeze_rail").freeze_current_qa_sync.assert_not_called()

    async def test_freeze_helper_persists_without_freeze_rail(self) -> None:
        setattr(self.adapter, "_qa_block_freeze_rail", None)
        with patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.persist_checkpoint_for_session",
            new=AsyncMock(),
        ) as mock_persist:
            await _freeze_qa_block_before_abort(
                self.adapter,
                "session-1",
                reason="cancel",
                persist_checkpoint=True,
            )
        mock_persist.assert_awaited_once()

    async def test_freeze_helper_calls_async_freeze(self) -> None:
        session = MagicMock()
        freeze_rail = getattr(self.adapter, "_qa_block_freeze_rail")
        with patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep._resolve_session_for_checkpoint",
            new=AsyncMock(return_value=(session, False)),
        ):
            with patch(
                "jiuwenclaw.agentserver.deep_agent.interface_deep.resolve_context_engine",
                return_value=None,
            ):
                with patch(
                    "jiuwenclaw.agentserver.deep_agent.interface_deep.persist_checkpoint_for_session",
                    new=AsyncMock(),
                ) as mock_persist:
                    await _freeze_qa_block_before_abort(
                        self.adapter,
                        "session-1",
                        reason="supplement",
                        persist_checkpoint=True,
                    )

        freeze_rail.freeze_current_qa_sync.assert_awaited_once_with(
            "session-1",
            agent=getattr(self.adapter, "_instance"),
            session=session,
            status="interrupted",
            persist_mode="async",
        )
        mock_persist.assert_awaited_once()

    async def test_supplement_interrupt_freezes_before_abort(self) -> None:
        request = AgentRequest(
            request_id="req-1",
            channel_id="ch-1",
            session_id="session-1",
            params={"intent": "supplement"},
        )
        freeze_mock = AsyncMock()
        setattr(self.adapter, "_stream_event_rail", SimpleNamespace(abort=MagicMock()))
        setattr(self.adapter, "_instance", SimpleNamespace(abort=AsyncMock()))
        setattr(self.adapter, "_cancel_session_toolkits", AsyncMock())
        setattr(self.adapter, "_abort_active_subagents", AsyncMock(return_value=0))
        setattr(self.adapter, "_clear_session_persisted_interrupt_state", AsyncMock())
        setattr(self.adapter, "_freeze_qa_block_before_abort", freeze_mock)

        response = await self.adapter.process_interrupt(request)

        freeze_mock.assert_awaited_once_with(
            "session-1",
            reason="supplement",
            persist_checkpoint=True,
        )
        self.assertTrue(response.ok)


if __name__ == "__main__":
    unittest.main()
