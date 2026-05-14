from __future__ import annotations

import json
import sys
import importlib.util
from pathlib import Path
from types import ModuleType, SimpleNamespace
from unittest.mock import AsyncMock

import pytest

pytestmark = [pytest.mark.integration, pytest.mark.system]

_BOOTSTRAP_PATH = (
        Path(__file__).resolve().parents[2]
        / "jiuwenclaw"
        / "agents"
        / "harness"
        / "team"
        / "remote_member_bootstrap.py"
)
_BOOTSTRAP_SPEC = importlib.util.spec_from_file_location(
    "test_remote_member_bootstrap_module",
    _BOOTSTRAP_PATH,
)
assert _BOOTSTRAP_SPEC is not None and _BOOTSTRAP_SPEC.loader is not None
bootstrap_module = importlib.util.module_from_spec(_BOOTSTRAP_SPEC)
_BOOTSTRAP_SPEC.loader.exec_module(bootstrap_module)


def _install_fake_openjiuwen_schema(monkeypatch: pytest.MonkeyPatch) -> None:
    import openjiuwen.agent_teams.schema.events as real_events
    import openjiuwen.agent_teams.schema.team as real_team

    events_module = ModuleType("openjiuwen.agent_teams.schema.events")
    events_module.__dict__.update(real_events.__dict__)

    team_module = ModuleType("openjiuwen.agent_teams.schema.team")
    team_module.__dict__.update(real_team.__dict__)

    monkeypatch.setitem(sys.modules, "openjiuwen.agent_teams.schema.events", events_module)
    monkeypatch.setitem(sys.modules, "openjiuwen.agent_teams.schema.team", team_module)


def _bootstrap_event(*, message_id: str, from_member: str, to_member: str) -> SimpleNamespace:
    return SimpleNamespace(
        event_type="message",
        payload={
            "message_id": message_id,
            "from_member_name": from_member,
            "to_member_name": to_member,
        },
    )


def _bootstrap_envelope_json(*, member_name: str, dataset: str, service_id: str) -> str:
    return json.dumps(
        {
            "type": "jiuwen.remote_teammate_bootstrap",
            "version": 1,
            "member_name": member_name,
            "leader_member_name": "leader_1",
            "leader_agent_id": "leader-agent-id",
            "leader_direct_addr": "tcp://127.0.0.1:18555",
            # These two fields are intentionally present but should be ignored
            # since runtime now reads dataset/service_id only from local deep_agent.
            "a2x_dataset": dataset,
            "a2x_service_id": service_id,
        },
        ensure_ascii=False,
    )


def _make_team_agent(
        *,
        deep_agent: object,
        envelope_content: str,
        target_member: str,
) -> tuple[SimpleNamespace, list]:
    listeners: list = []
    mm = SimpleNamespace(
        mark_message_read=AsyncMock(),
        send_message=AsyncMock(return_value="ack-1"),
    )
    team_agent = SimpleNamespace(
        role="teammate",
        deep_agent=deep_agent,
        _member_name=lambda: "teammate_local",
        _team_name=lambda: "team_demo",
        _messager=SimpleNamespace(register_peer=lambda _cfg: None),
        team_backend=SimpleNamespace(
            db=SimpleNamespace(
                get_message=AsyncMock(return_value=SimpleNamespace(content=envelope_content))
            )
        ),
        message_manager=mm,
    )

    def _add_event_listener(cb):
        listeners.append(cb)

    team_agent.add_event_listener = _add_event_listener
    return team_agent, listeners


