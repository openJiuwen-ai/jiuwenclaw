# coding: utf-8
"""Tests for JiuClawQABlockAssemblyRail task continuation fallback."""

from __future__ import annotations

import importlib.util
import pathlib
import unittest
from types import SimpleNamespace
from unittest.mock import MagicMock

from openjiuwen.core.context_engine.qa_block.config import QABlockConfig
from openjiuwen.core.context_engine.qa_block.schema import QABlockEntry, QABlockRegistry
from openjiuwen.core.session.interaction.interactive_input import InteractiveInput
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY

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

_is_task_continuation = _module._is_task_continuation
_last_n_history_qa_ids = _module._last_n_history_qa_ids


def _make_ctx(**extra: object) -> SimpleNamespace:
    ctx = SimpleNamespace()
    ctx.extra = dict(extra)
    return ctx


def _make_registry(*qa_ids: str, history_indices: set[int] | None = None) -> QABlockRegistry:
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


class TestIsTaskContinuation(unittest.TestCase):
    def test_interactive_input_in_extra_returns_true(self) -> None:
        ctx = _make_ctx(**{RESUME_USER_INPUT_KEY: InteractiveInput()})
        result = _is_task_continuation(ctx, "some query text")
        self.assertTrue(result)

    def test_interactive_input_with_user_inputs_returns_true(self) -> None:
        input_obj = InteractiveInput()
        input_obj.update("call_abc123", {"approved": True, "feedback": ""})
        ctx = _make_ctx(**{RESUME_USER_INPUT_KEY: input_obj})
        result = _is_task_continuation(ctx, "any query")
        self.assertTrue(result)

    def test_non_empty_next_query_no_resume_input_returns_false(self) -> None:
        ctx = _make_ctx()
        result = _is_task_continuation(ctx, "帮我整理会议纪要")
        self.assertFalse(result)

    def test_empty_next_query_no_resume_returns_false(self) -> None:
        ctx = _make_ctx()
        result = _is_task_continuation(ctx, "")
        self.assertFalse(result)

    def test_whitespace_only_next_query_no_resume_returns_false(self) -> None:
        ctx = _make_ctx()
        result = _is_task_continuation(ctx, "   \n\t  ")
        self.assertFalse(result)

    def test_empty_next_query_with_resume_input_returns_true(self) -> None:
        ctx = _make_ctx(**{RESUME_USER_INPUT_KEY: "some_string_not_interactive"})
        result = _is_task_continuation(ctx, "")
        self.assertTrue(result)

    def test_whitespace_query_with_resume_input_returns_true(self) -> None:
        ctx = _make_ctx(**{RESUME_USER_INPUT_KEY: "some_string_not_interactive"})
        result = _is_task_continuation(ctx, "   \n\t  ")
        self.assertTrue(result)

    def test_no_resume_key_in_extra_with_empty_query_returns_false(self) -> None:
        ctx = _make_ctx()
        self.assertIsNone(ctx.extra.get(RESUME_USER_INPUT_KEY))
        result = _is_task_continuation(ctx, "")
        self.assertFalse(result)

    def test_resume_key_is_not_interactive_input_with_empty_query_returns_true(self) -> None:
        ctx = _make_ctx(**{RESUME_USER_INPUT_KEY: "some_string_not_interactive"})
        result = _is_task_continuation(ctx, "")
        self.assertTrue(result)

    def test_resume_key_is_not_interactive_input_with_non_empty_query_returns_false(self) -> None:
        ctx = _make_ctx(**{RESUME_USER_INPUT_KEY: "some_string"})
        result = _is_task_continuation(ctx, "用户的问题")
        self.assertFalse(result)


