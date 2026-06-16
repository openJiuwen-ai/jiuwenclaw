from types import SimpleNamespace

import pytest

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.server.runtime.agent_adapter import interface_deep as interface_deep_module
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("query", "mode", "slash_command", "expected_output"),
    [
        (
            "/evolve demo-skill improve",
            "agent.fast",
            "evolve",
            "agent.fast 模式下演进功能不可用。",
        ),
        (
            "/evolve_simplify demo-skill",
            "code.normal",
            "evolve_simplify",
            "code.normal 模式下演进功能不可用。",
        ),
        (
            "/evolve demo-skill improve",
            "auto_harness",
            "evolve",
            "auto_harness 模式下演进功能不可用。",
        ),
    ],
)
async def test_evolve_slash_reports_current_mode_when_unsupported(
    query: str,
    mode: str,
    slash_command: str,
    expected_output: str,
):
    adapter = JiuWenSwarmDeepAdapter()

    result = await adapter._handle_slash_command(  # pylint: disable=protected-access
        query,
        session_id="sess-evolve-mode",
        mode=mode,
    )

    assert result is not None
    assert result["slash_command"] == slash_command
    assert result["result_type"] == "error"
    assert result["output"] == expected_output


@pytest.mark.anyio
async def test_evolve_slash_checks_enabled_without_lazy_registering(monkeypatch):
    adapter = JiuWenSwarmDeepAdapter()
    adapter._config_cache = {"evolution": {"enabled": False}}  # pylint: disable=protected-access

    async def _unexpected_register():
        raise AssertionError("slash enabled check must not register active evolution rails")

    def _unexpected_store(*_args, **_kwargs):
        raise AssertionError("disabled evolution slash must not initialize evolution store")

    monkeypatch.setattr(adapter, "_ensure_active_evolution_rails_registered", _unexpected_register)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.evolution_slash.EvolutionStore",
        _unexpected_store,
    )

    result = await adapter._handle_slash_command(  # pylint: disable=protected-access
        "/evolve demo-skill improve",
        session_id="sess-evolve-disabled",
        mode="agent.plan",
    )

    assert result is not None
    assert result["slash_command"] == "evolve"
    assert result["result_type"] == "error"
    assert result["output"] == "演进功能未启用。"


@pytest.mark.anyio
async def test_evolve_slash_allows_team_without_lazy_registering(monkeypatch):
    adapter = JiuWenSwarmDeepAdapter()
    adapter._config_cache = {"evolution": {"enabled": True}}  # pylint: disable=protected-access

    async def _unexpected_register():
        raise AssertionError("team slash availability check must not register single-agent evolution rails")

    async def _fake_handler(_query, context):
        assert context.mode == "team"
        assert context.evolution_enabled is True
        return {"output": "team slash handled", "result_type": "answer"}

    monkeypatch.setattr(adapter, "_ensure_active_evolution_rails_registered", _unexpected_register)
    monkeypatch.setattr(interface_deep_module, "handle_evolution_slash_command", _fake_handler)

    result = await adapter._handle_slash_command(  # pylint: disable=protected-access
        "/evolve_list demo-skill",
        session_id="sess-team-evolve",
        mode="team",
    )

    assert result is not None
    assert result["slash_command"] == "evolve_list"
    assert result["result_type"] == "answer"
    assert result["output"] == "team slash handled"


