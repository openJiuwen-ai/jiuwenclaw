# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Unit tests for jiuwenswarm.server.hooks.rail_hook_emitter.

覆盖 PermissionRequest / PermissionDenied / SubagentStart / SubagentStop
四个无 rail 回调事件的发射逻辑。
"""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock

import pytest

from jiuwenswarm.common.hooks_config import HookEvent, HooksConfig, HookMatcher
from jiuwenswarm.server.hooks.rail_hook_emitter import RailHookEmitter


def _make_config(**events):
    matchers = {}
    for event_name, matcher_list in events.items():
        matchers[event_name] = [
            HookMatcher(matcher=m[0], hooks=m[1]) for m in matcher_list
        ]
    return HooksConfig(events=matchers)


# ============================================================
# RailHookEmitter.fire (async)
# ============================================================

class TestFire:
    @pytest.mark.asyncio
    async def test_no_match_does_nothing(self):
        em = RailHookEmitter(_make_config())
        em._executor.run_all = AsyncMock(return_value=[])
        await em.fire(HookEvent.PERMISSION_REQUEST, query="Bash")
        em._executor.run_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_fires_permission_request(self):
        em = RailHookEmitter(_make_config(
            PermissionRequest=[("Bash", [{"command": "echo ok", "timeout": 5}])]
        ))
        em._executor.run_all = AsyncMock(return_value=[])
        await em.fire(
            HookEvent.PERMISSION_REQUEST, query="Bash",
            hook_input={"tool_name": "Bash"}, session_id="s1",
        )
        hi = em._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "PermissionRequest"
        assert hi["tool_name"] == "Bash"
        assert hi["session_id"] == "s1"

    @pytest.mark.asyncio
    async def test_fires_permission_denied(self):
        em = RailHookEmitter(_make_config(
            PermissionDenied=[("*", [{"command": "echo ok", "timeout": 5}])]
        ))
        em._executor.run_all = AsyncMock(return_value=[])
        await em.fire(
            HookEvent.PERMISSION_DENIED, query="Bash",
            hook_input={"tool_name": "Bash", "reason": "rejected"},
            session_id="s2",
        )
        hi = em._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "PermissionDenied"
        assert hi["reason"] == "rejected"

    @pytest.mark.asyncio
    async def test_fires_subagent_start(self):
        em = RailHookEmitter(_make_config(
            SubagentStart=[("general-purpose", [{"command": "echo ok", "timeout": 5}])]
        ))
        em._executor.run_all = AsyncMock(return_value=[])
        await em.fire(
            HookEvent.SUBAGENT_START, query="general-purpose",
            hook_input={"subagent_type": "general-purpose"}, session_id="sub-1",
        )
        hi = em._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "SubagentStart"
        assert hi["session_id"] == "sub-1"

    @pytest.mark.asyncio
    async def test_fires_subagent_stop(self):
        em = RailHookEmitter(_make_config(
            SubagentStop=[("*", [{"command": "echo ok", "timeout": 5}])]
        ))
        em._executor.run_all = AsyncMock(return_value=[])
        await em.fire(
            HookEvent.SUBAGENT_STOP, query="member-1",
            hook_input={"member_name": "member-1", "reason": "dynamic-member-stop"},
            session_id="s3",
        )
        hi = em._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "SubagentStop"
        assert hi["member_name"] == "member-1"

    @pytest.mark.asyncio
    async def test_disable_all_skips(self):
        em = RailHookEmitter(HooksConfig(
            events={"PermissionRequest": [HookMatcher(matcher="*", hooks=[{"command": "echo ok"}])]},
            disable_all_hooks=True,
        ))
        em._executor.run_all = AsyncMock(return_value=[])
        await em.fire(HookEvent.PERMISSION_REQUEST, query="Bash")
        em._executor.run_all.assert_not_called()

    @pytest.mark.asyncio
    async def test_sets_default_event_and_session_in_hook_input(self):
        """不传 hook_input/session_id 时自动补 event 与空 session_id."""
        em = RailHookEmitter(_make_config(
            Setup=[("*", [{"command": "echo ok", "timeout": 5}])]
        ))
        em._executor.run_all = AsyncMock(return_value=[])
        await em.fire(HookEvent.SETUP, query="gateway")
        hi = em._executor.run_all.await_args.kwargs["hook_input"]
        assert hi["event"] == "Setup"
        assert hi["session_id"] == ""


# ============================================================
# RailHookEmitter.trigger (sync fire-and-forget)
# ============================================================

class TestTrigger:
    @pytest.mark.asyncio
    async def test_trigger_schedules_task_and_runs(self):
        em = RailHookEmitter(_make_config(
            PermissionDenied=[("*", [{"command": "echo ok", "timeout": 5}])]
        ))
        em._executor.run_all = AsyncMock(return_value=[])
        em.trigger(
            HookEvent.PERMISSION_DENIED, query="Bash",
            hook_input={"tool_name": "Bash", "reason": "x"}, session_id="s",
        )
        # 让事件循环处理 create_task 调度出的 fire
        await asyncio.sleep(0)
        em._executor.run_all.assert_awaited_once()

    @pytest.mark.asyncio
    async def test_trigger_no_match_does_nothing(self):
        em = RailHookEmitter(_make_config())
        em._executor.run_all = AsyncMock(return_value=[])
        em.trigger(HookEvent.PERMISSION_REQUEST, query="Bash")
        await asyncio.sleep(0)
        em._executor.run_all.assert_not_called()


# ============================================================
# config lazy load
# ============================================================

class TestConfigLazyLoad:
    def test_explicit_config_used(self):
        cfg = _make_config(Setup=[("*", [{"command": "echo ok"}])])
        em = RailHookEmitter(cfg)
        assert em.config is cfg

    def test_reload_clears_cache(self):
        cfg = _make_config(Setup=[("*", [{"command": "echo ok"}])])
        em = RailHookEmitter(cfg)
        em.reload(None)
        assert em._config is None
