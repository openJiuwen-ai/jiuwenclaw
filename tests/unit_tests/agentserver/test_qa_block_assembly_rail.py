# coding: utf-8
"""Tests for JiuClawQABlockAssemblyRail invoke-scoped assembly guard and task continuation fallback."""

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
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
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
INTERRUPT_RESUME_TURN_KEY: str = getattr(_module, "_INTERRUPT_RESUME_TURN_KEY")
_is_task_continuation = _module._is_task_continuation
_last_n_history_qa_ids = _module._last_n_history_qa_ids


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


def _make_ctx_for_continuation(*, inputs: Any = None, **extra: object) -> SimpleNamespace:
    ctx = SimpleNamespace()
    ctx.extra = dict(extra)
    ctx.inputs = inputs if inputs is not None else SimpleNamespace()
    return ctx


def _make_registry_for_continuation(*qa_ids: str, history_indices: set[int] | None = None) -> QABlockRegistry:
    if history_indices is None:
        history_indices = set()
    registry = QABlockRegistry(session_id="test_session")
    for idx, qa_id in enumerate(qa_ids):
        entry = QABlockEntry(
            qa_id=qa_id,
            qa_index=idx + 1,
            status="completed",
            is_history=(idx + 1) in history_indices,
        )
        registry.blocks[qa_id] = entry
    return registry


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

    async def test_before_invoke_repairs_empty_orphan_without_salvage(self) -> None:
        ctx = _make_model_call_ctx(self.session, messages=[])
        ctx.inputs = InvokeInputs(query="new question")
        registry = _registry_with_active_qa("qa_005")

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "save_registry") as mock_save:
                with patch.object(_module, "post_agent_execute_for_session", new=AsyncMock()):
                    await self.rail.before_invoke(ctx)

        self.assertIsNone(registry.current_qa_id)
        self.assertIsNone(self.session.get_state(PENDING_ORPHAN_SALVAGE_KEY))
        mock_save.assert_called_once()

    async def test_before_invoke_defers_orphan_when_context_unavailable(self) -> None:
        """before_invoke often has no context; must not skip salvage blindly."""
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

    async def test_before_invoke_defers_orphan_with_native_work(self) -> None:
        ctx = _make_model_call_ctx(self.session, messages=_qa_messages("qa_005"))
        ctx.inputs = InvokeInputs(query="new question")
        registry = _registry_with_active_qa("qa_005")
        entry = QABlockEntry(
            qa_id="qa_005",
            qa_index=5,
            status="interrupted",
            message_count=2,
        )
        registry.blocks["qa_005"] = entry

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "save_registry") as mock_save:
                await self.rail.before_invoke(ctx)

        self.assertEqual(registry.current_qa_id, "qa_005")
        self.assertEqual(self.session.get_state(PENDING_ORPHAN_SALVAGE_KEY), "qa_005")
        mock_save.assert_not_called()

    async def test_before_invoke_defers_orphan_without_freeze_rail(self) -> None:
        ctx = AgentCallbackContext(
            agent=SimpleNamespace(),
            inputs=InvokeInputs(query="new question"),
            session=self.session,
        )
        registry = _registry_with_active_qa("qa_005")
        entry = QABlockEntry(
            qa_id="qa_005",
            qa_index=5,
            status="interrupted",
            message_count=1,
        )
        registry.blocks["qa_005"] = entry

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
        ctx.agent.context_engine.save_contexts = AsyncMock()

        with patch.object(
            _module,
            "load_registry",
            side_effect=[registry, registry, salvaged, salvaged, salvaged],
        ):
            with patch.object(_module, "maybe_compact_catalog_l1", return_value=salvaged):
                with patch.object(
                    _module,
                    "reconcile_orphan_l0_blocks",
                    new=AsyncMock(return_value=(salvaged, [])),
                ):
                    with patch.object(_module, "allocate_qa_id", return_value=("qa_010", 10)) as mock_allocate:
                        with patch.object(_module, "save_registry"):
                            with patch.object(
                                _module, "post_agent_execute_for_session", new=AsyncMock()
                            ):
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

    async def test_interrupt_resume_keeps_unfrozen_qa_without_allocate(self) -> None:
        registry = _registry_with_active_qa("qa_003")
        registry.blocks["qa_003"] = QABlockEntry(
            qa_id="qa_003",
            qa_index=3,
            status="interrupted",
        )
        self.session.update_state({INTERRUPT_RESUME_TURN_KEY: True})
        ctx = _make_model_call_ctx(self.session, messages=[])

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "allocate_qa_id") as mock_allocate:
                await self.rail.before_model_call(ctx)
                mock_allocate.assert_not_called()

        self.assertEqual(registry.current_qa_id, "qa_003")

    async def test_interrupt_resume_stale_pointer_with_query_reallocates(self) -> None:
        """Resume turn + no commit/native work + non-empty query → do not keep stale QA."""
        registry = _registry_with_active_qa("qa_003")
        registry.blocks["qa_003"] = QABlockEntry(
            qa_id="qa_003",
            qa_index=3,
            status="interrupted",
        )
        self.session.update_state({INTERRUPT_RESUME_TURN_KEY: True})
        # Only hydrated history for another QA → no active work for qa_003, but has user text.
        ctx = _make_model_call_ctx(
            self.session,
            messages=[_history_message("qa_001", "follow up question")],
        )
        rail = JiuClawQABlockAssemblyRail(QABlockConfig(enabled=True, selector_enabled=False))

        with patch.object(_module, "load_registry", return_value=registry):
            with patch.object(_module, "maybe_compact_catalog_l1", return_value=registry):
                with patch.object(
                    _module,
                    "reconcile_orphan_l0_blocks",
                    new=AsyncMock(return_value=(registry, [])),
                ):
                    with patch.object(_module, "allocate_qa_id", return_value=("qa_004", 4)) as mock_allocate:
                        with patch.object(_module, "save_registry"):
                            with patch.object(_module, "QABlockLayer") as mock_layer_cls:
                                layer = MagicMock()
                                layer.build_window_qas.return_value = []
                                mock_layer_cls.return_value = layer
                                layer.hydrate_history_into_window = AsyncMock()
                                await rail.before_model_call(ctx)

        mock_allocate.assert_called_once()
        self.assertEqual(registry.current_qa_id, "qa_004")

    async def test_orphan_salvage_preserves_current_round_user(self) -> None:
        ctx = _make_model_call_ctx(
            self.session,
            messages=[UserMessage(content="new user turn")],
        )
        registry = _registry_with_active_qa("qa_006")
        freeze_rail = SimpleNamespace(freeze_current_qa_sync=AsyncMock())
        salvaged = _frozen_registry("qa_006")
        salvaged.current_qa_id = None
        self.rail.attach_freeze_rail(freeze_rail)
        self.session.update_state({PENDING_ORPHAN_SALVAGE_KEY: "qa_006"})
        set_messages = MagicMock()
        ctx.context.set_messages = set_messages
        ctx.context.get_messages.return_value = [UserMessage(content="new user turn")]
        ctx.agent.context_engine.save_contexts = AsyncMock()
        ctx.agent.context_engine.get_history_qa_buffer.return_value = {}

        with patch.object(
            _module,
            "load_registry",
            side_effect=[registry, registry, salvaged, salvaged, salvaged],
        ):
            with patch.object(_module, "maybe_compact_catalog_l1", return_value=salvaged):
                with patch.object(
                    _module,
                    "reconcile_orphan_l0_blocks",
                    new=AsyncMock(return_value=(salvaged, [])),
                ):
                    with patch.object(_module, "allocate_qa_id", return_value=("qa_010", 10)):
                        with patch.object(_module, "save_registry"):
                            with patch.object(
                                _module, "post_agent_execute_for_session", new=AsyncMock()
                            ):
                                with patch.object(_module, "QABlockLayer") as mock_layer_cls:
                                    layer = MagicMock()
                                    layer.build_window_qas.return_value = []
                                    mock_layer_cls.return_value = layer
                                    layer.hydrate_history_into_window = AsyncMock()
                                    await self.rail.before_model_call(ctx)

        freeze_rail.freeze_current_qa_sync.assert_awaited_once()
        self.assertEqual(
            freeze_rail.freeze_current_qa_sync.await_args.kwargs.get("persist_context"),
            False,
        )
        ctx.agent.context_engine.save_contexts.assert_awaited()
        restored_calls = [
            call
            for call in set_messages.call_args_list
            if call.args and isinstance(call.args[0], list) and call.args[0]
        ]
        self.assertTrue(restored_calls)
        restored = restored_calls[-1].args[0]
        self.assertEqual(len(restored), 1)
        self.assertEqual(getattr(restored[0], "content", ""), "new user turn")

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


