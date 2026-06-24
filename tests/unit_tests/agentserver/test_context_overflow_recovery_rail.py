# coding: utf-8
# pylint: disable=protected-access
"""Tests for ContextOverflowRecoveryRail.before_model_call proactive bridge.

Covers the add_messages -> overflow recovery bridge added on the GET-path:
1. deferred flag triggers recovery prep
2. force_compact_pending flag triggers recovery prep
3. neither flag set -> no-op (but deferred flag is still consumed)
4. no FullCompactProcessor on context -> no-op
5. ctx.context is None -> no-op
6. end-to-end: recovery prep sets force_compact on the processor
"""

import asyncio
import importlib.util
import pathlib
import unittest
from unittest.mock import AsyncMock, patch

from openjiuwen.core.context_engine.processor.compressor.full_compact_processor import (
    FullCompactProcessorConfig,
)

# Load the rail module directly from its file to bypass
# ``jiuwenclaw.agentserver.deep_agent.rails.__init__`` which eager-imports
# many sibling rails (and their heavy tool deps). The module under test only
# depends on ``openjiuwen.*``, so direct loading keeps this unit test isolated.
_MODULE_PATH = (
    pathlib.Path(__file__).resolve().parents[3]
    / "jiuwenclaw"
    / "agentserver"
    / "deep_agent"
    / "rails"
    / "context_overflow_recovery_rail.py"
)
assert _MODULE_PATH.exists(), f"rail module path does not exist: {_MODULE_PATH}"
_spec = importlib.util.spec_from_file_location(
    "context_overflow_recovery_rail_under_test", _MODULE_PATH
)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
assert hasattr(_module, "ContextOverflowRecoveryRail")
ContextOverflowRecoveryRail = _module.ContextOverflowRecoveryRail
# Use the same FullCompactProcessor class object the rail module isinstance-checks.
FullCompactProcessor = _module.FullCompactProcessor


class _TestFullCompactProcessor(FullCompactProcessor):
    """Subclass with proactive-bridge hooks for unit tests."""

    def __init__(self, *, deferred: bool = False, force_pending: bool = False):
        super().__init__(FullCompactProcessorConfig())
        self._deferred_overflow_recovery = deferred
        self._force_compact = force_pending
        self.set_force_compact_calls = 0

    def consume_deferred_overflow_recovery(self):
        deferred = self._deferred_overflow_recovery
        self._deferred_overflow_recovery = False
        return deferred

    def is_force_compact_pending(self):
        return self._force_compact

    def set_force_compact(self, value=True):
        self.set_force_compact_calls += 1
        self._force_compact = value


class _OtherProcessor:
    """A non-FullCompact processor to ensure isinstance matching is strict."""


class _LegacyFullCompactProcessor(FullCompactProcessor):
    """Simulate an older agent-core FullCompactProcessor without bridge APIs."""

    consume_deferred_overflow_recovery = None
    is_force_compact_pending = None

    def __init__(self):
        super().__init__(FullCompactProcessorConfig())


class _Context:
    def __init__(self, processors):
        self._processors = processors


class _Ctx:
    def __init__(self, context, *, exception=None):
        self.context = context
        self.exception = exception
        self.retry_delays = []

    def request_retry(self, delay_seconds=0):
        self.retry_delays.append(delay_seconds)


class TestProactiveBridgeBeforeModelCall(unittest.TestCase):
    def _make_rail(self):
        rail = ContextOverflowRecoveryRail()
        # No agent bound -> _force_session_memory_update early-returns,
        # keeping the test focused on the bridge branching logic.
        rail._agent = None
        return rail

    def test_deferred_flag_triggers_prep(self):
        rail = self._make_rail()
        processor = _TestFullCompactProcessor(deferred=True, force_pending=False)
        ctx = _Ctx(_Context([processor]))

        with patch.object(rail, "_run_overflow_recovery_prep", new=AsyncMock()) as prep:
            asyncio.run(rail.before_model_call(ctx))

        prep.assert_awaited_once()
        assert processor._deferred_overflow_recovery is False

    def test_force_pending_flag_triggers_prep(self):
        rail = self._make_rail()
        processor = _TestFullCompactProcessor(deferred=False, force_pending=True)
        ctx = _Ctx(_Context([processor]))

        with patch.object(rail, "_run_overflow_recovery_prep", new=AsyncMock()) as prep:
            asyncio.run(rail.before_model_call(ctx))

        prep.assert_awaited_once()

    def test_no_flags_is_noop_but_consumes_deferred(self):
        rail = self._make_rail()
        processor = _TestFullCompactProcessor(deferred=False, force_pending=False)
        ctx = _Ctx(_Context([processor]))

        with patch.object(rail, "_run_overflow_recovery_prep", new=AsyncMock()) as prep:
            asyncio.run(rail.before_model_call(ctx))

        prep.assert_not_awaited()
        # deferred flag is always consumed (read-and-clear) even on no-op.
        assert processor._deferred_overflow_recovery is False

    def test_no_full_compact_processor_is_noop(self):
        rail = self._make_rail()
        ctx = _Ctx(_Context([_OtherProcessor()]))

        with patch.object(rail, "_run_overflow_recovery_prep", new=AsyncMock()) as prep:
            asyncio.run(rail.before_model_call(ctx))

        prep.assert_not_awaited()

    def test_missing_bridge_api_is_noop_and_warns_once(self):
        rail = self._make_rail()
        processor = _LegacyFullCompactProcessor()
        ctx = _Ctx(_Context([processor]))

        with patch.object(rail, "_run_overflow_recovery_prep", new=AsyncMock()) as prep:
            asyncio.run(rail.before_model_call(ctx))
            asyncio.run(rail.before_model_call(ctx))

        prep.assert_not_awaited()
        assert rail._logged_missing_full_compact_bridge_api is True

    def test_context_none_is_noop(self):
        rail = self._make_rail()
        ctx = _Ctx(None)

        with patch.object(rail, "_run_overflow_recovery_prep", new=AsyncMock()) as prep:
            asyncio.run(rail.before_model_call(ctx))

        prep.assert_not_awaited()

    def test_prep_sets_force_compact_end_to_end(self):
        rail = self._make_rail()
        processor = _TestFullCompactProcessor(deferred=True, force_pending=False)
        ctx = _Ctx(_Context([processor]))

        # No patching: exercise the real _run_overflow_recovery_prep ->
        # _set_force_compact_flag path (session-memory step early-returns
        # because no agent / CE rail is bound).
        asyncio.run(rail.before_model_call(ctx))

        assert processor.is_force_compact_pending() is True

    def test_reactive_overflow_sets_force_compact_once_and_retries(self):
        rail = self._make_rail()
        processor = _TestFullCompactProcessor(deferred=False, force_pending=False)
        ctx = _Ctx(
            _Context([processor]),
            exception=RuntimeError("context length exceeded"),
        )

        asyncio.run(rail.on_model_exception(ctx))

        assert processor.is_force_compact_pending() is True
        assert processor.set_force_compact_calls == 1
        assert ctx.retry_delays == [0]


if __name__ == "__main__":
    unittest.main()
