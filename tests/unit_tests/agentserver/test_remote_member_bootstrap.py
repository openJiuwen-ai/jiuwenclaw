# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

import asyncio
import importlib.util
import json
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

_root = Path(__file__).resolve().parents[3]
_spec = importlib.util.spec_from_file_location(
    "_jiuwen_remote_member_bootstrap_test",
    _root / "jiuwenswarm" / "agents" / "harness" / "team" / "remote_member_bootstrap.py",
)
_mod = importlib.util.module_from_spec(_spec)
assert _spec.loader is not None
_spec.loader.exec_module(_mod)
remote_member_names = _mod.remote_member_names
remote_all_spawn_members = _mod.remote_all_spawn_members
parse_remote_bootstrap_ack_json = _mod.parse_remote_bootstrap_ack_json
build_bootstrap_ack_envelope = _mod.build_bootstrap_ack_envelope
attach_remote_bootstrap_ack_listener = _mod.attach_remote_bootstrap_ack_listener
attach_distributed_local_spawn_guard = _mod.attach_distributed_local_spawn_guard
attach_spawn_member_remote_bootstrap_wrapper = _mod.attach_spawn_member_remote_bootstrap_wrapper
release_a2x_reservations_for_team = _mod.release_a2x_reservations_for_team
REMOTE_TEAM_DESTROY_DIRECT_EVENT_TYPE = _mod.REMOTE_TEAM_DESTROY_DIRECT_EVENT_TYPE


def test_remote_member_names_accepts_string():
    cfg = {"team": {"metadata": {"jiuwen_remote_member_names": "  t1  "}}}
    assert remote_member_names(cfg) == {"t1"}


def test_remote_member_names_accepts_list():
    cfg = {"team": {"metadata": {"jiuwen_remote_member_names": ["a", "b", ""]}}}
    assert remote_member_names(cfg) == {"a", "b"}


def test_remote_member_names_empty_when_missing():
    assert remote_member_names({"team": {}}) == set()


def test_remote_all_spawn_members_true_in_distributed_by_default():
    cfg = {"team": {"runtime": {"mode": "distributed"}}}
    assert remote_all_spawn_members(cfg) is True


def test_remote_all_spawn_members_honors_metadata_override():
    cfg = {
        "team": {
            "runtime": {"mode": "distributed"},
            "metadata": {"jiuwen_remote_all_spawn_members": False},
        },
    }
    assert remote_all_spawn_members(cfg) is False


def test_parse_remote_bootstrap_ack_json_accepts_valid():
    body = json.dumps(build_bootstrap_ack_envelope(member_name="m1", team_name="t1"))
    parsed = parse_remote_bootstrap_ack_json(body)
    assert parsed is not None
    assert parsed["member_name"] == "m1"
    assert parsed.get("team_name") == "t1"


def test_parse_remote_bootstrap_ack_json_rejects_non_json():
    assert parse_remote_bootstrap_ack_json("not json") is None


@pytest.mark.asyncio
async def test_ack_listener_updates_db_and_marks_read(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole

    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {
            "team": {
                "runtime": {"mode": "distributed", "role": "leader"},
                "metadata": {"jiuwen_remote_member_names": ["remote1"]},
            }
        },
    )

    listeners: list = []
    db = MagicMock()
    db.get_message = AsyncMock(
        return_value=SimpleNamespace(
            content=json.dumps(build_bootstrap_ack_envelope(member_name="remote1", team_name="tn")),
            from_member_name="remote1",
            to_member_name="leader1",
        )
    )
    db.update_member_status = AsyncMock(return_value=True)
    mm = MagicMock()
    mm.mark_message_read = AsyncMock(return_value=True)

    ta = SimpleNamespace(
        role=TeamRole.LEADER,
        team_backend=SimpleNamespace(db=db),
        message_manager=mm,
        _member_name=lambda: "leader1",
        _team_name=lambda: "tn",
        add_event_listener=listeners.append,
    )

    attach_remote_bootstrap_ack_listener(ta, session_id="sid", channel_id=None)
    assert len(listeners) == 1

    ev = SimpleNamespace(
        event_type="message",
        payload={
            "message_id": "mid-1",
            "from_member_name": "remote1",
            "to_member_name": "leader1",
            "team_name": "tn",
        },
    )
    await listeners[0](ev)

    db.get_message.assert_awaited_once_with("mid-1")
    db.update_member_status.assert_awaited_once_with("remote1", "tn", "ready")
    mm.mark_message_read.assert_awaited_once_with("mid-1", "leader1")