class TestIsTaskContinuation(unittest.TestCase):
    def test_interactive_input_in_extra_returns_true(self) -> None:
        ctx = _make_ctx_for_continuation(**{RESUME_USER_INPUT_KEY: InteractiveInput()})
        result = _is_task_continuation(ctx, "some query text")
        self.assertTrue(result)

    def test_interactive_input_with_user_inputs_returns_true(self) -> None:
        input_obj = InteractiveInput()
        input_obj.update("call_abc123", {"approved": True, "feedback": ""})
        ctx = _make_ctx_for_continuation(**{RESUME_USER_INPUT_KEY: input_obj})
        result = _is_task_continuation(ctx, "any query")
        self.assertTrue(result)

    def test_non_empty_next_query_no_resume_input_returns_false(self) -> None:
        ctx = _make_ctx_for_continuation()
        result = _is_task_continuation(ctx, "帮我整理会议纪要")
        self.assertFalse(result)

    def test_empty_next_query_no_resume_returns_false(self) -> None:
        ctx = _make_ctx_for_continuation()
        result = _is_task_continuation(ctx, "")
        self.assertFalse(result)

    def test_whitespace_only_next_query_no_resume_returns_false(self) -> None:
        ctx = _make_ctx_for_continuation()
        result = _is_task_continuation(ctx, "   \n\t  ")
        self.assertFalse(result)

    def test_empty_next_query_with_resume_input_returns_true(self) -> None:
        ctx = _make_ctx_for_continuation(
            inputs=InvokeInputs(query=InteractiveInput()),
            **{RESUME_USER_INPUT_KEY: "some_string_not_interactive"},
        )
        result = _is_task_continuation(ctx, "")
        self.assertTrue(result)

    def test_whitespace_query_with_resume_input_returns_true(self) -> None:
        ctx = _make_ctx_for_continuation(
            inputs=InvokeInputs(query=InteractiveInput()),
            **{RESUME_USER_INPUT_KEY: "some_string_not_interactive"},
        )
        result = _is_task_continuation(ctx, "   \n\t  ")
        self.assertTrue(result)

    def test_empty_query_with_interrupt_resume_session_returns_true(self) -> None:
        session = FakeSession()
        session.update_state({INTERRUPT_RESUME_TURN_KEY: True})
        ctx = _make_ctx_for_continuation()
        self.assertTrue(_is_task_continuation(ctx, "", session))

    def test_non_empty_query_with_interrupt_resume_session_returns_false(self) -> None:
        session = FakeSession()
        session.update_state({INTERRUPT_RESUME_TURN_KEY: True})
        ctx = _make_ctx_for_continuation()
        self.assertFalse(_is_task_continuation(ctx, "follow up", session))

    def test_no_resume_key_in_extra_with_empty_query_returns_false(self) -> None:
        ctx = _make_ctx_for_continuation()
        self.assertIsNone(ctx.extra.get(RESUME_USER_INPUT_KEY))
        result = _is_task_continuation(ctx, "")
        self.assertFalse(result)

    def test_resume_key_is_not_interactive_input_with_empty_query_returns_true(self) -> None:
        ctx = _make_ctx_for_continuation(
            inputs=InvokeInputs(query=InteractiveInput()),
            **{RESUME_USER_INPUT_KEY: "some_string_not_interactive"},
        )
        result = _is_task_continuation(ctx, "")
        self.assertTrue(result)

    def test_resume_key_is_not_interactive_input_with_non_empty_query_returns_false(self) -> None:
        ctx = _make_ctx_for_continuation(**{RESUME_USER_INPUT_KEY: "some_string"})
        result = _is_task_continuation(ctx, "用户的问题")
        self.assertFalse(result)


