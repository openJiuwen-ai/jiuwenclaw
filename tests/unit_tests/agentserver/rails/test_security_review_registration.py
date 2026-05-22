# coding: utf-8
from __future__ import annotations

import warnings
from unittest.mock import patch

import pytest

warnings.filterwarnings(
    "ignore",
    message="Pandas requires version .*",
    category=UserWarning,
)

from jiuwenswarm.agents.harness.common.rails import SecurityReviewAndSkillRail
from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenClawDeepAdapter


def test_security_review_rail_disabled_by_default():
    adapter = JiuWenClawDeepAdapter()

    assert adapter._build_security_review_rail({}) is None


def test_security_review_rail_uses_react_config_when_enabled():
    adapter = JiuWenClawDeepAdapter()

    rail = adapter._build_security_review_rail(
        {
            "security_review": {
                "enabled": True,
                "repeated_tool_failure_threshold": 3,
                "runtime_advice": False,
            }
        }
    )

    assert isinstance(rail, SecurityReviewAndSkillRail)
    assert rail.config.repeated_tool_failure_threshold == 3
    assert rail.config.runtime_advice is False


def test_security_review_rail_enabled_env_overrides_react_config(monkeypatch):
    adapter = JiuWenClawDeepAdapter()
    monkeypatch.setenv("SECURITY_REVIEW_ENABLED", "true")

    rail = adapter._build_security_review_rail({"security_review": {"enabled": False}})

    assert isinstance(rail, SecurityReviewAndSkillRail)
    assert rail.config.enabled is True


def test_build_agent_rails_registers_security_review_when_enabled():
    adapter = JiuWenClawDeepAdapter()
    security_review_rail = object()

    with (
        patch.object(adapter, "_filesystem_rail_enabled_for_profile", return_value=False),
        patch.object(adapter, "_skill_include_tools_for_profile", return_value=False),
        patch.object(adapter, "_build_runtime_prompt_rail", return_value=None),
        patch.object(adapter, "_build_response_prompt_rail", return_value=None),
        patch.object(adapter, "_build_skill_rail", return_value=None),
        patch.object(adapter, "_build_stream_event_rail", return_value=None),
        patch.object(adapter, "_build_task_planning_rail", return_value=None),
        patch.object(adapter, "_build_security_rail", return_value=None),
        patch.object(
            adapter, "_build_security_review_rail", return_value=security_review_rail
        ) as build_security_review,
        patch.object(adapter, "_build_heartbeat_rail", return_value=None),
        patch.object(adapter, "_build_avatar_rail", return_value=None),
        patch.object(adapter, "_build_subagent_rail", return_value=None),
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep.build_permission_rail",
            return_value=None,
        ),
        patch(
            "jiuwenswarm.server.runtime.agent_adapter.interface_deep._build_context_processor_rail",
            return_value=None,
        ),
    ):
        rails = adapter._build_agent_rails(
            {"security_review": {"enabled": True}},
            {"models": {"default": {"model_client_config": {"model_name": "test-model"}}}},
        )

    assert rails == [security_review_rail]
    assert adapter._security_review_rail is security_review_rail
    build_security_review.assert_called_once_with(config={"security_review": {"enabled": True}})


def test_security_review_candidate_chunks_are_approval_events():
    adapter = JiuWenClawDeepAdapter()
    candidate = {"type": "security_skill", "requires_approval": True}

    chunks = adapter._security_review_candidates_to_chunks([candidate])

    assert chunks[0]["event_type"] == "chat.ask_user_question"
    assert chunks[0]["request_id"].startswith("security_review_")
    assert "安全演进审批" in chunks[0]["questions"][0]["header"]


