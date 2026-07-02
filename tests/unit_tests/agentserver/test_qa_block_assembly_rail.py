# coding: utf-8
"""Tests for JiuClawQABlockAssemblyRail invoke-scoped assembly guard."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest
from typing import Any
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.schema import QABlockEntry, QABlockRegistry
from openjiuwen.core.foundation.llm import AssistantMessage, UserMessage
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.rail.base import AgentCallbackContext, InvokeInputs

_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "jiuwenclaw"
    / "agentserver"
    / "deep_agent"
    / "rails"
    / "qa_block_assembly_rail.py"
)
assert _MODULE_PATH.exists(), f"rail module path does not exist: {_MODULE_PATH}"
_spec = importlib.util.spec_from_file_location("qa_block_assembly_rail_under_test", _MODULE_PATH)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
JiuClawQABlockAssemblyRail = _module.JiuClawQABlockAssemblyRail
PENDING_ORPHAN_SALVAGE_KEY: str = getattr(_module, "_PENDING_ORPHAN_SALVAGE_KEY")
ASSEMBLY_COMMITTED_QA_ID_KEY: str = getattr(_module, "_ASSEMBLY_COMMITTED_QA_ID_KEY")


class FakeSession:
    def __init__(self, session_id: str = "session-1") -> None:
        self._session_id = session_id
        self._state: dict = {}

    def get_session_id(self) -> str:
        return self._session_id

    def get_state(self, key: str):
        return self._state.get(key)

    def update_state(self, state_patch: dict) -> None:
        self._state.update(state_patch)


def _registry_with_active_qa(qa_id: str = "qa_003") -> QABlockRegistry:
    return QABlockRegistry(session_id="session-1", current_qa_id=qa_id, next_qa_index=4)


def _frozen_registry(qa_id: str = "qa_002") -> QABlockRegistry:
    entry = QABlockEntry(
        qa_id=qa_id,
        qa_index=2,
        status="completed",
        is_history=True,
        freeze_committed_at="2026-06-01T00:00:00+00:00",
    )
    return QABlockRegistry(
        session_id="session-1",
        current_qa_id=qa_id,
        next_qa_index=3,
        blocks={qa_id: entry},
    )


def _qa_messages(qa_id: str) -> list:
    return [UserMessage(content="in-progress turn", metadata={"qa_id": qa_id})]


def _react_turn_messages(*, user_text: str = "read files", assistant_text: str = "searching") -> list:
    """Production-like ReAct messages: no metadata.qa_id on native turns."""
    return [
        UserMessage(content=user_text),
        AssistantMessage(content=assistant_text),
    ]


def _history_message(qa_id: str, content: str = "hello") -> UserMessage:
    return UserMessage(content=content, metadata={"qa_id": qa_id})


async def _run_first_assembly(
    rail: JiuClawQABlockAssemblyRail,
    session: FakeSession,
    registry: QABlockRegistry,
    *,
    ctx: AgentCallbackContext | None = None,
) -> QABlockRegistry:
    model_ctx = ctx or _make_model_call_ctx(session)
    with patch.object(_module, "load_registry", return_value=registry):
        with patch.object(_module, "maybe_compact_catalog_l1", return_value=registry):
            with patch.object(
                _module,
                "reconcile_orphan_l0_blocks",
                new=AsyncMock(return_value=(registry, [])),
            ):
                with patch.object(_module, "allocate_qa_id", return_value=("qa_003", 3)):
                    with patch.object(_module, "save_registry"):
                        with patch.object(_module, "QABlockLayer") as mock_layer_cls:
                            layer = MagicMock()
                            layer.build_window_qas.return_value = []
                            mock_layer_cls.return_value = layer
                            layer.hydrate_history_into_window = AsyncMock()
                            await rail.before_model_call(model_ctx)
    return registry


def _make_model_call_ctx(
    session: FakeSession,
    *,
    inputs: Any = None,
    messages: list | None = None,
) -> AgentCallbackContext:
    ctx = AgentCallbackContext(
        agent=SimpleNamespace(
            context_engine=MagicMock(),
            system_prompt_builder=MagicMock(language="cn"),
        ),
        inputs=inputs or SimpleNamespace(),
        session=session,
    )
    ctx.context = MagicMock()
    ctx.context.context_id.return_value = "ctx-1"
    ctx.context.get_messages.return_value = messages if messages is not None else []
    ctx.context.get_qa_artifact_manager.return_value = None
    ctx.context.token_counter = None
    ctx.agent.context_engine.get_history_qa_buffer.return_value = {}
    return ctx


def _make_invoke_ctx(session: FakeSession, *, query: str = "plan task") -> AgentCallbackContext:
    return AgentCallbackContext(
        agent=SimpleNamespace(),
        inputs=InvokeInputs(query=query),
        session=session,
    )


class TestQABlockAssemblyRailGuard(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.rail = JiuClawQABlockAssemblyRail(QABlockConfig(enabled=True))
        self.session = FakeSession()

    async def test_skip_reassembly_when_current_qa_active(self) -> None:
        ctx = _make_model_call_ctx(self.session, messages=_react_turn_messages())
        self.session.update_state({ASSEMBLY_COMMITTED_QA_ID_KEY: "qa_003"})

        with patch.object(_module, "load_registry", return_value=_registry_with_active_qa()):
            with patch.object(_module, "allocate_qa_id") as mock_allocate:
                await self.rail.before_model_call(ctx)
                mock_allocate.assert_not_called()

    async def test_before_invoke_defers_orphan_without_freeze_rail(self) -> None:
        ctx = AgentCallbackContext(
            agent=SimpleNamespace(),
            inputs=InvokeInputs(query="new question"),
            session=self.session,
        )
        registry = _registry_with_active_qa("qa_005")

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "save_registry") as mock_save:
                await self.rail.before_invoke(ctx)

        self.assertEqual(registry.current_qa_id, "qa_005")
        self.assertEqual(self.session.get_state(PENDING_ORPHAN_SALVAGE_KEY), "qa_005")
        mock_save.assert_not_called()

    async def test_before_invoke_skips_resume(self) -> None:
        ctx = AgentCallbackContext(
            agent=SimpleNamespace(),
            inputs=InvokeInputs(query=InteractiveInput()),
            session=self.session,
        )
        registry = _registry_with_active_qa()

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "save_registry") as mock_save:
                await self.rail.before_invoke(ctx)

        self.assertEqual(registry.current_qa_id, "qa_003")
        mock_save.assert_not_called()

    async def test_before_invoke_repairs_frozen_pointer(self) -> None:
        ctx = AgentCallbackContext(
            agent=SimpleNamespace(),
            inputs=InvokeInputs(query="new question"),
            session=self.session,
        )
        registry = _frozen_registry()

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "save_registry") as mock_save:
                await self.rail.before_invoke(ctx)

        self.assertIsNone(registry.current_qa_id)
        mock_save.assert_called_once()

    async def test_deferred_salvage_runs_in_before_model_call(self) -> None:
        ctx = _make_model_call_ctx(self.session)
        registry = _registry_with_active_qa("qa_006")
        freeze_rail = SimpleNamespace(
            freeze_current_qa_sync=AsyncMock(),
        )
        salvaged = _frozen_registry("qa_006")
        salvaged.current_qa_id = "qa_006"
        self.rail.attach_freeze_rail(freeze_rail)
        self.session.update_state({PENDING_ORPHAN_SALVAGE_KEY: "qa_006"})

        with patch.object(_module, "load_registry", side_effect=[registry, salvaged, salvaged, salvaged]):
            with patch.object(_module, "maybe_compact_catalog_l1", return_value=salvaged):
                with patch.object(
                    _module,
                    "reconcile_orphan_l0_blocks",
                    new=AsyncMock(return_value=(salvaged, [])),
                ):
                    with patch.object(_module, "allocate_qa_id", return_value=("qa_010", 10)) as mock_allocate:
                        with patch.object(_module, "save_registry"):
                            with patch.object(_module, "QABlockLayer") as mock_layer_cls:
                                layer = MagicMock()
                                layer.build_window_qas.return_value = []
                                mock_layer_cls.return_value = layer
                                layer.hydrate_history_into_window = AsyncMock()
                                await self.rail.before_model_call(ctx)

        freeze_rail.freeze_current_qa_sync.assert_awaited_once()
        self.assertIsNone(self.session.get_state(PENDING_ORPHAN_SALVAGE_KEY))
        mock_allocate.assert_called_once()

    async def test_deferred_salvage_without_freeze_rail_clears_then_assembles(self) -> None:
        ctx = _make_model_call_ctx(self.session)
        registry = _registry_with_active_qa("qa_007")
        self.session.update_state({PENDING_ORPHAN_SALVAGE_KEY: "qa_007"})

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "maybe_compact_catalog_l1", return_value=registry):
                with patch.object(
                    _module,
                    "reconcile_orphan_l0_blocks",
                    new=AsyncMock(return_value=(registry, [])),
                ):
                    with patch.object(_module, "allocate_qa_id", return_value=("qa_008", 8)) as mock_allocate:
                        with patch.object(_module, "save_registry"):
                            with patch.object(_module, "QABlockLayer") as mock_layer_cls:
                                layer = MagicMock()
                                layer.build_window_qas.return_value = []
                                mock_layer_cls.return_value = layer
                                layer.hydrate_history_into_window = AsyncMock()
                                await self.rail.before_model_call(ctx)

        mock_allocate.assert_called_once()
        self.assertEqual(registry.current_qa_id, "qa_008")

    async def test_task_loop_second_iteration_skips_reassembly(self) -> None:
        registry = QABlockRegistry(session_id="session-1", current_qa_id=None, next_qa_index=3)
        invoke_ctx = _make_invoke_ctx(self.session)
        model_ctx = _make_model_call_ctx(self.session)

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "save_registry") as mock_invoke_save:
                await self.rail.before_invoke(invoke_ctx)

        self.assertIsNone(self.session.get_state(PENDING_ORPHAN_SALVAGE_KEY))
        mock_invoke_save.assert_not_called()

        await _run_first_assembly(self.rail, self.session, registry, ctx=model_ctx)

        self.assertEqual(registry.current_qa_id, "qa_003")
        self.assertEqual(self.session.get_state(ASSEMBLY_COMMITTED_QA_ID_KEY), "qa_003")
        self.assertNotIn("qa_003", registry.blocks)
        self.assertIsNone(self.session.get_state(PENDING_ORPHAN_SALVAGE_KEY))

        loop_ctx = _make_model_call_ctx(self.session, messages=_react_turn_messages())
        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "allocate_qa_id") as mock_allocate:
                await self.rail.before_model_call(loop_ctx)
                mock_allocate.assert_not_called()

        self.assertIsNone(self.session.get_state(PENDING_ORPHAN_SALVAGE_KEY))

    async def test_react_second_iteration_skips_without_qa_metadata(self) -> None:
        """Regression for case_3: ReAct tool messages lack metadata.qa_id."""
        registry = QABlockRegistry(session_id="session-1", current_qa_id=None, next_qa_index=2)
        await _run_first_assembly(
            self.rail,
            self.session,
            registry,
            ctx=_make_model_call_ctx(self.session),
        )
        self.assertEqual(registry.current_qa_id, "qa_003")

        second_iter_ctx = _make_model_call_ctx(
            self.session,
            messages=_react_turn_messages(user_text="read desktop chapters"),
        )
        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "allocate_qa_id") as mock_allocate:
                await self.rail.before_model_call(second_iter_ctx)
                mock_allocate.assert_not_called()

        self.assertEqual(registry.current_qa_id, "qa_003")

    async def test_task_loop_second_outer_round_skips_with_committed_marker(self) -> None:
        """Regression for case_52: new react invoke, empty context, same user turn."""
        registry = _registry_with_active_qa("qa_002")
        self.session.update_state({ASSEMBLY_COMMITTED_QA_ID_KEY: "qa_002"})
        round2_ctx = _make_model_call_ctx(self.session, messages=[])

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "allocate_qa_id") as mock_allocate:
                await self.rail.before_model_call(round2_ctx)
                mock_allocate.assert_not_called()

    async def test_hydrated_history_messages_do_not_trigger_stale_reassembly(self) -> None:
        registry = _registry_with_active_qa("qa_003")
        self.session.update_state({ASSEMBLY_COMMITTED_QA_ID_KEY: "qa_003"})
        ctx = _make_model_call_ctx(
            self.session,
            messages=[_history_message("qa_001", "hello")],
        )

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "allocate_qa_id") as mock_allocate:
                await self.rail.before_model_call(ctx)
                mock_allocate.assert_not_called()

    async def test_before_model_call_treats_none_messages_as_empty_for_stale_guard(self) -> None:
        registry = _registry_with_active_qa("qa_010")
        ctx = _make_model_call_ctx(self.session)
        ctx.context.get_messages.return_value = None
        rail = JiuClawQABlockAssemblyRail(QABlockConfig(enabled=True, selector_enabled=False))

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "maybe_compact_catalog_l1", return_value=registry):
                with patch.object(
                    _module,
                    "reconcile_orphan_l0_blocks",
                    new=AsyncMock(return_value=(registry, [])),
                ):
                    with patch.object(_module, "allocate_qa_id", return_value=("qa_011", 11)) as mock_allocate:
                        with patch.object(_module, "save_registry"):
                            with patch.object(_module, "QABlockLayer") as mock_layer_cls:
                                layer = MagicMock()
                                layer.build_window_qas.return_value = []
                                mock_layer_cls.return_value = layer
                                layer.hydrate_history_into_window = AsyncMock()
                                await rail.before_model_call(ctx)

        mock_allocate.assert_called_once()
        self.assertEqual(registry.current_qa_id, "qa_011")

    async def test_stale_pointer_without_context_messages_reassembles(self) -> None:
        registry = _registry_with_active_qa("qa_010")
        ctx = _make_model_call_ctx(self.session, messages=[])

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "maybe_compact_catalog_l1", return_value=registry):
                with patch.object(
                    _module,
                    "reconcile_orphan_l0_blocks",
                    new=AsyncMock(return_value=(registry, [])),
                ):
                    with patch.object(_module, "allocate_qa_id", return_value=("qa_011", 11)) as mock_allocate:
                        with patch.object(_module, "save_registry"):
                            with patch.object(_module, "QABlockLayer") as mock_layer_cls:
                                layer = MagicMock()
                                layer.build_window_qas.return_value = []
                                mock_layer_cls.return_value = layer
                                layer.hydrate_history_into_window = AsyncMock()
                                await self.rail.before_model_call(ctx)

        mock_allocate.assert_called_once()
        self.assertEqual(registry.current_qa_id, "qa_011")

    async def test_resume_empty_context_clears_stale_pointer_and_assembles(self) -> None:
        registry = _registry_with_active_qa("qa_008")
        ctx = _make_model_call_ctx(
            self.session,
            inputs=InvokeInputs(query=InteractiveInput()),
            messages=[],
        )

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "maybe_compact_catalog_l1", return_value=registry):
                with patch.object(
                    _module,
                    "reconcile_orphan_l0_blocks",
                    new=AsyncMock(return_value=(registry, [])),
                ):
                    with patch.object(_module, "allocate_qa_id", return_value=("qa_009", 9)):
                        with patch.object(_module, "save_registry"):
                            with patch.object(_module, "QABlockLayer") as mock_layer_cls:
                                layer = MagicMock()
                                layer.build_window_qas.return_value = []
                                mock_layer_cls.return_value = layer
                                layer.hydrate_history_into_window = AsyncMock()
                                await self.rail.before_model_call(ctx)

        self.assertEqual(registry.current_qa_id, "qa_009")

    async def test_before_model_call_repairs_frozen_pointer_then_assembles(self) -> None:
        ctx = _make_model_call_ctx(self.session)
        registry = _frozen_registry("qa_004")

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "maybe_compact_catalog_l1", return_value=registry):
                with patch.object(
                    _module,
                    "reconcile_orphan_l0_blocks",
                    new=AsyncMock(return_value=(registry, [])),
                ):
                    with patch.object(_module, "allocate_qa_id", return_value=("qa_005", 5)) as mock_allocate:
                        with patch.object(_module, "save_registry"):
                            with patch.object(_module, "QABlockLayer") as mock_layer_cls:
                                layer = MagicMock()
                                layer.build_window_qas.return_value = []
                                mock_layer_cls.return_value = layer
                                layer.hydrate_history_into_window = AsyncMock()
                                await self.rail.before_model_call(ctx)

        mock_allocate.assert_called_once()
        self.assertEqual(registry.current_qa_id, "qa_005")


if __name__ == "__main__":
    unittest.main()
