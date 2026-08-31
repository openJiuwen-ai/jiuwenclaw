# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Gateway → Agent HTTP：method 查表、填路径、body/query、身份头、表外 RPC。"""

from __future__ import annotations

import json
from urllib.parse import quote, urlparse

import pytest

from jiuwenswarm.common.e2a.gateway_normalize import e2a_from_agent_fields
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.gateway.routing.agent_rest_map import (
    API_PREFIX,
    REST_ROUTES,
    RestAssemblyError,
    _METHODS_WITHOUT_PARAM_SESSION_ID,
    _PATH_PLACEHOLDER,
    assemble_rest_request,
    normalize_agent_http_base,
)

BASE = "http://127.0.0.1:8766"

PATH_SAMPLES: dict[str, str] = {
    "session_id": "sess_1",
    "name": "agent_a",
    "team_name": "team_a",
    "task_id": "task_1",
    "issue_id": "issue_1",
    "rule_id": "rule_1",
    "override_id": "ovr_1",
}

# 本仓 ReqMethod 有、REST 表没有：只允许走 POST /rpc/{method}，禁止猜路径。
_RPC_ALLOWLIST = frozenset(
    {
        "chat.swarmflow_reply",
        "ssh.relay",
        "config.get",
        "config.set",
        "channel.get",
        "team.session.reset",
        "team.runtime.dissolve",
        "path.get",
        "path.set",
        "logging.set",
        "memory.compute",
        "tts.synthesize",
        "3rdagent.switch",
        "3rdagent.list",
        "skills.enterprise.list",
        # 下面全仓无任何实现（只有ReqMethod枚举），与logging.set同类遗留
        "files.list",
        "files.get",
        "harness.packages.import",
        "harness.packages.export",
    }
)

_SPECIAL_METHODS = frozenset({"chat.send", "chat.resume"})


def _env(
    method: str | ReqMethod,
    *,
    params: dict | None = None,
    session_id: str | None = "sess_1",
    is_stream: bool = False,
    user_id: str | None = "u1",
    request_id: str = "r1",
    channel_id: str = "web",
):
    return e2a_from_agent_fields(
        request_id=request_id,
        channel_id=channel_id,
        session_id=session_id,
        req_method=method,
        params=params,
        is_stream=is_stream,
        user_id=user_id,
    )


def _params_for(path: str) -> dict:
    params = {key: PATH_SAMPLES[key] for key in _PATH_PLACEHOLDER.findall(path)}
    params["limit"] = 3
    return params


@pytest.mark.parametrize(
    ("method", "verb", "path"),
    [(method, verb, path) for method, (verb, path) in REST_ROUTES.items()],
    ids=list(REST_ROUTES),
)
def test_every_rest_route_assembles(method: str, verb: str, path: str):
    assembled = assemble_rest_request(
        _env(method, params=_params_for(path)),
        base_url=BASE,
    )
    assert assembled.used_rpc_fallback is False
    assert assembled.verb == verb
    expected = f"{BASE}{API_PREFIX}" + path.format(
        **{k: quote(v, safe="") for k, v in PATH_SAMPLES.items() if f"{{{k}}}" in path}
    )
    assert assembled.url == expected
    used = set(_PATH_PLACEHOLDER.findall(path))
    remaining: dict = {"limit": 3}
    if "session_id" not in used and method not in _METHODS_WITHOUT_PARAM_SESSION_ID:
        remaining["session_id"] = "sess_1"
    if verb == "GET":
        assert assembled.json_body is None
        assert assembled.query == {k: str(v) for k, v in remaining.items()}
        assert assembled.headers["Accept"] == "application/json"
        assert "Content-Type" not in assembled.headers
    else:
        assert assembled.query is None
        assert assembled.json_body == remaining
        assert assembled.headers["Content-Type"] == "application/json"
        for key in used:
            assert key not in assembled.json_body
    assert assembled.headers["X-Request-Id"] == "r1"
    assert assembled.headers["X-Channel-Id"] == "web"
    assert assembled.headers["X-Session-Id"] == "sess_1"
    assert assembled.headers["X-User-Id"] == "u1"
    if assembled.json_body is not None:
        assert "method" not in assembled.json_body
        assert "request_id" not in assembled.json_body
        assert "channel" not in assembled.json_body