@pytest.mark.asyncio
async def test_teammate_bootstrap_replaces_card_using_local_dataset_service_id(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openjiuwen_schema(monkeypatch)
    monkeypatch.setattr(bootstrap_module, "processed_message_ids", set(), raising=False)
    monkeypatch.setattr(
        "jiuwenclaw.common.config.get_config",
        lambda: {"team": {"runtime": {"mode": "distributed", "role": "teammate"}}},
    )
    monkeypatch.setattr(bootstrap_module, "_apply_leader_route_from_envelope", lambda *_a, **_k: True)

    client = SimpleNamespace(replace_agent_card=AsyncMock(return_value=SimpleNamespace(service_id="sid-local")))
    deep_agent = SimpleNamespace(
        _jiuwen_a2x_client=client,
        _jiuwen_a2x_blank_dataset="team_pool_local",
        _jiuwen_a2x_blank_service_id="sid-local",
    )
    target_member = "teammate_1"
    team_agent, listeners = _make_team_agent(
        deep_agent=deep_agent,
        envelope_content=_bootstrap_envelope_json(
            member_name=target_member,
            dataset="envelope_ds_should_be_ignored",
            service_id="envelope_sid_should_be_ignored",
        ),
        target_member=target_member,
    )

    bootstrap_module.attach_remote_teammate_bootstrap_listener(
        team_agent,
        session_id="sess_bootstrap_replace",
    )
    assert len(listeners) == 1

    await listeners[0](
        _bootstrap_event(
            message_id="msg-1",
            from_member="leader_1",
            to_member=target_member,
        )
    )

    client.replace_agent_card.assert_awaited_once()
    call_args = client.replace_agent_card.await_args
    assert call_args.args[0] == "team_pool_local"
    assert call_args.args[1] == "sid-local"
    assert call_args.args[2]["name"] == target_member


@pytest.mark.asyncio
async def test_teammate_bootstrap_raises_when_local_dataset_service_id_missing(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openjiuwen_schema(monkeypatch)
    monkeypatch.setattr(bootstrap_module, "processed_message_ids", set(), raising=False)
    monkeypatch.setattr(
        "jiuwenclaw.common.config.get_config",
        lambda: {"team": {"runtime": {"mode": "distributed", "role": "teammate"}}},
    )
    monkeypatch.setattr(bootstrap_module, "_apply_leader_route_from_envelope", lambda *_a, **_k: True)

    client = SimpleNamespace(replace_agent_card=AsyncMock())
    deep_agent = SimpleNamespace(
        _jiuwen_a2x_client=client,
        _jiuwen_a2x_blank_dataset="",
        _jiuwen_a2x_blank_service_id="",
    )
    target_member = "teammate_1"
    team_agent, listeners = _make_team_agent(
        deep_agent=deep_agent,
        envelope_content=_bootstrap_envelope_json(
            member_name=target_member,
            dataset="ignored_ds",
            service_id="ignored_sid",
        ),
        target_member=target_member,
    )

    bootstrap_module.attach_remote_teammate_bootstrap_listener(team_agent, session_id="sess_missing_ids")
    assert len(listeners) == 1

    with pytest.raises(ValueError, match="missing required dataset/service_id/member_name"):
        await listeners[0](
            _bootstrap_event(
                message_id="msg-2",
                from_member="leader_1",
                to_member=target_member,
            )
        )


@pytest.mark.asyncio
async def test_teammate_bootstrap_raises_when_local_a2x_client_missing(
        monkeypatch: pytest.MonkeyPatch,
) -> None:
    _install_fake_openjiuwen_schema(monkeypatch)
    monkeypatch.setattr(bootstrap_module, "processed_message_ids", set(), raising=False)
    monkeypatch.setattr(
        "jiuwenclaw.common.config.get_config",
        lambda: {"team": {"runtime": {"mode": "distributed", "role": "teammate"}}},
    )
    monkeypatch.setattr(bootstrap_module, "_apply_leader_route_from_envelope", lambda *_a, **_k: True)

    deep_agent = SimpleNamespace(
        _jiuwen_a2x_client=None,
        _jiuwen_a2x_blank_dataset="team_pool_local",
        _jiuwen_a2x_blank_service_id="sid-local",
    )
    target_member = "teammate_1"
    team_agent, listeners = _make_team_agent(
        deep_agent=deep_agent,
        envelope_content=_bootstrap_envelope_json(
            member_name=target_member,
            dataset="ignored_ds",
            service_id="ignored_sid",
        ),
        target_member=target_member,
    )

    bootstrap_module.attach_remote_teammate_bootstrap_listener(team_agent, session_id="sess_missing_client")
    assert len(listeners) == 1

    with pytest.raises(RuntimeError, match="missing A2X client"):
        await listeners[0](
            _bootstrap_event(
                message_id="msg-3",
                from_member="leader_1",
                to_member=target_member,
            )
        )