@pytest.mark.asyncio
async def test_security_review_watcher_pushes_candidates_after_background_review(monkeypatch):
    adapter = JiuWenClawDeepAdapter()
    pushed = []

    class _FakeTransport:
        async def send_push(self, msg):
            pushed.append(msg)

    class _FakeRail:
        async def wait_for_background_reviews(self):
            return None

        def drain_candidates(self, *, session_id=None):
            assert session_id == "sess-1"
            return [{"type": "security_note", "requires_approval": True}]

    monkeypatch.setattr(
        "jiuwenswarm.server.gateway_push.WebSocketGatewayPushTransport",
        _FakeTransport,
    )
    adapter._security_review_rail = _FakeRail()

    await adapter._watch_security_review_and_push("rid-1", "cid-1", "sess-1")

    assert pushed == [
        {
            "request_id": "rid-1",
            "channel_id": "cid-1",
            "session_id": "sess-1",
            "payload": {
                "event_type": "chat.ask_user_question",
                "request_id": next(iter(adapter._security_review_pending_candidates)),
                "questions": [
                    {
                        "header": "安全演进审批",
                        "question": (
                            "检测到安全自演进候选：\n\n"
                            "类型：security_note\n"
                            '内容：{"type": "security_note", "requires_approval": true}'
                        ),
                        "options": [
                            {"label": "接收", "description": "保留此安全演进候选"},
                            {"label": "拒绝", "description": "丢弃此安全演进候选"},
                        ],
                        "multi_select": False,
                    }
                ],
            },
        }
    ]


@pytest.mark.asyncio
async def test_security_review_candidate_answer_is_resolved_and_recorded():
    adapter = JiuWenClawDeepAdapter()
    candidate = {"type": "security_note", "requires_approval": True}
    chunks = adapter._security_review_candidates_to_chunks([candidate])

    response = await adapter.handle_user_answer(
        AgentRequest(
            request_id="answer-1",
            channel_id="web",
            session_id="sess-1",
            params={
                "request_id": chunks[0]["request_id"],
                "answers": [{"selected_options": ["接收"]}],
            },
        )
    )

    assert response.payload["resolved"] is True
    assert adapter._security_review_approved_candidates == [candidate]
    assert adapter._security_review_pending_candidates == {}


def test_security_review_rail_receives_model_and_context_providers():
    adapter = JiuWenClawDeepAdapter()
    fake_model = object()
    adapter._model = fake_model

    rail = adapter._build_security_review_rail({"security_review": {"enabled": True}})

    assert rail.worker._llm is fake_model
    assert rail._message_provider is not None
    assert rail._skill_state_provider is not None


@pytest.mark.asyncio
async def test_security_review_rule_candidate_is_applied_after_approval(monkeypatch):
    adapter = JiuWenClawDeepAdapter()
    candidate = {
        "type": "security_rule",
        "rule_id": "block-curl-pipe-shell",
        "severity": "HIGH",
        "tools": ["bash"],
        "pattern": "re:(?i)curl\\b.*\\|\\s*sh",
        "rationale": "Downloaded script is piped directly to shell.",
        "requires_approval": True,
    }
    chunks = adapter._security_review_candidates_to_chunks([candidate])
    applied = []

    def fake_apply(payload):
        applied.append(payload)
        return {
            "applied": True,
            "target": "permissions.rules",
            "rule_id": "security_review_block-curl-pipe-shell",
        }

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.apply_security_rule_candidate",
        fake_apply,
    )

    response = await adapter.handle_user_answer(
        AgentRequest(
            request_id="answer-1",
            channel_id="web",
            session_id="sess-1",
            params={
                "request_id": chunks[0]["request_id"],
                "answers": [{"selected_options": ["接收"]}],
            },
        )
    )

    assert response.payload["resolved"] is True
    assert applied == [candidate]
    assert adapter._security_review_approved_candidates[0]["application"]["applied"] is True
    assert adapter._security_review_approved_candidates[0]["application"]["target"] == "permissions.rules"