@pytest.mark.parametrize("req_method", list(ReqMethod), ids=lambda m: m.value)
def test_every_reqmethod_is_rest_or_documented_rpc(req_method: ReqMethod):
    method = req_method.value
    placeholders = REST_ROUTES.get(method, ("POST", ""))[1]
    assembled = assemble_rest_request(
        _env(method, params=_params_for(placeholders) if placeholders else dict(PATH_SAMPLES)),
        base_url=BASE,
    )
    if method in REST_ROUTES or method in _SPECIAL_METHODS:
        assert assembled.used_rpc_fallback is False, method
        return
    assert method in _RPC_ALLOWLIST, f"新增 method {method}：补 REST 表或写入 _RPC_ALLOWLIST"
    assert assembled.used_rpc_fallback is True
    assert assembled.verb == "POST"
    assert assembled.url.endswith(f"/rpc/{method}")


def test_chat_send_and_resume_ignore_stream_flag_for_path():
    send_stream = assemble_rest_request(
        _env(ReqMethod.CHAT_SEND, params={"query": "hi"}, is_stream=True),
        base_url=BASE,
    )
    send_unary = assemble_rest_request(
        _env(ReqMethod.CHAT_SEND, params={"query": "hi"}, is_stream=False),
        base_url=BASE,
    )
    resume = assemble_rest_request(
        _env(ReqMethod.CHAT_RESUME, params={"query": "hi"}, is_stream=True),
        base_url=BASE,
    )
    assert send_stream.url.endswith("/chat/completions")
    assert send_unary.url.endswith("/chat/completions")
    assert send_stream.headers["Accept"] == "text/event-stream"
    assert send_unary.headers["Accept"] == "application/json"
    assert resume.url.endswith("/chat/resume")
    assert send_stream.used_rpc_fallback is False


def test_history_get_stream_switches_path_unary_keeps_history():
    stream = assemble_rest_request(
        _env(ReqMethod.HISTORY_GET, params={"limit": 20}, is_stream=True),
        base_url=BASE,
    )
    unary = assemble_rest_request(
        _env(ReqMethod.HISTORY_GET, params={"limit": 20}, is_stream=False),
        base_url=BASE,
    )
    assert stream.verb == "GET"
    assert stream.url.endswith("/sessions/sess_1/history/stream")
    assert stream.query == {"limit": "20"}
    assert stream.json_body is None
    assert unary.url.endswith("/sessions/sess_1/history")
    assert "/stream" not in unary.url


@pytest.mark.parametrize(
    ("alias", "canonical"),
    [
        ("agents.tools_list", "agents.tools.list"),
        ("agent.prewarm_sync", "agent.prewarm.sync"),
        ("issue.state_list", "issue.state.list"),
        ("skills.online.search", "skills.online_search.search"),
    ],
)
def test_method_aliases_share_path(alias: str, canonical: str):
    params = _params_for(REST_ROUTES[canonical][1])
    left = assemble_rest_request(_env(alias, params=params), base_url=BASE)
    right = assemble_rest_request(_env(canonical, params=params), base_url=BASE)
    assert left.verb == right.verb
    assert left.url == right.url
    assert left.used_rpc_fallback is False
    assert right.used_rpc_fallback is False


def test_skills_static_names_are_not_skills_get():
    clawhub = assemble_rest_request(_env("skills.clawhub.get_token", params={}), base_url=BASE)
    get_named = assemble_rest_request(
        _env("skills.get", params={"name": "clawhub"}),
        base_url=BASE,
    )
    assert clawhub.url.endswith("/skills/clawhub/token")
    assert get_named.url.endswith("/skills/clawhub")
    assert clawhub.url != get_named.url


def test_session_id_from_envelope_when_params_omit_it():
    assembled = assemble_rest_request(
        _env(ReqMethod.SESSION_RENAME, params={"title": "n"}, session_id="from-env"),
        base_url=BASE,
    )
    assert assembled.url.endswith("/sessions/from-env")
    assert assembled.json_body == {"title": "n"}


def test_params_session_id_wins_over_envelope():
    assembled = assemble_rest_request(
        _env(
            ReqMethod.SESSION_RENAME,
            params={"session_id": "from-params", "title": "n"},
            session_id="from-env",
        ),
        base_url=BASE,
    )
    assert assembled.url.endswith("/sessions/from-params")


def test_path_placeholder_is_percent_encoded():
    assembled = assemble_rest_request(
        _env(ReqMethod.SESSION_DELETE, params={}, session_id="sess/a b"),
        base_url=BASE,
    )
    assert assembled.url.endswith("/sessions/sess%2Fa%20b")


def test_missing_path_placeholder_raises_and_does_not_emit_half_url():
    with pytest.raises(RestAssemblyError, match="session_id"):
        assemble_rest_request(
            _env(ReqMethod.SESSION_DELETE, params={}, session_id=None),
            base_url=BASE,
        )


