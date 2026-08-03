# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""SessionMap ``Session`` rows + unified runtime invoke-id helpers."""

from __future__ import annotations

import json

import pytest

from jiuwenclaw.extensions.yuanrong_frontend_client import YuanrongFrontendAgentClient
from jiuwenclaw.gateway.session_map import (
    SessionMap,
    SessionMapScope,
    get_session_map_path,
    invoke_ids_from_identity,
    invoke_ids_from_session_id_string,
    invoke_service_id,
    migrate_legacy_session_map_if_needed,
)
from jiuwenclaw.schema.agent import AgentRequest


@pytest.fixture
def gateway_tmp(monkeypatch, tmp_path):
    gateway_dir = tmp_path / ".gateway"
    gateway_dir.mkdir(parents=True)
    monkeypatch.setattr(
        "jiuwenclaw.gateway.session_map.get_session_map_path",
        lambda: gateway_dir / "session_map.json",
    )
    return gateway_dir


def test_invoke_ids_from_identity() -> None:
    sid = invoke_service_id("c1", "b1")
    s, a = invoke_ids_from_identity("c1", "b1", "u1", SessionMapScope.PER_CHAT_BOT)
    assert s == sid and a is None
    s2, a2 = invoke_ids_from_identity("c1", "b1", "u1", SessionMapScope.PER_CHAT_BOT_USER)
    assert s2 == sid and a2 == "u1"


def test_invoke_ids_from_session_id_string_shapes() -> None:
    p, c, b, u = "feishu", "chat1", "bot1", "user1"
    five = f"{p}::{c}::{b}::1a2b3c::deadbe"
    s5, a5 = invoke_ids_from_session_id_string(five)
    assert a5 is None
    assert s5 == invoke_service_id(c, b)
    six = f"{p}::{c}::{b}::{u}::1a2b3c::deadbe"
    s6, a6 = invoke_ids_from_session_id_string(six)
    assert a6 == u
    assert s6 == invoke_service_id(c, b)


def test_session_map_legacy_string_json(gateway_tmp) -> None:
    legacy_sid = "prov::chatA::botB::abc::def12"
    key = "prov::chatA::botB"
    path = gateway_tmp / "session_map.json"
    path.write_text(json.dumps({key: legacy_sid}, ensure_ascii=False), encoding="utf-8")

    m = SessionMap(scope=SessionMapScope.PER_CHAT_BOT)
    s = m.get_session("prov", "chatA", "botB", "userIgnored")
    assert s.session_id == legacy_sid
    exp_svc, _ = invoke_ids_from_session_id_string(legacy_sid)
    assert s.service_id == exp_svc
    assert s.agent_id is None


def test_session_map_dict_record_json(gateway_tmp) -> None:
    legacy_sid = "prov::chatA::botB::abc::def12"
    key = "prov::chatA::botB"
    svc = invoke_service_id("chatA", "botB")
    path = gateway_tmp / "session_map.json"
    path.write_text(
        json.dumps(
            {key: {"session_id": legacy_sid, "service_id": svc, "agent_id": None}},
            ensure_ascii=False,
        ),
        encoding="utf-8",
    )
    m = SessionMap(scope=SessionMapScope.PER_CHAT_BOT)
    s = m.get_session("prov", "chatA", "botB", "u")
    assert s.session_id == legacy_sid
    assert s.service_id == svc
    assert s.agent_id is None


def test_session_map_get_session_and_rotate(gateway_tmp) -> None:
    m = SessionMap(scope=SessionMapScope.PER_CHAT_BOT_USER)
    a = m.get_session("p", "c", "b", "u", rotate=False)
    b = m.get_session("p", "c", "b", "u", rotate=False)
    assert a.session_id == b.session_id
    assert a.service_id == b.service_id == invoke_service_id("c", "b")
    assert a.agent_id == "u"
    c = m.get_session("p", "c", "b", "u", rotate=True)
    assert c.session_id != a.session_id
    assert c.service_id == a.service_id
    assert c.agent_id == "u"
    assert (gateway_tmp / "session_map.json").exists()


def test_session_map_migrates_from_checkpoint_legacy(tmp_path, monkeypatch) -> None:
    legacy_dir = tmp_path / "agent_default" / ".checkpoint"
    legacy_dir.mkdir(parents=True)
    gateway_dir = tmp_path / ".gateway"
    key = "prov::chatA::botB"
    legacy_sid = "prov::chatA::botB::abc::def12"
    (legacy_dir / "session_map.json").write_text(
        json.dumps({key: legacy_sid}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenclaw.gateway.session_map.get_checkpoint_dir",
        lambda: legacy_dir,
    )
    monkeypatch.setattr(
        "jiuwenclaw.gateway.session_map.get_session_map_path",
        lambda: gateway_dir / "session_map.json",
    )

    migrate_legacy_session_map_if_needed()
    assert (gateway_dir / "session_map.json").exists()

    m = SessionMap(scope=SessionMapScope.PER_CHAT_BOT)
    s = m.get_session("prov", "chatA", "botB", "u")
    assert s.session_id == legacy_sid


def test_session_map_does_not_migrate_on_construct(tmp_path, monkeypatch) -> None:
    """SessionMap construction must not relocate legacy files; startup migrate owns that."""
    legacy_dir = tmp_path / "agent_default" / ".checkpoint"
    legacy_dir.mkdir(parents=True)
    gateway_dir = tmp_path / ".gateway"
    (legacy_dir / "session_map.json").write_text(
        json.dumps({"k": "prov::c::b::1::2"}, ensure_ascii=False),
        encoding="utf-8",
    )
    monkeypatch.setattr(
        "jiuwenclaw.gateway.session_map.get_checkpoint_dir",
        lambda: legacy_dir,
    )
    monkeypatch.setattr(
        "jiuwenclaw.gateway.session_map.get_session_map_path",
        lambda: gateway_dir / "session_map.json",
    )

    SessionMap(scope=SessionMapScope.PER_CHAT_BOT)
    assert not (gateway_dir / "session_map.json").exists()


def test_get_session_map_path_under_gateway(monkeypatch, tmp_path) -> None:
    monkeypatch.setattr(
        "jiuwenclaw.utils.get_user_workspace_dir",
        lambda: tmp_path,
    )
    # get_session_map_path imports get_gateway_dir from utils at call time via module binding
    monkeypatch.setattr(
        "jiuwenclaw.gateway.session_map.get_gateway_dir",
        lambda: tmp_path / ".gateway",
    )
    path = get_session_map_path()
    assert path == tmp_path / ".gateway" / "session_map.json"


def test_yuanrong_client_requires_envelope_service_id() -> None:
    req = AgentRequest(
        request_id="1",
        channel_id="feishu",
        session_id="opaque",
        service_id="svc1",
        agent_id="ag1",
    )
    assert YuanrongFrontendAgentClient.invoke_ids_from_agent_request(req) == ("svc1", "ag1")

    req_empty_agent = AgentRequest(
        request_id="2",
        session_id="x",
        service_id="svc2",
        agent_id="   ",
    )
    assert YuanrongFrontendAgentClient.invoke_ids_from_agent_request(req_empty_agent) == ("svc2", None)

    req_no_svc = AgentRequest(request_id="3", session_id="x", service_id="", agent_id="ag")
    with pytest.raises(ValueError, match="service_id"):
        YuanrongFrontendAgentClient.invoke_ids_from_agent_request(req_no_svc)