@pytest.mark.asyncio
async def test_ack_listener_ignores_plain_text_message(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole

    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {
            "team": {
                "runtime": {"mode": "distributed", "role": "leader"},
                "metadata": {"jiuwen_remote_member_names": ["remote1"]},
            }
        },
    )

    listeners: list = []
    db = MagicMock()
    db.get_message = AsyncMock(
        return_value=SimpleNamespace(
            content="hello leader",
            from_member_name="remote1",
            to_member_name="leader1",
        )
    )
    db.update_member_status = AsyncMock(return_value=True)
    mm = MagicMock()
    mm.mark_message_read = AsyncMock(return_value=True)

    ta = SimpleNamespace(
        role=TeamRole.LEADER,
        team_backend=SimpleNamespace(db=db),
        message_manager=mm,
        _member_name=lambda: "leader1",
        _team_name=lambda: "tn",
        add_event_listener=listeners.append,
    )

    attach_remote_bootstrap_ack_listener(ta, session_id="sid", channel_id=None)
    ev = SimpleNamespace(
        event_type="message",
        payload={
            "message_id": "mid-2",
            "from_member_name": "remote1",
            "to_member_name": "leader1",
        },
    )
    await listeners[0](ev)

    db.get_message.assert_awaited_once_with("mid-2")
    db.update_member_status.assert_not_awaited()
    mm.mark_message_read.assert_not_awaited()


@pytest.mark.asyncio
async def test_ack_listener_accepts_any_sender_when_remote_all(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole

    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {"team": {"runtime": {"mode": "distributed", "role": "leader"}}},
    )

    listeners: list = []
    db = MagicMock()
    db.get_message = AsyncMock(
        return_value=SimpleNamespace(
            content=json.dumps(build_bootstrap_ack_envelope(member_name="calculator-1", team_name="tn")),
            from_member_name="calculator-1",
            to_member_name="leader1",
        )
    )
    db.update_member_status = AsyncMock(return_value=True)
    mm = MagicMock()
    mm.mark_message_read = AsyncMock(return_value=True)

    ta = SimpleNamespace(
        role=TeamRole.LEADER,
        team_backend=SimpleNamespace(db=db),
        message_manager=mm,
        _member_name=lambda: "leader1",
        _team_name=lambda: "tn",
        add_event_listener=listeners.append,
    )

    attach_remote_bootstrap_ack_listener(ta, session_id="sid", channel_id=None)
    ev = SimpleNamespace(
        event_type="message",
        payload={
            "message_id": "mid-3",
            "from_member_name": "calculator-1",
            "to_member_name": "leader1",
        },
    )
    await listeners[0](ev)

    db.update_member_status.assert_awaited_once_with("calculator-1", "tn", "ready")


@pytest.mark.asyncio
async def test_distributed_local_spawn_guard_disables_local_startup(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.runner import Runner

    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {"team": {"runtime": {"mode": "distributed", "role": "leader"}}},
    )

    send_message_tool = SimpleNamespace(_on_teammate_created=object())
    resource_mgr = MagicMock()
    resource_mgr.get_tool = MagicMock(return_value=send_message_tool)
    monkeypatch.setattr(Runner, "resource_mgr", resource_mgr)

    original_spawn = AsyncMock(return_value="local-handle")
    ta = SimpleNamespace(
        role=TeamRole.LEADER,
        deep_agent=SimpleNamespace(
            ability_manager=SimpleNamespace(
                list=lambda: [SimpleNamespace(id="team.send_message", name="send_message")]
            ),
            card=SimpleNamespace(id="leader-card"),
        ),
        spawn_teammate=original_spawn,
    )

    attach_distributed_local_spawn_guard(ta, session_id="sid", channel_id="web")

    assert getattr(send_message_tool, "_on_teammate_created") is None
    assert getattr(ta, "_jiuwen_distributed_local_spawn_guard_attached") is True
    result = await ta.spawn_teammate(SimpleNamespace(member_name="calculator-1"))
    assert result is None
    original_spawn.assert_not_awaited()


