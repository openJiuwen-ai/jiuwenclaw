# coding: utf-8
# pylint: disable=protected-access
"""SkillComplianceRail.before_invoke publishes session_id via ContextVar."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenclaw.agentserver.deep_agent.rails import skill_compliance_rail as scr
from jiuwenclaw.agentserver.deep_agent.rails.skill_compliance_rail import (
    SkillComplianceRail,
)


class TestBeforeInvokePublishesSession:
    """Verify before_invoke sets the ContextVar read by the patch layer.

    ContextVar is context-scoped, so each test runs both the set (in
    ``before_invoke``) and the read inside the same ``asyncio.run`` coroutine.
    """

    @staticmethod
    def test_before_invoke_sets_contextvar():
        rail = SkillComplianceRail()
        ctx = SimpleNamespace(
            inputs=SimpleNamespace(conversation_id="conv-123"),
        )

        async def scenario():
            await rail.before_invoke(ctx)
            return scr._current_session_var.get()

        assert asyncio.run(scenario()) == "conv-123"

    @staticmethod
    def test_before_invoke_overwrites_contextvar_on_subsequent_invoke():
        rail = SkillComplianceRail()
        ctx1 = SimpleNamespace(
            inputs=SimpleNamespace(conversation_id="conv-1"),
        )
        ctx2 = SimpleNamespace(
            inputs=SimpleNamespace(conversation_id="conv-2"),
        )

        async def scenario():
            await rail.before_invoke(ctx1)
            first = scr._current_session_var.get()
            await rail.before_invoke(ctx2)
            second = scr._current_session_var.get()
            return first, second

        first, second = asyncio.run(scenario())
        assert first == "conv-1"
        assert second == "conv-2"

    @staticmethod
    def test_before_invoke_does_not_use_ctx_extra_sidechannel():
        """Regression guard: ContextVar is the sole propagation mechanism, so
        ``ctx.extra`` must not be written to.
        """
        rail = SkillComplianceRail()
        extra: dict = {}
        ctx = SimpleNamespace(
            inputs=SimpleNamespace(conversation_id="conv-xyz"),
            extra=extra,
        )

        async def scenario():
            await rail.before_invoke(ctx)
            return scr._current_session_var.get()

        assert asyncio.run(scenario()) == "conv-xyz"
        assert extra == {}

    @staticmethod
    def test_before_invoke_resolves_preset_session_id():
        rail = SkillComplianceRail(session_id="preset-session")
        ctx = SimpleNamespace(
            inputs=SimpleNamespace(conversation_id="conv-from-inputs"),
        )

        async def scenario():
            await rail.before_invoke(ctx)
            return scr._current_session_var.get()

        assert asyncio.run(scenario()) == "preset-session"
