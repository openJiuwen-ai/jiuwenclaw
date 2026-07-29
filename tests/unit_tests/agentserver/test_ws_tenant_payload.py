"""Tests for WS tenant field parsing and extract_ids normalization."""

from jiuwenclaw.agentserver.agent_ws_server import _payload_to_request
from jiuwenclaw.agentserver.tenant_agent_pool import TenantAgentPool
from jiuwenclaw.schema.agent import AgentRequest
from jiuwenclaw.schema.message import ReqMethod


def _make_request(**kwargs) -> AgentRequest:
    defaults = {
        "request_id": "req-1",
        "channel_id": "web",
        "session_id": "sess-1",
        "req_method": ReqMethod.CHAT_SEND,
        "params": {},
        "is_stream": False,
        "timestamp": 0.0,
    }
    defaults.update(kwargs)
    return AgentRequest(**defaults)


class TestResolveControlRpcTenant:
    def test_remaps_web_rebuild_when_default_tip_lacks_api_base(self, monkeypatch):
        tips = {
            ("default", "default"): {"MODEL_NAME": "glm-5.2"},
            ("default", "office"): {"MODEL_NAME": "glm-5.2", "API_BASE": "https://llm.example/v1"},
        }

        class _FakeRegistry:
            def list_ids(self, service_id="default"):
                return ["assistant", "office", "expert-architecture"]

        monkeypatch.setattr(
            "jiuwenclaw.agentserver.tenant_agent_pool.effective_tip",
            lambda sid, aid: dict(tips.get((sid, aid), {})),
        )
        monkeypatch.setattr(
            "jiuwenclaw.agentserver.tenant_agent_pool.TenantCatalogRegistry.get_instance",
            lambda: _FakeRegistry(),
        )
        req = _make_request(
            channel_id="web",
            req_method=ReqMethod.SKILLS_EVOLUTION_REBUILD,
            agent_id=None,
            service_id=None,
        )
        assert TenantAgentPool.resolve_control_rpc_tenant(req, "default", "default") == (
            "office",
            "default",
        )

    def test_keeps_default_when_tip_already_has_api_base(self, monkeypatch):
        monkeypatch.setattr(
            "jiuwenclaw.agentserver.tenant_agent_pool.effective_tip",
            lambda sid, aid: {"MODEL_NAME": "glm-5.2", "API_BASE": "https://llm.example/v1"},
        )
        req = _make_request(
            channel_id="web",
            req_method=ReqMethod.SKILLS_EVOLUTION_REBUILD,
        )
        assert TenantAgentPool.resolve_control_rpc_tenant(req, "default", "default") == (
            "default",
            "default",
        )


class TestExtractIdsNormalize:
    def test_service_id_missing(self):
        req = _make_request(agent_id="office")
        assert TenantAgentPool.extract_ids(req) == ("office", "default")

    def test_service_id_empty_string(self):
        req = _make_request(agent_id="office", service_id="")
        assert TenantAgentPool.extract_ids(req) == ("office", "default")

    def test_service_id_whitespace(self):
        req = _make_request(agent_id="office", service_id="  ")
        assert TenantAgentPool.extract_ids(req) == ("office", "default")

    def test_service_id_explicit_default(self):
        req = _make_request(agent_id="office", service_id="default")
        assert TenantAgentPool.extract_ids(req) == ("office", "default")

    def test_acp_channel(self):
        req = _make_request(channel_id="acp", agent_id="ignored", service_id="ignored")
        assert TenantAgentPool.extract_ids(req) == ("acp", "global_acp")

    def test_explicit_default_default(self):
        req = _make_request(agent_id="default", service_id="default")
        assert TenantAgentPool.extract_ids(req) == ("default", "default")


class TestPayloadToRequest:
    def test_parses_tenant_fields(self):
        data = {
            "request_id": "r1",
            "channel_id": "web",
            "agent_id": "office",
            "service_id": "",
        }
        req = _payload_to_request(data)
        assert req.agent_id == "office"
        assert req.service_id == ""
        assert req.chat_id is None
        assert TenantAgentPool.extract_ids(req) == ("office", "default")

    def test_reload_payload_inherits_tenant_fields(self):
        data = {
            "request_id": "reload-1",
            "channel_id": "web",
            "req_method": "agent.reload_config",
            "agent_id": "assistant",
            "params": {"config": {}, "env": {}},
        }
        req = _payload_to_request(data)
        assert req.agent_id == "assistant"
        assert TenantAgentPool.extract_ids(req) == ("assistant", "default")
