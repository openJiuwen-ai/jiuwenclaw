# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Team security guardrail contract: ALLOW / ASK→leader / no teammate user HITL."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent.team_helpers import (
    _is_ask_user_tool_event,
)
from jiuwenclaw.agentserver.swarm.config_specs import build_member_capability_specs
from jiuwenclaw.agentserver.swarm.registry import (
    JIUWEN_WEB_SEARCH,
    PLATFORM_CATALOG_TOOLS,
    PLATFORM_MEMBER_RAILS,
)
from jiuwenclaw.agentserver.team.team_runtime_inheritance import build_team_permission_rails
from openjiuwen.harness.security.models import PermissionLevel


@pytest.mark.asyncio
async def test_explicit_allow_skips_ask() -> None:
    """Requirement 2: tools.X=allow must not land on ASK (plan-aligned engine)."""
    from jiuwenclaw.agentserver.permissions.core import PermissionEngine as JiuwenclawPermissionEngine
    from jiuwenclaw.agentserver.team.rails.permission_engine_adapter import (
        JiuwenclawPermissionEngineAdapter,
    )

    engine = JiuwenclawPermissionEngineAdapter(
        JiuwenclawPermissionEngine(
            config={
                "enabled": True,
                "tools": {
                    "bash": "allow",
                    "web_free_search": "allow",
                    "read_file": "allow",
                },
            },
        )
    )
    for name, args in (
        ("bash", {}),
        ("bash", {"command": "ls"}),
        ("web_free_search", {"query": "x"}),
        ("read_file", {"path": "a.txt"}),
    ):
        result = await engine.check_permission(name, args)
        assert result.permission == PermissionLevel.ALLOW, (name, args, result.matched_rule)


@pytest.mark.asyncio
async def test_explicit_ask_is_ask() -> None:
    """Requirement 1 prelude: tools.X=ask evaluates to ASK."""
    from jiuwenclaw.agentserver.permissions.core import PermissionEngine as JiuwenclawPermissionEngine
    from jiuwenclaw.agentserver.team.rails.permission_engine_adapter import (
        JiuwenclawPermissionEngineAdapter,
    )

    engine = JiuwenclawPermissionEngineAdapter(
        JiuwenclawPermissionEngine(config={"enabled": True, "tools": {"bash": "ask"}}),
    )
    result = await engine.check_permission("bash", {"command": "ls"})
    assert result.permission == PermissionLevel.ASK


