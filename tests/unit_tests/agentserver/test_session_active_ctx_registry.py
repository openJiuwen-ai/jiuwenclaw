# coding: utf-8
# pylint: disable=protected-access
"""SessionActiveCtxRegistry multi-session isolation."""

import unittest
from unittest.mock import MagicMock

from openjiuwen.core.single_agent.rail.base import AgentCallbackContext

from jiuwenclaw.agentserver.deep_agent.session_active_ctx_registry import SessionActiveCtxRegistry


def _ctx(session_id: str) -> AgentCallbackContext:
    session = MagicMock()
    session.get_session_id.return_value = session_id
    return AgentCallbackContext(agent=MagicMock(), session=session)


class TestSessionActiveCtxRegistry(unittest.TestCase):
    def test_concurrent_sessions_isolated(self) -> None:
        registry = SessionActiveCtxRegistry()
        ctx_a = _ctx("s1")
        ctx_b = _ctx("s2")
        registry.pin(ctx_a)
        registry.pin(ctx_b)
        self.assertIs(registry.resolve(session=ctx_a.session), ctx_a)
        self.assertIs(registry.resolve(session=ctx_b.session), ctx_b)
        registry.pop(ctx_a)
        self.assertIsNone(registry.resolve(session=ctx_a.session))
        self.assertIs(registry.resolve(session=ctx_b.session), ctx_b)

    def test_no_single_session_fallback(self) -> None:
        registry = SessionActiveCtxRegistry()
        ctx_a = _ctx("s1")
        ctx_b = _ctx("s2")
        registry.pin(ctx_a)
        registry.pin(ctx_b)
        self.assertIsNone(registry.resolve(session=None))

    def test_replace_same_session_reinvoke(self) -> None:
        registry = SessionActiveCtxRegistry()
        first = _ctx("s1")
        second = _ctx("s1")
        registry.pin(first)
        registry.pin(second)
        self.assertIs(registry.resolve(session=second.session), second)
        registry.pop(second)
        self.assertIsNone(registry.resolve(session=second.session))


if __name__ == "__main__":
    unittest.main()