@pytest.mark.anyio
async def test_evolve_slash_lazy_init_registers_active_review_rails(monkeypatch):
    class _FakeSkillEvolutionRail:
        pass

    class _FakeEvolutionInterruptRail:
        pass

    class _FakeSubagentRail:
        pass

    class _FakeInstance:
        def __init__(self):
            self.registered: list[object] = []

        async def register_rail(self, rail):
            self.registered.append(rail)

        def find_rails_by_type(self, rail_types):
            return [rail for rail in self.registered if isinstance(rail, rail_types)]

    class _FakeSkillManager:
        @staticmethod
        def list_execution_disabled_skills():
            return ["disabled-demo"]

    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = _FakeInstance()  # pylint: disable=protected-access
    adapter._config_cache = {  # pylint: disable=protected-access
        "evolution": {"enabled": True, "auto_scan": False},
        "model_name": "configured-model",
    }
    adapter._skill_manager = _FakeSkillManager()  # pylint: disable=protected-access
    adapter._default_model_name = "default-model"  # pylint: disable=protected-access
    adapter._model = object()  # pylint: disable=protected-access

    monkeypatch.setattr(interface_deep_module, "SkillEvolutionRail", _FakeSkillEvolutionRail)
    monkeypatch.setattr(interface_deep_module, "EvolutionInterruptRail", _FakeEvolutionInterruptRail)
    monkeypatch.setattr(interface_deep_module, "SubagentRail", _FakeSubagentRail)
    monkeypatch.setattr(adapter, "_resolve_runtime_language", lambda: "en")
    monkeypatch.setenv("EVOLUTION_AUTO_SCAN", "true")

    configure_calls = []

    async def _fake_configure(agent, **kwargs):
        configure_calls.append(kwargs)
        await agent.register_rail(_FakeEvolutionInterruptRail())
        await agent.register_rail(_FakeSkillEvolutionRail())

    monkeypatch.setattr(
        interface_deep_module,
        "configure_skill_evolution_runtime",
        _fake_configure,
    )

    result = await adapter._ensure_evolution_rail_for_slash("agent.plan")  # pylint: disable=protected-access

    assert result is None
    registered = adapter._instance.registered  # pylint: disable=protected-access
    assert len(registered) == 2
    assert isinstance(registered[0], _FakeEvolutionInterruptRail)
    assert isinstance(registered[1], _FakeSkillEvolutionRail)
    assert configure_calls == [
        {
            "skills_dir": str(interface_deep_module.get_agent_skills_dir()),
            "llm": adapter._model,  # pylint: disable=protected-access
            "model": "default-model",
            "auto_scan": True,
            "auto_save": False,
            "disabled_skills": ["disabled-demo"],
            "language": "en",
        }
    ]


def test_sync_active_evolution_review_agent_after_reload_restores_retained_rail(monkeypatch):
    class _FakeSkillEvolutionRail:
        def __init__(self):
            self.registered_agent = None

        def _register_evolution_review_agent(self, agent):
            self.registered_agent = agent

    class _FakeEvolutionInterruptRail:
        pass

    class _FakeSubagentRail:
        pass

    class _FakeInstance:
        def __init__(self, rails):
            self.rails = rails

        def find_rails_by_type(self, rail_types):
            return [rail for rail in self.rails if isinstance(rail, rail_types)]

    rail = _FakeSkillEvolutionRail()
    interrupt_rail = _FakeEvolutionInterruptRail()
    subagent_rail = _FakeSubagentRail()
    instance = _FakeInstance([subagent_rail, interrupt_rail, rail])
    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = instance  # pylint: disable=protected-access
    adapter._skill_evolution_rail = rail  # pylint: disable=protected-access
    adapter._config_cache = {"evolution": {"enabled": True}}  # pylint: disable=protected-access

    monkeypatch.setattr(interface_deep_module, "SkillEvolutionRail", _FakeSkillEvolutionRail)
    monkeypatch.setattr(interface_deep_module, "EvolutionInterruptRail", _FakeEvolutionInterruptRail)
    monkeypatch.setattr(interface_deep_module, "SubagentRail", _FakeSubagentRail)

    adapter._sync_active_evolution_review_agent_after_reload()  # pylint: disable=protected-access

    assert rail.registered_agent is instance
    assert adapter._skill_evolution_rail is rail  # pylint: disable=protected-access
    assert adapter._evolution_interrupt_rail is interrupt_rail  # pylint: disable=protected-access
    assert adapter._subagent_rail is subagent_rail  # pylint: disable=protected-access


