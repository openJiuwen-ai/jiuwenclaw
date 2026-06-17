# coding: utf-8
"""Unit tests for per-session AgentCard.id resolution."""

from __future__ import annotations

import re
import unittest

from jiuwenclaw.agentserver.deep_agent.agent_card_id import (
    DEFAULT_SESSION_ID,
    JIUWENCLAW_RESOURCE_AGENT_ID,
    is_default_session,
    resolve_agent_card_id,
)


class TestIsDefaultSession(unittest.TestCase):
    def test_empty_is_default(self) -> None:
        self.assertTrue(is_default_session(None))
        self.assertTrue(is_default_session(""))
        self.assertTrue(is_default_session("   "))

    def test_default_literal_is_default(self) -> None:
        self.assertTrue(is_default_session(DEFAULT_SESSION_ID))

    def test_real_session_is_not_default(self) -> None:
        self.assertFalse(is_default_session("cfb850fc"))


class TestResolveAgentCardId(unittest.TestCase):
    def test_normal_session_id(self) -> None:
        card_id, suffix = resolve_agent_card_id(
            "cfb850fc",
            cached_session_id=None,
            fallback_card_suffix=None,
        )
        self.assertEqual(card_id, f"{JIUWENCLAW_RESOURCE_AGENT_ID}_cfb850fc")
        self.assertIsNone(suffix)

    def test_empty_session_uses_uuid_fallback(self) -> None:
        card_id, suffix = resolve_agent_card_id(
            "",
            cached_session_id=None,
            fallback_card_suffix=None,
        )
        pattern = rf"^{JIUWENCLAW_RESOURCE_AGENT_ID}_{DEFAULT_SESSION_ID}_[0-9a-f]{{12}}$"
        self.assertRegex(card_id, pattern)
        self.assertRegex(suffix or "", re.compile(r"^[0-9a-f]{12}$"))

    def test_default_session_uses_uuid_fallback(self) -> None:
        card_id, _suffix = resolve_agent_card_id(
            DEFAULT_SESSION_ID,
            cached_session_id=None,
            fallback_card_suffix=None,
        )
        pattern = rf"^{JIUWENCLAW_RESOURCE_AGENT_ID}_{DEFAULT_SESSION_ID}_[0-9a-f]{{12}}$"
        self.assertRegex(card_id, pattern)

    def test_fallback_suffix_stable_for_same_adapter(self) -> None:
        first_id, suffix = resolve_agent_card_id(
            "",
            cached_session_id=None,
            fallback_card_suffix=None,
        )
        second_id, returned_suffix = resolve_agent_card_id(
            None,
            cached_session_id=None,
            fallback_card_suffix=suffix,
        )
        self.assertEqual(first_id, second_id)
        self.assertEqual(suffix, returned_suffix)

    def test_uses_cached_instance_session_id(self) -> None:
        card_id, suffix = resolve_agent_card_id(
            None,
            cached_session_id="abc123",
            fallback_card_suffix=None,
        )
        self.assertEqual(card_id, f"{JIUWENCLAW_RESOURCE_AGENT_ID}_abc123")
        self.assertIsNone(suffix)

    def test_explicit_session_id_overrides_cache(self) -> None:
        card_id, suffix = resolve_agent_card_id(
            "explicit",
            cached_session_id="cached",
            fallback_card_suffix=None,
        )
        self.assertEqual(card_id, f"{JIUWENCLAW_RESOURCE_AGENT_ID}_explicit")
        self.assertIsNone(suffix)


if __name__ == "__main__":
    unittest.main()
