# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for jiuwenswarm.gateway.hooks.handler.GatewayHookHandler.

覆盖补齐的 ConfigChange / InstructionsLoaded / Setup 三个方法，以及
SessionEnd / Notification（此前为死代码，现已接入调用点）。
"""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.common.hooks_config import HooksConfig, HookMatcher
from jiuwenswarm.gateway.hooks.handler import GatewayHookHandler


def _make_config(**events):
    matchers = {}
    for event_name, matcher_list in events.items():
        matchers[event_name] = [
            HookMatcher(matcher=m[0], hooks=m[1]) for m in matcher_list
        ]
    return HooksConfig(events=matchers)


# ============================================================
# ConfigChange
# ============================================================

class TestOnConfigChange:
    @pytest.mark.asyncio
    async def test_no_match_does_nothing(self):
        h = GatewayHookHandler(_make_config())
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_config_change(["models.defaults"])
        h._executor.run_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_fires_for_matching_key(self):
        h = GatewayHookHandler(_make_config(
            ConfigChange=[("models.defaults", [{"command": "echo ok", "timeout": 5}])]
        ))
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_config_change(["models.defaults"])
        hi = h._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "ConfigChange"
        assert hi["changed_keys"] == ["models.defaults"]

    @pytest.mark.asyncio
    async def test_star_matches_any_key(self):
        h = GatewayHookHandler(_make_config(
            ConfigChange=[("*", [{"command": "echo ok", "timeout": 5}])]
        ))
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_config_change(["permissions"])
        assert h._executor.run_all.await_args.kwargs["hook_input"]["changed_keys"] == ["permissions"]

    @pytest.mark.asyncio
    async def test_dedupes_multiple_keys_matching_star(self):
        """"*" matcher 命中多 key 时只触发一次（按对象 id 去重）."""
        h = GatewayHookHandler(_make_config(
            ConfigChange=[("*", [{"command": "echo ok", "timeout": 5}])]
        ))
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_config_change(["a", "b", "c"])
        configs = h._executor.run_all.await_args.args[0]
        assert len(configs) == 1

    @pytest.mark.asyncio
    async def test_empty_keys_fires_star_matcher(self):
        h = GatewayHookHandler(_make_config(
            ConfigChange=[("*", [{"command": "echo ok", "timeout": 5}])]
        ))
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_config_change()
        h._executor.run_all.assert_awaited_once()


# ============================================================
# InstructionsLoaded
# ============================================================

class TestOnInstructionsLoaded:
    @pytest.mark.asyncio
    async def test_no_match_does_nothing(self):
        h = GatewayHookHandler(_make_config())
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_instructions_loaded(source="AGENTS.md")
        h._executor.run_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_fires_with_source(self):
        h = GatewayHookHandler(_make_config(
            InstructionsLoaded=[("AGENTS.md", [{"command": "echo ok", "timeout": 5}])]
        ))
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_instructions_loaded(source="AGENTS.md", session_id="s1")
        hi = h._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "InstructionsLoaded"
        assert hi["source"] == "AGENTS.md"
        assert hi["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_star_matches_any_source(self):
        h = GatewayHookHandler(_make_config(
            InstructionsLoaded=[("*", [{"command": "echo ok", "timeout": 5}])]
        ))
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_instructions_loaded(source="custom.md")
        h._executor.run_all.assert_awaited_once()


# ============================================================
# Setup
# ============================================================

class TestOnSetup:
    @pytest.mark.asyncio
    async def test_no_match_does_nothing(self):
        h = GatewayHookHandler(_make_config())
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_setup()
        h._executor.run_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_fires_with_source(self):
        h = GatewayHookHandler(_make_config(
            Setup=[("gateway", [{"command": "echo ok", "timeout": 5}])]
        ))
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_setup(source="gateway", session_id="s2")
        hi = h._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "Setup"
        assert hi["source"] == "gateway"
        assert hi["session_id"] == "s2"


# ============================================================
# SessionEnd / Notification（已有方法，验证仍可用）
# ============================================================

class TestSessionEnd:
    @pytest.mark.asyncio
    async def test_fires_on_delete_reason(self):
        h = GatewayHookHandler(_make_config(
            SessionEnd=[("delete", [{"command": "echo ok", "timeout": 5}])]
        ))
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_session_end("s3", reason="delete")
        hi = h._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "SessionEnd"
        assert hi["reason"] == "delete"

    @pytest.mark.asyncio
    async def test_removes_from_active_sessions(self):
        h = GatewayHookHandler(_make_config())
        h._active_sessions.add("s4")
        await h.on_session_end("s4", reason="delete")
        assert "s4" not in h._active_sessions


class TestNotification:
    @pytest.mark.asyncio
    async def test_fires_with_type(self):
        h = GatewayHookHandler(_make_config(
            Notification=[("notice", [{"command": "echo ok", "timeout": 5}])]
        ))
        h._executor.run_all = AsyncMock(return_value=[])
        await h.on_notification("notice", "hello", session_id="s5")
        hi = h._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "Notification"
        assert hi["notification_type"] == "notice"
        assert hi["message"] == "hello"