class TestLastNHistoryQaIds(unittest.TestCase):
    def test_empty_registry_returns_empty(self) -> None:
        registry = _make_registry_for_continuation()
        result = _last_n_history_qa_ids(registry, n=3)
        self.assertEqual(result, [])

    def test_no_history_entries_returns_empty(self) -> None:
        registry = _make_registry_for_continuation("qa_001", "qa_002")
        result = _last_n_history_qa_ids(registry, n=3)
        self.assertEqual(result, [])

    def test_n_zero_returns_empty(self) -> None:
        registry = _make_registry_for_continuation("qa_001", "qa_002", history_indices={1, 2})
        result = _last_n_history_qa_ids(registry, n=0)
        self.assertEqual(result, [])

    def test_n_negative_returns_empty(self) -> None:
        registry = _make_registry_for_continuation("qa_001", "qa_002", history_indices={1, 2})
        result = _last_n_history_qa_ids(registry, n=-1)
        self.assertEqual(result, [])

    def test_single_history_entry_n1(self) -> None:
        registry = _make_registry_for_continuation("qa_001", history_indices={1})
        result = _last_n_history_qa_ids(registry, n=1)
        self.assertEqual(result, ["qa_001"])

    def test_single_history_entry_n3_returns_one(self) -> None:
        registry = _make_registry_for_continuation("qa_001", history_indices={1})
        result = _last_n_history_qa_ids(registry, n=3)
        self.assertEqual(result, ["qa_001"])

    def test_multiple_history_entries_n1_returns_last(self) -> None:
        registry = _make_registry_for_continuation("qa_001", "qa_002", "qa_003", history_indices={1, 2, 3})
        result = _last_n_history_qa_ids(registry, n=1)
        self.assertEqual(result, ["qa_003"])

    def test_multiple_history_entries_n2_returns_last_two(self) -> None:
        registry = _make_registry_for_continuation("qa_001", "qa_002", "qa_003", history_indices={1, 2, 3})
        result = _last_n_history_qa_ids(registry, n=2)
        self.assertEqual(result, ["qa_002", "qa_003"])

    def test_mixed_history_and_current_returns_only_history(self) -> None:
        registry = _make_registry_for_continuation("qa_001", "qa_002", "qa_003", history_indices={1, 3})
        result = _last_n_history_qa_ids(registry, n=2)
        self.assertEqual(result, ["qa_001", "qa_003"])

    def test_n_exceeds_history_count_returns_all(self) -> None:
        registry = _make_registry_for_continuation("qa_001", "qa_002", history_indices={1, 2})
        result = _last_n_history_qa_ids(registry, n=10)
        self.assertEqual(result, ["qa_001", "qa_002"])

    def test_preserves_index_order_even_if_insertion_order_differs(self) -> None:
        registry = QABlockRegistry(session_id="test_session")
        entry_3 = QABlockEntry(qa_id="qa_003", qa_index=3, status="completed", is_history=True)
        entry_1 = QABlockEntry(qa_id="qa_001", qa_index=1, status="completed", is_history=True)
        entry_2 = QABlockEntry(qa_id="qa_002", qa_index=2, status="completed", is_history=True)
        registry.blocks["qa_003"] = entry_3
        registry.blocks["qa_001"] = entry_1
        registry.blocks["qa_002"] = entry_2
        result = _last_n_history_qa_ids(registry, n=3)
        self.assertEqual(result, ["qa_001", "qa_002", "qa_003"])