@pytest.mark.asyncio
async def test_spawn_member_wrapper_rebinds_reused_tool_to_latest_team(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.runner import Runner

    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {"team": {"runtime": {"mode": "distributed", "role": "leader"}}},
    )

    class _Result:
        success = True

    class _SpawnMemberTool:
        async def invoke(self, inputs, **kwargs):
            return _Result()

    tool = _SpawnMemberTool()
    resource_mgr = MagicMock()
    resource_mgr.get_tool = MagicMock(return_value=tool)
    monkeypatch.setattr(Runner, "resource_mgr", resource_mgr)

    bootstrap_calls = []

    async def _fake_send_bootstrap(team_agent, session_id, member_name, prompt):
        bootstrap_calls.append((team_agent, session_id, member_name, prompt))
        return True

    monkeypatch.setattr(_mod, "_send_bootstrap_message", _fake_send_bootstrap)

    def _team(name):
        db = MagicMock()
        db.update_member_status = AsyncMock(return_value=True)
        return SimpleNamespace(
            role=TeamRole.LEADER,
            deep_agent=SimpleNamespace(
                ability_manager=SimpleNamespace(
                    list=lambda: [SimpleNamespace(id="team.spawn_member", name="spawn_member")]
                ),
                card=SimpleNamespace(id="leader-card"),
            ),
            team_backend=SimpleNamespace(db=db),
            _team_name=lambda: name,
        )

    old_team = _team("old-team")
    new_team = _team("new-team")

    attach_spawn_member_remote_bootstrap_wrapper(old_team, session_id="old-sid", channel_id="web")
    attach_spawn_member_remote_bootstrap_wrapper(new_team, session_id="new-sid", channel_id="web")

    await tool.invoke({"member_name": "calculator", "prompt": "run calc"})

    assert bootstrap_calls == [(new_team, "new-sid", "calculator", "run calc")]
    old_team.team_backend.db.update_member_status.assert_not_awaited()
    new_team.team_backend.db.update_member_status.assert_any_await("calculator", "new-team", "unstarted")
    new_team.team_backend.db.update_member_status.assert_any_await("calculator", "new-team", "ready")


@pytest.mark.asyncio
async def test_spawn_member_wrapper_ensures_member_row_on_active_team(monkeypatch):
    from openjiuwen.agent_teams.schema.team import TeamRole
    from openjiuwen.core.runner import Runner

    monkeypatch.setattr(
        "jiuwenswarm.common.config.get_config",
        lambda: {"team": {"runtime": {"mode": "distributed", "role": "leader"}}},
    )

    class _Result:
        success = True

    class _SpawnMemberTool:
        async def invoke(self, inputs, **kwargs):
            return _Result()

    tool = _SpawnMemberTool()
    resource_mgr = MagicMock()
    resource_mgr.get_tool = MagicMock(return_value=tool)
    monkeypatch.setattr(Runner, "resource_mgr", resource_mgr)
    monkeypatch.setattr(_mod, "_send_bootstrap_message", AsyncMock(return_value=True))

    db = MagicMock()
    db.update_member_status = AsyncMock(return_value=True)
    team_backend = SimpleNamespace(
        db=db,
        get_member=AsyncMock(return_value=None),
        spawn_member=AsyncMock(return_value=_Result()),
    )
    team_agent = SimpleNamespace(
        role=TeamRole.LEADER,
        deep_agent=SimpleNamespace(
            ability_manager=SimpleNamespace(
                list=lambda: [SimpleNamespace(id="team.spawn_member", name="spawn_member")]
            ),
            card=SimpleNamespace(id="leader-card"),
        ),
        team_backend=team_backend,
        _team_name=lambda: "active-team",
    )

    attach_spawn_member_remote_bootstrap_wrapper(team_agent, session_id="sid", channel_id="web")

    await tool.invoke(
        {
            "member_name": "calculator",
            "display_name": "Calculator",
            "desc": "Does math",
            "prompt": "run calc",
        }
    )

    team_backend.get_member.assert_awaited_once_with("calculator")
    team_backend.spawn_member.assert_awaited_once()
    kwargs = team_backend.spawn_member.await_args.kwargs
    assert kwargs["member_name"] == "calculator"
    assert kwargs["display_name"] == "Calculator"
    assert kwargs["desc"] == "Does math"
    assert kwargs["prompt"] == "run calc"
    db.update_member_status.assert_any_await("calculator", "active-team", "unstarted")
    db.update_member_status.assert_any_await("calculator", "active-team", "ready")