def test_team_bind_missing_one_of_two_placeholders_raises():
    with pytest.raises(RestAssemblyError, match="team_name"):
        assemble_rest_request(
            _env(
                ReqMethod.TEAM_SESSION_BIND,
                params={"session_id": "s1"},
                session_id="s1",
            ),
            base_url=BASE,
        )


def test_empty_method_raises():
    env = e2a_from_agent_fields(
        request_id="r1",
        channel_id="web",
        req_method=None,
        params={},
    )
    with pytest.raises(RestAssemblyError, match="method"):
        assemble_rest_request(env, base_url=BASE)


def test_unknown_method_rpc_keeps_dots_and_puts_remaining_in_body():
    assembled = assemble_rest_request(
        _env("file.transfer.start", params={"chunk": 1}, session_id="s1"),
        base_url=BASE,
    )
    assert assembled.used_rpc_fallback is True
    assert assembled.url.endswith("/rpc/file.transfer.start")
    assert assembled.json_body == {"chunk": 1, "session_id": "s1"}


def test_get_nested_params_are_json_in_query():
    assembled = assemble_rest_request(
        _env(ReqMethod.SESSION_LIST, params={"filter": {"a": 1}, "tags": ["x"]}, session_id=None),
        base_url=BASE,
    )
    assert assembled.query is not None
    assert json.loads(assembled.query["filter"]) == {"a": 1}
    assert json.loads(assembled.query["tags"]) == ["x"]


def test_none_params_are_dropped():
    assembled = assemble_rest_request(
        _env(ReqMethod.SESSION_CREATE, params={"title": "t", "hint": None}),
        base_url=BASE,
    )
    # session.create 不把信封 session_id 写入 body；None 字段仍丢弃。
    assert assembled.json_body == {"title": "t"}


def test_identity_headers_omit_optional_ids():
    assembled = assemble_rest_request(
        _env(ReqMethod.SESSION_LIST, params={}, session_id=None, user_id=None, channel_id=""),
        base_url=BASE,
    )
    assert assembled.headers["X-Channel-Id"] == "web"
    assert "X-Session-Id" not in assembled.headers
    assert "X-User-Id" not in assembled.headers
    assert "X-Bot-Id" not in assembled.headers
    assert "X-Group-Id" not in assembled.headers
    assert "X-Gateway-Id" not in assembled.headers


def test_identity_headers_carry_routing_from_channel_context():
    """REST body 不含 E2A；routing 必须经 X-* 头传到 Agent。"""
    env = _env(
        ReqMethod.COMMAND_GOAL,
        params={"session_id": "sess_1", "action": "get", "mode": "agent"},
        user_id="user1",
    )
    env.channel_context = {
        "user_id": "user1",
        "routing": {
            "group_id": "__none__",
            "bot_id": "d64efe50-3b44-4895-b040-df922e1df242",
            "gateway_id": "4e3a795a-2339-4efd-895f-bc796943f57c",
        },
        "method": "command.goal",
    }
    assembled = assemble_rest_request(env, base_url=BASE)
    assert assembled.url.endswith("/sessions/sess_1/commands/goal")
    assert assembled.json_body == {
        "action": "get",
        "mode": "agent",
    }
    assert assembled.headers["X-User-Id"] == "user1"
    assert assembled.headers["X-Group-Id"] == "__none__"
    assert assembled.headers["X-Bot-Id"] == "d64efe50-3b44-4895-b040-df922e1df242"
    assert assembled.headers["X-Gateway-Id"] == "4e3a795a-2339-4efd-895f-bc796943f57c"


def test_normalize_base_empty_raises():
    with pytest.raises(RestAssemblyError, match="空"):
        normalize_agent_http_base("  ")


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("http://h:8766", "http://h:8766/api/v1"),
        ("http://h:8766/", "http://h:8766/api/v1"),
        ("http://h:8766/api/v1", "http://h:8766/api/v1"),
        ("http://h:8766/api/v1/", "http://h:8766/api/v1"),
        ("https://h:443/api/v1", "https://h:443/api/v1"),
    ],
)
def test_normalize_agent_http_base(raw: str, expected: str):
    assert normalize_agent_http_base(raw) == expected


def test_assembled_url_keeps_api_prefix_segment():
    assembled = assemble_rest_request(_env(ReqMethod.SESSION_LIST, params={}), base_url=BASE)
    parsed = urlparse(assembled.url)
    assert parsed.path.startswith("/api/v1/")