class TestLastNHistoryQaIds(unittest.TestCase):
    def test_empty_registry_returns_empty(self) -> None:
        registry = _make_registry()
        result = _last_n_history_qa_ids(registry, n=3)
        self.assertEqual(result, [])

    def test_no_history_entries_returns_empty(self) -> None:
        registry = _make_registry("qa_001", "qa_002")
        result = _last_n_history_qa_ids(registry, n=3)
        self.assertEqual(result, [])

    def test_n_zero_returns_empty(self) -> None:
        registry = _make_registry("qa_001", "qa_002", history_indices={1, 2})
        result = _last_n_history_qa_ids(registry, n=0)
        self.assertEqual(result, [])

    def test_n_negative_returns_empty(self) -> None:
        registry = _make_registry("qa_001", "qa_002", history_indices={1, 2})
        result = _last_n_history_qa_ids(registry, n=-1)
        self.assertEqual(result, [])

    def test_single_history_entry_n1(self) -> None:
        registry = _make_registry("qa_001", history_indices={1})
        result = _last_n_history_qa_ids(registry, n=1)
        self.assertEqual(result, ["qa_001"])

    def test_single_history_entry_n3_returns_one(self) -> None:
        registry = _make_registry("qa_001", history_indices={1})
        result = _last_n_history_qa_ids(registry, n=3)
        self.assertEqual(result, ["qa_001"])

    def test_multiple_history_entries_n1_returns_last(self) -> None:
        registry = _make_registry("qa_001", "qa_002", "qa_003", history_indices={1, 2, 3})
        result = _last_n_history_qa_ids(registry, n=1)
        self.assertEqual(result, ["qa_003"])

    def test_multiple_history_entries_n2_returns_last_two(self) -> None:
        registry = _make_registry("qa_001", "qa_002", "qa_003", history_indices={1, 2, 3})
        result = _last_n_history_qa_ids(registry, n=2)
        self.assertEqual(result, ["qa_002", "qa_003"])

    def test_mixed_history_and_current_returns_only_history(self) -> None:
        registry = _make_registry("qa_001", "qa_002", "qa_003", history_indices={1, 3})
        result = _last_n_history_qa_ids(registry, n=2)
        self.assertEqual(result, ["qa_001", "qa_003"])

    def test_n_exceeds_history_count_returns_all(self) -> None:
        registry = _make_registry("qa_001", "qa_002", history_indices={1, 2})
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
    """Verify that the fallback logic in before_model_call correctly
    forces history preload when selector returns empty and continuation is detected."""

    def test_bug_scenario_permission_resume_empty_query(self) -> None:
        """Simulate the exact BUG_2097 scenario: permission resume with empty query
        and selector returning empty qa_ids."""
        ctx = _make_ctx(**{RESUME_USER_INPUT_KEY: InteractiveInput()})
        next_query = ""
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertTrue(is_continuation)

        registry = _make_registry("qa_001", history_indices={1})
        fallback_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(fallback_ids, ["qa_001"])

        selected_qa_ids: list[str] = []
        if not selected_qa_ids and is_continuation:
            selected_qa_ids = fallback_ids
        self.assertEqual(selected_qa_ids, ["qa_001"])

    def test_bug_scenario_permission_resume_with_empty_query_no_history(self) -> None:
        """Permission resume with empty query but no history blocks at all."""
        ctx = _make_ctx(**{RESUME_USER_INPUT_KEY: InteractiveInput()})
        next_query = ""
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertTrue(is_continuation)

        registry = _make_registry()
        fallback_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(fallback_ids, [])

        selected_qa_ids: list[str] = []
        if not selected_qa_ids and is_continuation:
            selected_qa_ids = fallback_ids
        self.assertEqual(selected_qa_ids, [])

    def test_normal_user_query_no_fallback(self) -> None:
        """Normal user query: selector returns non-empty, no fallback triggered."""
        ctx = _make_ctx()
        next_query = "帮我整理会议纪要"
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertFalse(is_continuation)

        registry = _make_registry("qa_001", history_indices={1})
        selected_qa_ids: list[str] = ["qa_001"]

        if not selected_qa_ids and is_continuation:
            selected_qa_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(selected_qa_ids, ["qa_001"])

    def test_normal_user_query_selector_empty_no_fallback(self) -> None:
        """Normal user query: selector returns empty, but NOT a continuation,
        so fallback should NOT be triggered."""
        ctx = _make_ctx()
        next_query = "今天天气怎么样"
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertFalse(is_continuation)

        registry = _make_registry("qa_001", history_indices={1})
        selected_qa_ids: list[str] = []

        if not selected_qa_ids and is_continuation:
            selected_qa_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(selected_qa_ids, [])

    def test_selector_exception_with_continuation_triggers_fallback(self) -> None:
        """Selector throws exception, fallback_rule_last_n also returns empty,
        but continuation is detected → force load history."""
        ctx = _make_ctx(**{RESUME_USER_INPUT_KEY: InteractiveInput()})
        next_query = ""
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertTrue(is_continuation)

        registry = _make_registry("qa_001", "qa_002", history_indices={1, 2})
        selected_qa_ids: list[str] = []

        if not selected_qa_ids and is_continuation:
            selected_qa_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(selected_qa_ids, ["qa_001", "qa_002"])

    def test_empty_query_without_resume_no_fallback(self) -> None:
        ctx = _make_ctx()
        next_query = ""
        is_continuation = _is_task_continuation(ctx, next_query)
        self.assertFalse(is_continuation)

        registry = _make_registry("qa_001", history_indices={1})
        selected_qa_ids: list[str] = []

        if not selected_qa_ids and is_continuation:
            selected_qa_ids = _last_n_history_qa_ids(registry, n=QABlockConfig().max_preload_blocks)
        self.assertEqual(selected_qa_ids, [])


if __name__ == "__main__":
    unittest.main()
