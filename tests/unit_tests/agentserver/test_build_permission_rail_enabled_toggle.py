# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""PermissionInterruptRail must stay mountable when the guardrail is off."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent.interrupt.interrupt_helpers import build_permission_rail
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse
from jiuwenclaw.schema.message import ReqMethod


def test_build_permission_rail_creates_rail_when_permissions_disabled() -> None:
    """Closing the approval guardrail must not skip rail creation.

    Agents created while disabled previously had no PermissionInterruptRail, so
    re-enabling the guardrail never recovered HITL until a full recreate.
    """
    fake_rail = object()
    fake_engine = MagicMock(name="permission_engine")

    with (
        patch(
            "jiuwenclaw.agentserver.deep_agent.interrupt.interrupt_helpers.get_permission_engine",
            return_value=fake_engine,
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.rails.permission_rail.PermissionInterruptRail",
            return_value=fake_rail,
        ) as rail_ctor,
    ):
        rail = build_permission_rail({"permissions": {"enabled": False, "tools": {"bash": "ask"}}})

    assert rail is fake_rail
    rail_ctor.assert_called_once()
    kwargs = rail_ctor.call_args.kwargs
    assert kwargs["config"]["enabled"] is False
    assert kwargs["engine"] is fake_engine


def test_build_permission_rail_still_creates_when_enabled() -> None:
    fake_rail = object()
    with (
        patch(
            "jiuwenclaw.agentserver.deep_agent.interrupt.interrupt_helpers.get_permission_engine",
            return_value=MagicMock(),
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.rails.permission_rail.PermissionInterruptRail",
            return_value=fake_rail,
        ),
    ):
        assert build_permission_rail({"permissions": {"enabled": True}}) is fake_rail


@pytest.mark.asyncio
async def test_handle_permissions_enabled_set_true_reloads_agents() -> None:
    """Re-enabling permissions must reload cached agents to remount missing rails."""
    from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    pool = MagicMock()
    pool.collect_runtime_tools_catalog_nowait = MagicMock(return_value={})
    pool.reload_agents_config = AsyncMock()
    server._agent_manager = pool
    fake_engine = MagicMock(name="permission_engine")

    request = AgentRequest(
        request_id="req-1",
        channel_id="officeclaw",
        session_id="sess-1",
        req_method=ReqMethod.PERMISSIONS_ENABLED_SET,
        params={"enabled": True},
    )
    ws = MagicMock()
    ws.send = AsyncMock()
    send_lock = AsyncMock()
    send_lock.__aenter__ = AsyncMock(return_value=None)
    send_lock.__aexit__ = AsyncMock(return_value=None)

    ok_resp = AgentResponse(
        request_id="req-1",
        channel_id="officeclaw",
        ok=True,
        payload={"enabled": True},
    )

    with (
        patch(
            "jiuwenclaw.agentserver.permissions.config_rpc.dispatch_permissions_config_request",
            return_value=ok_resp,
        ),
        patch(
            "jiuwenclaw.config.get_config",
            return_value={"permissions": {"enabled": True, "tools": {"bash": "ask"}}},
        ),
        patch(
            "jiuwenclaw.agentserver.permissions.core.get_permission_engine",
            return_value=fake_engine,
        ),
        patch(
            "jiuwenclaw.agentserver.agent_ws_server.encode_agent_response_for_wire",
            return_value={"ok": True},
        ),
    ):
        await server._handle_permissions_config(ws, request, send_lock)

    fake_engine.update_config.assert_called_once()
    assert fake_engine.update_config.call_args.args[0]["enabled"] is True
    pool.reload_agents_config.assert_awaited_once()
    reloaded_cfg = pool.reload_agents_config.await_args.args[0]
    assert reloaded_cfg["permissions"]["enabled"] is True
    ws.send.assert_awaited_once()


@pytest.mark.asyncio
async def test_handle_permissions_enabled_set_false_does_not_reload_agents() -> None:
    from jiuwenclaw.agentserver.agent_ws_server import AgentWebSocketServer

    server = AgentWebSocketServer.__new__(AgentWebSocketServer)
    pool = MagicMock()
    pool.collect_runtime_tools_catalog_nowait = MagicMock(return_value={})
    pool.reload_agents_config = AsyncMock()
    server._agent_manager = pool

    request = AgentRequest(
        request_id="req-2",
        channel_id="officeclaw",
        session_id="sess-1",
        req_method=ReqMethod.PERMISSIONS_ENABLED_SET,
        params={"enabled": False},
    )
    ws = MagicMock()
    ws.send = AsyncMock()
    send_lock = AsyncMock()
    send_lock.__aenter__ = AsyncMock(return_value=None)
    send_lock.__aexit__ = AsyncMock(return_value=None)

    ok_resp = AgentResponse(
        request_id="req-2",
        channel_id="officeclaw",
        ok=True,
        payload={"enabled": False},
    )

    with (
        patch(
            "jiuwenclaw.agentserver.permissions.config_rpc.dispatch_permissions_config_request",
            return_value=ok_resp,
        ),
        patch(
            "jiuwenclaw.agentserver.agent_ws_server.encode_agent_response_for_wire",
            return_value={"ok": True},
        ),
    ):
        await server._handle_permissions_config(ws, request, send_lock)

    pool.reload_agents_config.assert_not_awaited()
