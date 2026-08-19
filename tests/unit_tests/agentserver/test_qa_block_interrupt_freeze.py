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

    async def test_freeze_helper_uses_async_for_reused_session(self) -> None:
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

    async def test_freeze_helper_uses_sync_before_closing_owned_session(self) -> None:
        calls: list[str] = []
        session = MagicMock()
        session.pre_run = AsyncMock(side_effect=lambda *_a, **_k: calls.append("pre_run"))
        session.post_run = AsyncMock(side_effect=lambda *_a, **_k: calls.append("post_run"))
        freeze_rail = getattr(self.adapter, "_qa_block_freeze_rail")
        freeze_rail.freeze_current_qa_sync = AsyncMock(
            side_effect=lambda *_a, **_k: calls.append("freeze")
        )

        with patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep._resolve_session_for_checkpoint",
            new=AsyncMock(return_value=(session, True)),
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.resolve_context_engine",
            return_value=None,
        ):
            await _freeze_qa_block_before_abort(
                self.adapter,
                "session-1",
                reason="cancel",
            )

        freeze_rail.freeze_current_qa_sync.assert_awaited_once_with(
            "session-1",
            agent=getattr(self.adapter, "_instance"),
            session=session,
            status="interrupted",
            persist_mode="sync",
        )
        session.pre_run.assert_awaited_once_with(inputs=None)
        session.post_run.assert_awaited_once_with()
        self.assertEqual(calls, ["pre_run", "freeze", "post_run"])

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


    async def test_freeze_binds_env_overlay_for_llm_auth(self) -> None:
        """Regression: _freeze_qa_block_before_abort must bind env overlay so that
        freeze_persist (async background task) inherits default_headers for LLM
        summarization auth.  Without this, read_default_headers() returns None
        → 401 APIG.0303.
        """
        with patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.bind_agent_env_ns",
            return_value="ns_token",
        ) as mock_bind_ns, patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_effective_env_overlay",
            return_value={"KEY": "val"},
        ) as mock_build_overlay, patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.bind_task_env_overlay",
            return_value="overlay_token",
        ) as mock_bind_overlay, patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.reset_task_env_overlay",
        ) as mock_reset_overlay, patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.reset_agent_env_ns",
        ) as mock_reset_ns, patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep._resolve_session_for_checkpoint",
            new=AsyncMock(return_value=(MagicMock(), False)),
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.resolve_context_engine",
            return_value=None,
        ):
            await _freeze_qa_block_before_abort(
                self.adapter, "session-1", reason="cancel",
            )

        mock_bind_ns.assert_called_once()
        mock_build_overlay.assert_called_once()
        mock_bind_overlay.assert_called_once()
        mock_reset_overlay.assert_called_once_with("overlay_token")
        mock_reset_ns.assert_called_once_with("ns_token")

    async def test_freeze_swallows_overlay_bind_failure(self) -> None:
        """Overlay bind failure must skip freeze and not raise (abort must proceed)."""
        freeze_rail = getattr(self.adapter, "_qa_block_freeze_rail")
        with patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.bind_agent_env_ns",
            return_value="ns_token",
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.build_effective_env_overlay",
            return_value={"KEY": "val"},
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.bind_task_env_overlay",
            side_effect=RuntimeError("bind failed"),
        ), patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.reset_task_env_overlay",
        ) as mock_reset_overlay, patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.reset_agent_env_ns",
        ) as mock_reset_ns, patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep._resolve_session_for_checkpoint",
            new=AsyncMock(),
        ) as mock_resolve:
            await _freeze_qa_block_before_abort(
                self.adapter, "session-1", reason="cancel",
            )

        # ns_token was bound before the failure, so it must be reset
        mock_reset_ns.assert_called_once_with("ns_token")
        # overlay_token was never bound, so it must NOT be reset
        mock_reset_overlay.assert_not_called()
        # Freeze must be skipped when overlay bind fails
        freeze_rail.freeze_current_qa_sync.assert_not_called()
        mock_resolve.assert_not_awaited()

    async def test_process_interrupt_aborts_even_if_freeze_raises(self) -> None:
        """Defense in depth: interrupt abort continues if freeze helper raises."""
        request = AgentRequest(
            request_id="req-1",
            channel_id="ch-1",
            session_id="session-1",
            params={"intent": "cancel"},
        )
        abort_stream = MagicMock()
        abort_instance = AsyncMock()
        cancel_toolkits = AsyncMock()
        setattr(self.adapter, "_stream_event_rail", SimpleNamespace(abort=abort_stream))
        setattr(self.adapter, "_instance", SimpleNamespace(abort=abort_instance))
        setattr(self.adapter, "_cancel_session_toolkits", cancel_toolkits)
        setattr(self.adapter, "_abort_active_subagents", AsyncMock(return_value=0))
        setattr(self.adapter, "_clear_session_persisted_interrupt_state", AsyncMock())
        setattr(
            self.adapter,
            "_freeze_qa_block_before_abort",
            AsyncMock(side_effect=RuntimeError("freeze boom")),
        )

        response = await self.adapter.process_interrupt(request)

        self.assertTrue(response.ok)
        abort_stream.assert_called_once()
        abort_instance.assert_awaited_once()
        cancel_toolkits.assert_awaited_once()
        getattr(self.adapter, "_freeze_qa_block_before_abort").assert_awaited_once()


if __name__ == "__main__":
    unittest.main()
