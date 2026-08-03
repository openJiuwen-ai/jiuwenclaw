# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from jiuwenclaw.agentserver.runtime_scope import RuntimeScopeKey


class TestRuntimeScopeKey:
    def test_defaults(self) -> None:
        key = RuntimeScopeKey()
        assert key.tenant() == ("default", "default")
        assert key.session_key() == ("default", "default", "")

    def test_from_ids_normalizes_empty(self) -> None:
        key = RuntimeScopeKey.from_ids("", "  ", "sess-1")
        assert key.service_id == "default"
        assert key.agent_id == "default"
        assert key.session_id == "sess-1"

    def test_from_ids_without_session(self) -> None:
        key = RuntimeScopeKey.from_ids("svc", "aid")
        assert key.session_id == ""
        assert key.tenant() == ("svc", "aid")

    def test_with_session(self) -> None:
        base = RuntimeScopeKey.from_ids("s1", "a1")
        scoped = base.with_session("sess-x")
        assert scoped.tenant() == ("s1", "a1")
        assert scoped.session_id == "sess-x"
        assert base.session_id == ""

    def test_from_request_include_session(self) -> None:
        class _Req:
            service_id = "svc"
            agent_id = "aid"
            session_id = "sess"
            channel_id = "default"

        key = RuntimeScopeKey.from_request(_Req(), include_session=True)
        assert key.session_key() == ("svc", "aid", "sess")

        key_no_sess = RuntimeScopeKey.from_request(_Req(), include_session=False)
        assert key_no_sess.session_id == ""

    def test_from_request_missing_ids_default(self) -> None:
        class _Req:
            service_id = None
            agent_id = None
            session_id = "s"
            channel_id = "default"

        key = RuntimeScopeKey.from_request(_Req(), include_session=True)
        assert key.tenant() == ("default", "default")
        assert key.session_id == "s"

    def test_from_request_acp_channel(self) -> None:
        class _Req:
            service_id = None
            agent_id = None
            session_id = "s"
            channel_id = "acp"

        key = RuntimeScopeKey.from_request(_Req(), include_session=True)
        assert key.tenant() == ("global_acp", "acp")
        assert key.session_id == "s"

    def test_from_adapter(self) -> None:
        class _Adapter:
            _env_service_id = "es"
            _env_agent_id = "ea"
            _service_id = "ignored"
            _agent_id = "ignored"

        key = RuntimeScopeKey.from_adapter(_Adapter(), session_id="sess")
        assert key.session_key() == ("es", "ea", "sess")