def test_sync_active_evolution_review_agent_after_reload_skips_when_disabled():
    class _FakeSkillEvolutionRail:
        @staticmethod
        def _register_evolution_review_agent(_agent):
            raise AssertionError("disabled evolution must not restore review agent")

    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = object()  # pylint: disable=protected-access
    adapter._skill_evolution_rail = _FakeSkillEvolutionRail()  # pylint: disable=protected-access
    adapter._config_cache = {"evolution": {"enabled": False}}  # pylint: disable=protected-access

    adapter._sync_active_evolution_review_agent_after_reload()  # pylint: disable=protected-access


@pytest.mark.anyio
async def test_agent_evolve_simplify_already_minimal_returns_answer():
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

    class _FakeRail:
        store = _FakeStore()

        @staticmethod
        async def request_simplify(*_args, **_kwargs):
            return SimpleNamespace(
                status="already_minimal",
                message="Already minimal",
                approval_event=None,
                actions=[],
            )

    adapter = JiuWenSwarmDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access

    result = await adapter._handle_evolve_simplify_command(  # pylint: disable=protected-access
        "/evolve_simplify demo-skill",
    )

    assert result["result_type"] == "answer"
    assert result["output"].strip()


@pytest.mark.anyio
async def test_handle_user_answer_routes_regular_evolution_approval_without_request_prefix(monkeypatch):
    adapter = JiuWenSwarmDeepAdapter()
    seen: list[tuple[str, list[dict[str, list[str]]]]] = []

    async def _fake_handle_evolution_approval(request_id: str, answers: list):
        seen.append((request_id, answers))
        return True

    monkeypatch.setattr(adapter, "_handle_evolution_approval", _fake_handle_evolution_approval)

    response = await adapter.handle_user_answer(
        AgentRequest(
            request_id="answer-1",
            channel_id="web",
            session_id="sess-agent-evolve",
            req_method=ReqMethod.CHAT_ANSWER,
            params={
                "request_id": "regular_123",
                "answers": [{"selected_options": ["接收"]}],
                "source": "skill_evolution_approval",
                "approval_schema": "openjiuwen.skill_evolution_approval.v1",
                "evolution_meta": {
                    "event_kind": "approval",
                    "rail_kind": "regular",
                    "approval_kind": "evolve",
                },
            },
        )
    )

    assert seen == [("regular_123", [{"selected_options": ["接收"]}])]
    assert response.payload == {"accepted": True, "resolved": True}


@pytest.mark.anyio
async def test_handle_user_answer_does_not_route_call_interrupt_approval_to_regular_rail(monkeypatch):
    adapter = JiuWenSwarmDeepAdapter()

    async def _unexpected_handle_evolution_approval(*_args, **_kwargs):
        raise AssertionError("call_* interrupt approval must not use regular evolution rail")

    monkeypatch.setattr(adapter, "_handle_evolution_approval", _unexpected_handle_evolution_approval)

    response = await adapter.handle_user_answer(
        AgentRequest(
            request_id="answer-1",
            channel_id="web",
            session_id="sess-agent-evolve",
            req_method=ReqMethod.CHAT_ANSWER,
            params={
                "request_id": "call_123",
                "answers": [{"selected_options": ["allow_once"]}],
                "source": "skill_evolution_approval",
                "approval_schema": "openjiuwen.skill_evolution_approval.v1",
                "evolution_meta": {
                    "event_kind": "approval",
                    "rail_kind": "regular",
                    "approval_kind": "evolve",
                    "approval_transport": "interrupt",
                },
            },
        )
    )

    assert response.payload == {"accepted": True, "resolved": False}