class TestTaskContinuationFallbackIntegration(unittest.TestCase):
    def test_bug_scenario_permission_resume_empty_query(self) -> None:
        ctx = _make_ctx_for_continuation(**{RESUME_USER_INPUT_KEY: InteractiveInput()})
        next_query = ""
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertTrue(is_continuation)

        registry = _make_registry_for_continuation("qa_001", history_indices={1})
        fallback_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(fallback_ids, ["qa_001"])

        selected_qa_ids: list[str] = []
        if not selected_qa_ids and is_continuation:
            selected_qa_ids = fallback_ids
        self.assertEqual(selected_qa_ids, ["qa_001"])

    def test_bug_scenario_permission_resume_with_empty_query_no_history(self) -> None:
        ctx = _make_ctx_for_continuation(**{RESUME_USER_INPUT_KEY: InteractiveInput()})
        next_query = ""
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertTrue(is_continuation)

        registry = _make_registry_for_continuation()
        fallback_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(fallback_ids, [])

        selected_qa_ids: list[str] = []
        if not selected_qa_ids and is_continuation:
            selected_qa_ids = fallback_ids
        self.assertEqual(selected_qa_ids, [])

    def test_normal_user_query_no_fallback(self) -> None:
        ctx = _make_ctx_for_continuation()
        next_query = "帮我整理会议纪要"
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertFalse(is_continuation)

        registry = _make_registry_for_continuation("qa_001", history_indices={1})
        selected_qa_ids: list[str] = ["qa_001"]

        if not selected_qa_ids and is_continuation:
            selected_qa_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(selected_qa_ids, ["qa_001"])

    def test_normal_user_query_selector_empty_no_fallback(self) -> None:
        ctx = _make_ctx_for_continuation()
        next_query = "今天天气怎么样"
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertFalse(is_continuation)

        registry = _make_registry_for_continuation("qa_001", history_indices={1})
        selected_qa_ids: list[str] = []

        if not selected_qa_ids and is_continuation:
            selected_qa_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(selected_qa_ids, [])

    def test_selector_exception_with_continuation_triggers_fallback(self) -> None:
        ctx = _make_ctx_for_continuation(**{RESUME_USER_INPUT_KEY: InteractiveInput()})
        next_query = ""
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertTrue(is_continuation)

        registry = _make_registry_for_continuation("qa_001", "qa_002", history_indices={1, 2})
        selected_qa_ids: list[str] = []

        if not selected_qa_ids and is_continuation:
            selected_qa_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(selected_qa_ids, ["qa_001", "qa_002"])

    def test_empty_query_without_resume_no_fallback(self) -> None:
        ctx = _make_ctx_for_continuation()
        next_query = ""
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertFalse(is_continuation)

        registry = _make_registry_for_continuation("qa_001", history_indices={1})
        selected_qa_ids: list[str] = []

        if not selected_qa_ids and is_continuation:
            selected_qa_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(selected_qa_ids, [])


if __name__ == "__main__":
    unittest.main()