@pytest.mark.asyncio
async def test_security_review_skill_candidate_is_applied_after_approval(monkeypatch):
    adapter = JiuWenClawDeepAdapter()
    candidate = {
        "type": "security_skill",
        "title": "Post exploitation chain defense",
        "problem": "listener plus credential access",
        "evidence": ["listener", "credential access"],
        "suggested_skill_scope": "Pattern, IOCs, response.",
        "category": "security",
        "requires_approval": True,
    }
    chunks = adapter._security_review_candidates_to_chunks([candidate])
    applied = []

    def fake_apply(payload):
        applied.append(payload)
        return {
            "applied": True,
            "target": "skills",
            "skill_name": "security-post-exploitation-chain-defense",
            "skill_path": "/tmp/skills/security-post-exploitation-chain-defense",
        }

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.apply_security_skill_candidate",
        fake_apply,
    )

    response = await adapter.handle_user_answer(
        AgentRequest(
            request_id="answer-1",
            channel_id="web",
            session_id="sess-1",
            params={
                "request_id": chunks[0]["request_id"],
                "answers": [{"selected_options": ["接收"]}],
            },
        )
    )

    assert response.payload["resolved"] is True
    assert applied == [candidate]
    assert adapter._security_review_approved_candidates[0]["application"]["applied"] is True
    assert adapter._security_review_approved_candidates[0]["application"]["target"] == "skills"


@pytest.mark.asyncio
async def test_security_review_evolution_candidate_is_applied_after_approval(monkeypatch):
    adapter = JiuWenClawDeepAdapter()
    candidate = {
        "type": "security_evolution",
        "skill_name": "safe-shell",
        "section": "Troubleshooting",
        "content": "Stop repeating blocked shell commands.",
        "evidence": ["blocked twice"],
        "requires_approval": True,
    }
    chunks = adapter._security_review_candidates_to_chunks([candidate])
    applied = []

    def fake_apply(payload):
        applied.append(payload)
        return {
            "applied": True,
            "target": "skills",
            "skill_name": "safe-shell",
            "skill_path": "/tmp/skills/safe-shell",
        }

    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.apply_security_evolution_candidate",
        fake_apply,
    )

    response = await adapter.handle_user_answer(
        AgentRequest(
            request_id="answer-1",
            channel_id="web",
            session_id="sess-1",
            params={
                "request_id": chunks[0]["request_id"],
                "answers": [{"selected_options": ["接收"]}],
            },
        )
    )

    assert response.payload["resolved"] is True
    assert applied == [candidate]
    assert adapter._security_review_approved_candidates[0]["application"]["applied"] is True
    assert adapter._security_review_approved_candidates[0]["application"]["target"] == "skills"


def test_get_current_agent_rails_updates_existing_security_review_rail_on_reload():
    adapter = JiuWenClawDeepAdapter()
    existing = SecurityReviewAndSkillRail(
        config={"enabled": True, "repeated_tool_failure_threshold": 2}
    )
    adapter._security_review_rail = existing

    with (
        patch.object(adapter, "_build_skill_rail", return_value=None),
        patch.object(adapter, "_update_permission_rail", return_value=None),
    ):
        rails = adapter._get_current_agent_rails(
            {"security_review": {"enabled": True, "repeated_tool_failure_threshold": 5}}
        )

    assert len(rails) == 1
    assert rails[0] is existing
    assert adapter._security_review_rail is existing
    assert existing.config.repeated_tool_failure_threshold == 5


def test_get_current_agent_rails_removes_security_review_rail_when_disabled():
    adapter = JiuWenClawDeepAdapter()
    adapter._security_review_rail = SecurityReviewAndSkillRail(config={"enabled": True})

    with (
        patch.object(adapter, "_build_skill_rail", return_value=None),
        patch.object(adapter, "_update_permission_rail", return_value=None),
    ):
        rails = adapter._get_current_agent_rails({"security_review": {"enabled": False}})

    assert rails == []
    assert adapter._security_review_rail is None