@pytest.mark.anyio
async def test_agent_evolve_missing_skill_md_fails_before_sdk_call(monkeypatch):
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return False

    class _FakeRail:
        store = _FakeStore()
        processed_signal_keys: set[tuple[str, str]] = set()

        @staticmethod
        async def request_user_evolution(*_args, **_kwargs):
            pytest.fail("missing SKILL.md must be rejected before calling SDK evolution")

    adapter = JiuWenSwarmDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access
    monkeypatch.setattr(
        adapter,
        "_collect_messages_for_evolve",
        lambda _session_id: [{"role": "user", "content": "please evolve"}],
    )

    result = await adapter._handle_evolve_command(  # pylint: disable=protected-access
        "/evolve demo-skill improve review flow",
        "sess-agent-evolve",
    )

    assert result["result_type"] == "error"
    assert "SKILL.md" in result["output"]


@pytest.mark.anyio
@pytest.mark.parametrize(
    ("status", "message", "expected_type", "expected_output"),
    [
        ("generation_failed", "llm unavailable", "error", "llm unavailable"),
        (
            "no_evolution_no_records",
            "",
            "answer",
            "已请求演进，但本次未生成可保存经验。",
        ),
    ],
)
async def test_agent_evolve_maps_sdk_result_status(
    monkeypatch,
    status: str,
    message: str,
    expected_type: str,
    expected_output: str,
):
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

    class _FakeRail:
        store = _FakeStore()
        processed_signal_keys: set[tuple[str, str]] = set()

        @staticmethod
        async def request_user_evolution(*_args, **_kwargs):
            return SimpleNamespace(
                status=status,
                message=message,
                has_changes=False,
                approval_event=None,
                records=[],
            )

    adapter = JiuWenSwarmDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_collect_messages_for_evolve", lambda _session_id: [])

    result = await adapter._handle_evolve_command(  # pylint: disable=protected-access
        "/evolve demo-skill improve review flow",
        "sess-agent-evolve",
    )

    assert result == {"output": expected_output, "result_type": expected_type}