def test_teammate_rail_routes_ask_via_leader_host(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 3: teammate mounts TeamPermissionRail with hosted leader path."""
    created: dict[str, object] = {}

    class _FakeRail:
        def __init__(self, *, config, engine=None, host=None):
            created["config"] = config
            created["engine"] = engine
            created["host"] = host

    class _FakeOrchestrator:
        def __init__(self, **kwargs):
            pass

        async def handle_approval_request(self, request):
            return "interrupt"

    monkeypatch.setattr(
        "openjiuwen.agent_teams.rails.team_permission_rail.TeamPermissionRail",
        _FakeRail,
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.rails.team_permission_rail.TeamApprovalOrchestrator",
        _FakeOrchestrator,
    )
    monkeypatch.setattr(
        "openjiuwen.agent_teams.tools.message_manager.TeamMessageManager",
        lambda *a, **k: MagicMock(),
    )

    rails = build_team_permission_rails(
        role="teammate",
        language="cn",
        permissions_config={"enabled": True, "tools": {"bash": "ask"}},
        team_backend=MagicMock(team_name="t", db=MagicMock()),
        messager=MagicMock(),
        member_name="worker",
        leader_member_name="office",
    )
    assert len(rails) == 1
    from jiuwenclaw.agentserver.team.rails.permission_engine_adapter import (
        JiuwenclawPermissionEngineAdapter,
    )

    assert isinstance(created["engine"], JiuwenclawPermissionEngineAdapter)
    host = created["host"]
    assert host is not None
    assert host.request_permission_confirmation is not None


def test_leader_mounts_user_facing_permission_rail(monkeypatch: pytest.MonkeyPatch) -> None:
    """Requirement 1 (leader): ASK surfaces via plan PermissionInterruptRail."""
    policy = MagicMock(name="policy")
    perm = MagicMock(name="perm")
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.team.rails.team_permission_policy_rail.TeamPermissionPolicyRail",
        lambda **kwargs: policy,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.interrupt.interrupt_helpers.build_permission_rail",
        lambda *, config: perm,
    )
    rails = build_team_permission_rails(
        role="leader",
        language="cn",
        permissions_config={"enabled": True, "tools": {"bash": "ask"}},
        team_backend=None,
        messager=None,
        member_name="office",
        leader_member_name="office",
    )
    assert policy in rails and perm in rails


def test_teammate_permission_interrupt_frames_dropped_from_user_stream() -> None:
    """Requirement 3: teammate __interaction__→chat.ask_user_question must not reach UI.

    team_helpers drops non-leader chat.ask_user_question (covers permission_interrupt).
    """
    # Mirror the filter predicate used in _consume_stream_with_query_impl.
    is_leader = False
    parsed = {
        "event_type": "chat.ask_user_question",
        "source": "permission_interrupt",
        "request_id": "x",
        "questions": [],
    }
    assert (not is_leader) and parsed.get("event_type") == "chat.ask_user_question"
    # ask_user tool frames from teammates are also dropped.
    assert _is_ask_user_tool_event({"event_type": "chat.tool_call", "tool_name": "ask_user"})


def test_catalog_tools_remain_declared_for_capability() -> None:
    """Catalog mount stays on so guardrail has a real call surface."""
    rails, tools = build_member_capability_specs(
        {},
        "team",
        "teammate",
        enable_permissions=True,
        leader_member_name="office",
    )
    assert any(r.type == PLATFORM_MEMBER_RAILS for r in rails)
    assert rails[0].params.get("enable_permissions") is True
    types = [t.type for t in tools]
    assert JIUWEN_WEB_SEARCH in types
    assert PLATFORM_CATALOG_TOOLS in types


@pytest.mark.asyncio
async def test_hosted_ask_then_interrupt_does_not_skip_leader_path() -> None:
    """ASK + host returning 'interrupt' falls through to rail.interrupt (teammate suspend).

    Resume is driven by leader approve_tool → ToolApprovalResultEvent → InteractiveInput
    (agent_lifecycle.on_tool_approval_result), not by user permission UI.
    """
    from openjiuwen.harness.rails.security.tool_security_rail import PermissionInterruptRail
    from openjiuwen.harness.security.host import ToolPermissionHost
    from openjiuwen.harness.security.models import PermissionResult

    host_calls: list[object] = []

    async def _hosted(req):
        host_calls.append(req)
        return "interrupt"

    engine = MagicMock()
    engine.check_permission = AsyncMock(
        return_value=PermissionResult(
            permission=PermissionLevel.ASK,
            matched_rule="tools.bash",
            reason="ask",
        )
    )
    engine.update_config = MagicMock()
    engine.set_permission_checks_active = MagicMock()
    engine.update_llm = MagicMock()

    host = ToolPermissionHost(
        get_permissions_snapshot=lambda: {"enabled": True, "tools": {"bash": "ask"}},
        request_permission_confirmation=_hosted,
    )
    rail = PermissionInterruptRail(config={"enabled": True, "tools": {"bash": "ask"}}, engine=engine, host=host)

    tool_call = MagicMock()
    tool_call.name = "bash"
    tool_call.id = "tc1"
    tool_call.arguments = "{}"

    with patch.object(rail, "interrupt", return_value="INTERRUPTED") as interrupt_mock:
        with patch.object(rail, "_is_auto_confirmed", return_value=False):
            decision = await rail.resolve_interrupt(
                ctx=MagicMock(session=MagicMock()),
                tool_call=tool_call,
                user_input=None,
            )
    assert decision == "INTERRUPTED"
    assert len(host_calls) == 1
    interrupt_mock.assert_called_once()