@pytest.mark.asyncio
async def test_bootstrap_allows_later_kickoff_for_same_member_after_task_done(monkeypatch):
    kickoff_calls = []
    card_replace_calls = []

    async def fake_ensure_dynamic_member_execution_loop(**kwargs):
        kickoff_calls.append(kwargs)
        return True, True

    async def fake_replace_card_after_direct_bootstrap(**kwargs):
        card_replace_calls.append(kwargs)
        return True

    monkeypatch.setattr(
        _mod,
        "_ensure_dynamic_member_execution_loop",
        fake_ensure_dynamic_member_execution_loop,
    )
    monkeypatch.setattr(
        _mod,
        "_replace_teammate_card_after_direct_bootstrap",
        fake_replace_card_after_direct_bootstrap,
    )

    processed = set()
    loop_kicked_members = set()
    kickoff_tasks = set()
    envelope = {
        "bootstrap_id": "boot-1",
        "team_name": "jiuwen_team_sess_1",
        "session_id": "sess_1",
        "member_name": "calculator",
        "leader_agent_id": "leader",
        "leader_direct_addr": "tcp://127.0.0.1:28555",
    }
    apply_bootstrap_envelope = getattr(
        _mod,
        "_apply_bootstrap_envelope"
        "_from_control_plane",
    )

    await apply_bootstrap_envelope(
        processed_ids=processed,
        loop_kicked_members=loop_kicked_members,
        kickoff_tasks=kickoff_tasks,
        adopted_member="teammate_1",
        envelope=envelope,
        source_id="src-1",
    )
    await asyncio.gather(*list(kickoff_tasks))
    await asyncio.sleep(0)

    assert len(kickoff_calls) == 1
    assert card_replace_calls == [{"channel_id": "default", "member_name": "calculator"}]
    assert ("sess_1", "calculator") not in loop_kicked_members

    envelope["bootstrap_id"] = "boot-2"
    await apply_bootstrap_envelope(
        processed_ids=processed,
        loop_kicked_members=loop_kicked_members,
        kickoff_tasks=kickoff_tasks,
        adopted_member="calculator",
        envelope=envelope,
        source_id="src-2",
    )
    await asyncio.gather(*list(kickoff_tasks))
    await asyncio.sleep(0)

    assert len(kickoff_calls) == 2
    assert card_replace_calls == [
        {"channel_id": "default", "member_name": "calculator"},
        {"channel_id": "default", "member_name": "calculator"},
    ]


@pytest.mark.asyncio
async def test_replace_teammate_card_after_direct_bootstrap_uses_local_a2x_state(monkeypatch):
    replace_calls = []
    client = SimpleNamespace()
    deep_agent = SimpleNamespace(
        _jiuwen_a2x_client=client,
        _jiuwen_a2x_blank_dataset="team_pool_local",
        _jiuwen_a2x_blank_service_id="sid-local",
    )
    agent = SimpleNamespace(get_instance=lambda: deep_agent)
    agent_manager = SimpleNamespace(
        get_agent_nowait=lambda channel_id: agent,
        get_agent=AsyncMock(return_value=agent),
    )
    server = SimpleNamespace(get_agent_manager=lambda: agent_manager)

    async def fake_replace_teammate_agent_card_after_bootstrap(*args, **kwargs):
        replace_calls.append((args, kwargs))
        return True

    monkeypatch.setattr(
        "jiuwenswarm.server.agent_ws_server.AgentWebSocketServer.get_instance",
        lambda: server,
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.a2x.a2x_registry_runtime.replace_teammate_agent_card_after_bootstrap",
        fake_replace_teammate_agent_card_after_bootstrap,
    )
    replace_teammate_card = getattr(
        _mod,
        "_replace_teammate_card"
        "_after_direct_bootstrap",
    )

    replaced = await replace_teammate_card(channel_id="default", member_name="calculator")

    assert replaced is True
    assert len(replace_calls) == 1
    args, kwargs = replace_calls[0]
    assert args == (client,)
    assert kwargs == {
        "dataset": "team_pool_local",
        "service_id": "sid-local",
        "member_name": "calculator",
        "source": "teammate-direct-bootstrap",
    }


def test_retarget_teammate_direct_addr_allocates_non_default_port(monkeypatch):
    class _Config:
        direct_addr = "tcp://127.0.0.1:16000"

        @staticmethod
        def model_copy(update):
            copied = _Config()
            copied.direct_addr = update["direct_addr"]
            return copied

    class _Context:
        messager_config = _Config()

        @staticmethod
        def model_copy(update):
            copied = _Context()
            copied.messager_config = update["messager_config"]
            return copied

    monkeypatch.setattr(_mod, "_allocate_loopback_direct_addr", lambda: "tcp://127.0.0.1:32123")
    retarget_teammate_direct_addr = getattr(
        _mod,
        "_retarget_teammate"
        "_direct_addr",
    )

    retargeted = retarget_teammate_direct_addr(
        _Context(),
        session_id="sid",
        member_name="calculator",
    )

    assert retargeted.messager_config.direct_addr == "tcp://127.0.0.1:32123"