@pytest.mark.anyio
async def test_agent_evolve_without_local_signal_still_maps_sdk_generation_failure(monkeypatch):
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["code-runner"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

    recorded_intents: list[str] = []

    class _FakeRail:
        store = _FakeStore()
        processed_signal_keys: set[tuple[str, str]] = set()

        @staticmethod
        async def request_user_evolution(skill_name: str, evolution_intent: str, **_kwargs):
            recorded_intents.append(evolution_intent)
            return SimpleNamespace(
                status="generation_failed",
                message="llm unavailable",
                has_changes=False,
                approval_event=None,
                records=[],
            )

    adapter = JiuWenSwarmDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_collect_messages_for_evolve", lambda _session_id: [])

    result = await adapter._handle_evolve_command(  # pylint: disable=protected-access
        "/evolve code-runner",
        "sess-agent-evolve",
    )

    assert result == {"output": "llm unavailable", "result_type": "error"}
    assert recorded_intents == ["用户显式请求演进 Skill 'code-runner'。"]


@pytest.mark.anyio
async def test_agent_evolve_returns_followup_when_sdk_uses_active_review(monkeypatch):
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["code-runner"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

    recorded_calls: list[tuple[str, str]] = []

    class _FakeRail:
        store = _FakeStore()
        processed_signal_keys: set[tuple[str, str]] = set()

        @staticmethod
        async def request_user_evolution(skill_name: str, evolution_intent: str):
            recorded_calls.append((skill_name, evolution_intent))
            return SimpleNamespace(
                followup_prompt="review and evolve code-runner",
                status=None,
                message="",
                has_changes=True,
                approval_event=None,
                records=[],
            )

    adapter = JiuWenSwarmDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_collect_messages_for_evolve", lambda _session_id: [])

    result = await adapter._handle_evolve_command(  # pylint: disable=protected-access
        "/evolve code-runner improve review flow",
        "sess-agent-evolve",
    )

    assert recorded_calls == [("code-runner", "improve review flow")]
    assert result == {
        "action": "run_evolve_followup",
        "followup_prompt": "review and evolve code-runner",
        "skill_name": "code-runner",
        "result_type": "followup",
    }


@pytest.mark.anyio
async def test_agent_evolve_hides_internal_toolchain_generation_error(monkeypatch):
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["code-runner"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return skill_name == "code-runner"

    class _FakeRail:
        store = _FakeStore()
        processed_signal_keys: set[tuple[str, str]] = set()

        @staticmethod
        async def request_user_evolution(*_args, **_kwargs):
            return SimpleNamespace(
                status="generation_failed",
                message=(
                    "[170001] toolchain optimizer_backword execution error, "
                    "reason: [174031] toolchain optimizer tool_call lim_call "
                    "execution error, reason: invoke_failed"
                ),
                has_changes=False,
                approval_event=None,
                records=[],
            )

    adapter = JiuWenSwarmDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access
    monkeypatch.setattr(adapter, "_collect_messages_for_evolve", lambda _session_id: [])

    result = await adapter._handle_evolve_command(  # pylint: disable=protected-access
        "/evolve code-runner",
        "sess-agent-evolve",
    )

    assert result == {
        "output": "LLM 服务调用失败，请检查模型配置或稍后重试",
        "result_type": "error",
    }


@pytest.mark.anyio
async def test_agent_evolve_list_allows_skill_without_skill_md():
    class _FakeStore:
        @staticmethod
        def list_skill_names() -> list[str]:
            return ["demo-skill"]

        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return False

        @staticmethod
        async def get_records_by_score(skill_name: str) -> list[object]:
            return []

    adapter = JiuWenSwarmDeepAdapter()
    adapter._skill_evolution_rail = SimpleNamespace(  # pylint: disable=protected-access
        store=_FakeStore()
    )

    result = await adapter._handle_evolve_list_command(  # pylint: disable=protected-access
        "/evolve_list demo-skill",
    )

    assert result == {
        "output": "Skill 'demo-skill' 暂无演进经验。",
        "result_type": "answer",
    }


@pytest.mark.anyio
async def test_agent_evolve_simplify_returns_followup_when_sdk_uses_active_review():
    class _FakeStore:
        @staticmethod
        def skill_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

        @staticmethod
        def skill_definition_exists(skill_name: str) -> bool:
            return skill_name == "demo-skill"

    recorded_calls: list[tuple[str, str | None]] = []

    class _FakeRail:
        store = _FakeStore()

        @staticmethod
        async def request_simplify(skill_name: str, user_intent: str | None):
            recorded_calls.append((skill_name, user_intent))
            return SimpleNamespace(
                followup_prompt="review and simplify demo-skill",
                request_id=None,
                approval_event=None,
                actions=[],
            )

    adapter = JiuWenSwarmDeepAdapter()
    adapter._skill_evolution_rail = _FakeRail()  # pylint: disable=protected-access

    result = await adapter._handle_evolve_simplify_command(  # pylint: disable=protected-access
        "/evolve_simplify demo-skill merge duplicate records",
    )

    assert recorded_calls == [("demo-skill", "merge duplicate records")]
    assert result == {
        "action": "run_simplify_followup",
        "followup_prompt": "review and simplify demo-skill",
        "skill_name": "demo-skill",
        "result_type": "followup",
    }


@pytest.mark.parametrize(
    "action",
    [
        "run_rebuild_followup",
        "run_evolve_followup",
        "run_simplify_followup",
    ],
)
def test_agent_slash_followup_prompt_extraction_accepts_all_evolution_followups(action: str):
    result = {
        "action": action,
        "followup_prompt": "review and continue code-runner",
        "result_type": "followup",
    }

    assert (
        JiuWenSwarmDeepAdapter._extract_followup_prompt(result)  # pylint: disable=protected-access
        == "review and continue code-runner"
    )


def _adapter_ready_for_followup_execution(monkeypatch: pytest.MonkeyPatch) -> JiuWenSwarmDeepAdapter:
    adapter = JiuWenSwarmDeepAdapter()
    adapter._instance = SimpleNamespace(  # pylint: disable=protected-access
        get_context_usage=lambda **_kwargs: {},
    )
    monkeypatch.setattr(adapter, "_has_valid_model_config", lambda _model_name="": True)
    monkeypatch.setattr(adapter, "_bind_runtime_cron_context", lambda **_kwargs: None)
    monkeypatch.setattr(adapter, "_reset_runtime_cron_context", lambda _tokens: None)
    monkeypatch.setattr(adapter, "_resolve_model_for_request", lambda _request: None)
    monkeypatch.setattr(adapter, "_apply_model_to_react_agent", lambda _model: None)
    monkeypatch.setattr(adapter, "_mark_session_active", lambda _session_id: None)
    monkeypatch.setattr(adapter, "_register_session_agent_task", lambda _session_id: None)
    monkeypatch.setattr(adapter, "_unregister_session_agent_task", lambda _session_id: None)
    monkeypatch.setattr(adapter, "_unmark_session_active", lambda _session_id, **_kwargs: None)

    async def _noop_update_runtime_config(_runtime_config):
        return None

    monkeypatch.setattr(adapter, "_update_runtime_config", _noop_update_runtime_config)
    return adapter


@pytest.mark.anyio
async def test_agent_non_stream_slash_followup_continues_into_runner(monkeypatch):
    adapter = _adapter_ready_for_followup_execution(monkeypatch)
    seen_inputs: list[dict] = []

    async def _fake_slash_command(_query, _session_id, _mode):
        return {
            "action": "run_evolve_followup",
            "followup_prompt": "review and evolve code-runner",
            "result_type": "followup",
        }

    class _FakeRunner:
        @staticmethod
        async def run_agent(agent, inputs):
            assert agent is adapter._instance  # pylint: disable=protected-access
            seen_inputs.append(dict(inputs))
            return "agent completed"

    monkeypatch.setattr(adapter, "_handle_slash_command", _fake_slash_command)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.Runner",
        _FakeRunner,
    )

    response = await adapter.process_message_impl(
        AgentRequest(
            request_id="req-followup",
            channel_id="web",
            session_id="sess-followup",
            params={"query": "/evolve code-runner", "mode": "agent.plan"},
        ),
        {"query": "/evolve code-runner"},
    )

    assert seen_inputs == [
        {"query": "review and evolve code-runner", "_invoke_turn_id": "req-followup"}
    ]
    assert response.ok is True
    assert response.payload == {"content": "agent completed"}


@pytest.mark.anyio
async def test_agent_stream_slash_followup_continues_into_runner(monkeypatch):
    adapter = _adapter_ready_for_followup_execution(monkeypatch)
    seen_inputs: list[dict] = []

    async def _fake_slash_command(_query, _session_id, _mode):
        return {
            "action": "run_simplify_followup",
            "followup_prompt": "review and simplify code-runner",
            "result_type": "followup",
        }

    class _FakeRunner:
        @staticmethod
        async def run_agent_streaming(agent, inputs):
            assert agent is adapter._instance  # pylint: disable=protected-access
            seen_inputs.append(dict(inputs))
            yield SimpleNamespace(type="llm_output", payload={"content": "agent delta"})

    monkeypatch.setattr(adapter, "_handle_slash_command", _fake_slash_command)
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.agent_adapter.interface_deep.Runner",
        _FakeRunner,
    )

    chunks = []
    async for chunk in adapter.process_message_stream_impl(
        AgentRequest(
            request_id="req-followup-stream",
            channel_id="web",
            session_id="sess-followup-stream",
            params={"query": "/evolve_simplify code-runner", "mode": "agent.plan"},
            is_stream=True,
        ),
        {"query": "/evolve_simplify code-runner"},
    ):
        chunks.append(chunk)

    assert seen_inputs == [
        {"query": "review and simplify code-runner", "_invoke_turn_id": "req-followup-stream"}
    ]
    assert chunks[0].payload == {"event_type": "chat.delta", "content": "agent delta"}
    assert chunks[-1].is_complete is True