@pytest.mark.asyncio
async def test_discard_auxiliary_team_agent_removes_cache_and_stops_runtime():
    stop_coordination = AsyncMock()
    stop_messager = AsyncMock()
    helper = SimpleNamespace(
        member_name="team_leader",
        _stop_coordination=stop_coordination,
        _messager=SimpleNamespace(
            _config=SimpleNamespace(direct_addr="tcp://127.0.0.1:28555"),
            stop=stop_messager,
        ),
    )
    team_agents_attr = "_team" "_agents"
    team_manager = SimpleNamespace()
    setattr(team_manager, team_agents_attr, {"sid": helper})
    discard_auxiliary_team_agent = getattr(
        _mod,
        "_discard_auxiliary"
        "_team_agent",
    )

    await discard_auxiliary_team_agent(team_manager, "sid", helper)

    assert getattr(team_manager, team_agents_attr) == {}
    stop_coordination.assert_awaited_once()
    stop_messager.assert_awaited_once()


@pytest.mark.asyncio
async def test_release_a2x_reservations_notifies_remote_teammate_and_does_not_release_from_leader():
    send = AsyncMock()
    register_peer = MagicMock()
    messager = SimpleNamespace(register_peer=register_peer, send=send)
    reservation = SimpleNamespace(
        dataset="team_pool",
        service_id="blank-agent-1",
        endpoint="tcp://127.0.0.1:28610",
        release=AsyncMock(),
        close=AsyncMock(),
    )
    ta = SimpleNamespace(
        spec=SimpleNamespace(
            team_name="jiuwen_team_sess_destroy_1",
            leader=SimpleNamespace(member_name="team_leader"),
        ),
        runtime_context=None,
        _messager=messager,
    )
    setattr(
        ta,
        "_jiuwen_a2x_blank_agent_reservations",
        [("sess_destroy_1", "math-calc-1", reservation)],
    )

    await release_a2x_reservations_for_team(ta)

    send.assert_awaited_once()
    peer_agent_id, event = send.await_args.args
    assert peer_agent_id == "blank-agent-1"
    assert event.event_type == REMOTE_TEAM_DESTROY_DIRECT_EVENT_TYPE
    assert event.payload["envelope"]["type"] == "jiuwen.remote_team_destroy"
    assert event.payload["envelope"]["member_name"] == "math-calc-1"
    assert event.payload["envelope"]["session_id"] == "sess_destroy_1"
    assert event.payload["envelope"]["registry"] == {
        "dataset": "team_pool",
        "service_id": "blank-agent-1",
        "endpoint": "tcp://127.0.0.1:28610",
    }
    reservation.release.assert_not_awaited()
    reservation.close.assert_awaited_once()
    assert getattr(ta, "_jiuwen_a2x_blank_agent_reservations") == []


@pytest.mark.asyncio
async def test_team_destroy_stops_dynamic_member_runtime(monkeypatch):
    destroy_team = AsyncMock(return_value=True)
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.team_manager.get_team_manager",
        lambda channel_id: SimpleNamespace(destroy_team=destroy_team),
    )
    monkeypatch.setattr("jiuwenswarm.common.config.get_config", lambda: {})
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.team.a2x.a2x_registry_runtime.restore_teammate_blank_agent_on_destroy",
        AsyncMock(return_value=True),
    )

    stop_coordination = AsyncMock()
    stop_messager = AsyncMock()
    agent = SimpleNamespace(
        _stop_coordination=stop_coordination,
        _messager=SimpleNamespace(stop=stop_messager),
    )
    dynamic_member_agents = getattr(_mod, "_DYNAMIC_MEMBER" "_AGENTS")
    apply_team_destroy_envelope = getattr(
        _mod,
        "_apply_team_destroy_envelope"
        "_from_control_plane",
    )
    dynamic_member_agents.clear()
    dynamic_member_agents[("sid-1", "calculator")] = agent

    try:
        adopted = await apply_team_destroy_envelope(
            loop_kicked_members={("sid-1", "calculator")},
            kickoff_tasks=set(),
            adopted_member="calculator",
            local_member="blank-agent",
            envelope={
                "team_name": "jiuwen_team_sid-1",
                "session_id": "sid-1",
                "member_name": "calculator",
            },
            source_id="leader",
        )
    finally:
        dynamic_member_agents.clear()

    assert adopted == "blank-agent"
    assert ("sid-1", "calculator") not in dynamic_member_agents
    stop_coordination.assert_awaited_once()
    stop_messager.assert_awaited_once()
    destroy_team.assert_awaited_once_with("sid-1")
