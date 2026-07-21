from __future__ import annotations

import asyncio
import copy
from types import SimpleNamespace

import pytest
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig

from jiuwenswarm.common.schema.agent import AgentRequest
from jiuwenswarm.common.schema.message import ReqMethod
from jiuwenswarm.integrations.ai4research_subscription.auth_controller import CodexAuthController
from jiuwenswarm.integrations.ai4research_subscription.constants import (
    CODEX_MODEL_ALIAS,
    CODEX_PROVIDER_NAME,
)
from jiuwenswarm.integrations.ai4research_subscription.consumer_policy import (
    CODEX_CALL_PERMIT_KWARG,
    CODEX_SUBSCRIPTION_ENABLED_ENV,
    CodexConsumer,
    classify_agent_request,
    codex_consumer_scope,
    codex_subscription_enabled,
    issue_codex_call_permit,
    require_codex_model_consumer,
)
from jiuwenswarm.integrations.ai4research_subscription.contracts import ProviderTurnResult
from jiuwenswarm.integrations.ai4research_subscription.errors import CodexProviderError
from jiuwenswarm.integrations.ai4research_subscription.model_client import (
    CodexSubscriptionModelClient,
)
from jiuwenswarm.server.runtime.agent_adapter import interface_deep as deep_module
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter
from jiuwenswarm.symphony.llm import LLMConfig, create_llm_client


def _codex_client() -> CodexSubscriptionModelClient:
    return CodexSubscriptionModelClient(
        model_config=ModelRequestConfig(model_name=CODEX_MODEL_ALIAS, temperature=0),
        model_client_config=ModelClientConfig(
            client_id="consumer-policy-test",
            client_provider=CODEX_PROVIDER_NAME,
            api_key="",
            api_base="",
            timeout=25,
            max_retries=0,
        ),
    )


@pytest.mark.parametrize("value", [None, "", " ", "1", "true", "YES", "on"])
def test_feature_switch_enables_unset_empty_and_explicit_true(monkeypatch, value):
    if value is None:
        monkeypatch.delenv(CODEX_SUBSCRIPTION_ENABLED_ENV, raising=False)
    else:
        monkeypatch.setenv(CODEX_SUBSCRIPTION_ENABLED_ENV, value)
    assert codex_subscription_enabled() is True


@pytest.mark.parametrize("value", ["0", "false", "NO", "off", "invalid", "2"])
def test_feature_switch_disables_explicit_false_and_invalid_nonempty(monkeypatch, value):
    monkeypatch.setenv(CODEX_SUBSCRIPTION_ENABLED_ENV, value)
    assert codex_subscription_enabled() is False


@pytest.mark.parametrize(
    ("agent_request", "expected"),
    [
        (
            SimpleNamespace(
                req_method=ReqMethod.CHAT_SEND,
                channel_id="web",
                params={"mode": "agent.fast"},
                metadata={},
            ),
            CodexConsumer.DIRECT_AGENT_FAST,
        ),
        (
            SimpleNamespace(
                req_method=ReqMethod.CHAT_SEND,
                channel_id="__cron__",
                params={"mode": "agent.fast"},
                metadata={},
            ),
            CodexConsumer.CRON,
        ),
        (
            SimpleNamespace(
                req_method=ReqMethod.CHAT_SEND,
                channel_id="web",
                params={"mode": "agent.fast", "cron": {"job_id": "j"}},
                metadata={},
            ),
            CodexConsumer.CRON,
        ),
        (
            SimpleNamespace(
                req_method=ReqMethod.CHAT_SEND,
                channel_id="web",
                params={"mode": "team"},
                metadata={},
            ),
            CodexConsumer.TEAM,
        ),
        (
            SimpleNamespace(
                req_method=ReqMethod.CHAT_SEND,
                channel_id="web",
                params={"mode": "agent.plan"},
                metadata={},
            ),
            CodexConsumer.PLAN,
        ),
        (
            SimpleNamespace(
                req_method=ReqMethod.CHAT_SEND,
                channel_id="web",
                params={"mode": "code.normal"},
                metadata={},
            ),
            CodexConsumer.CODE,
        ),
        (
            SimpleNamespace(
                req_method=ReqMethod.PROACTIVE_TICK,
                channel_id="web",
                params={"mode": "agent.fast"},
                metadata={},
            ),
            CodexConsumer.PROACTIVE,
        ),
        (
            SimpleNamespace(
                req_method=ReqMethod.CHAT_SEND,
                channel_id="web",
                params={"mode": "unexpected"},
                metadata={},
            ),
            CodexConsumer.UNCLASSIFIED,
        ),
    ],
)
def test_request_consumer_classification_is_fail_closed(agent_request, expected):
    assert classify_agent_request(agent_request) is expected


@pytest.mark.parametrize(
    ("bound", "params", "expected"),
    [
        (
            False,
            {
                "mode": "agent.fast",
                "request_id": "skill_evolve_1",
                "answers": [{"selected_options": ["accept"]}],
                "source": "skill_evolution_approval",
                "approval_schema": "openjiuwen.skill_evolution_approval.v1",
            },
            CodexConsumer.UNCLASSIFIED,
        ),
        (
            True,
            {"mode": "agent.fast", "request_id": "skill_evolve_1", "answers": []},
            CodexConsumer.UNCLASSIFIED,
        ),
        (
            True,
            {
                "mode": "agent.fast",
                "request_id": "skill_evolve_1",
                "answers": [{"selected_options": ["accept"]}],
                "source": "skill_evolution_approval",
                "approval_schema": "openjiuwen.skill_evolution_approval.v1",
            },
            CodexConsumer.DIRECT_AGENT_FAST,
        ),
    ],
)
def test_chat_answer_requires_trusted_bound_approval_shape(
    bound: bool,
    params: dict,
    expected: CodexConsumer,
) -> None:
    request = SimpleNamespace(
        req_method=ReqMethod.CHAT_ANSWER,
        channel_id="web",
        params=params,
        metadata={},
        subscription_continuation_bound=bound,
    )
    assert classify_agent_request(request) is expected


@pytest.mark.asyncio
async def test_model_client_backstop_rejects_unclassified_before_runner(monkeypatch):
    monkeypatch.delenv(CODEX_SUBSCRIPTION_ENABLED_ENV, raising=False)
    client = _codex_client()
    calls = 0

    class Runner:
        async def run(self, **_kwargs):
            nonlocal calls
            calls += 1
            return ProviderTurnResult(content="unexpected", finish_reason="stop")

    client._runner = Runner()
    with pytest.raises(CodexProviderError) as captured:
        await client.invoke([{"role": "user", "content": "hello"}])
    assert captured.value.code == "consumer_unclassified"
    assert calls == 0


@pytest.mark.asyncio
async def test_feature_switch_rejects_allowed_consumer_before_runner(monkeypatch):
    monkeypatch.setenv(CODEX_SUBSCRIPTION_ENABLED_ENV, "off")
    client = _codex_client()
    calls = 0

    class Runner:
        async def run(self, **_kwargs):
            nonlocal calls
            calls += 1
            return ProviderTurnResult(content="unexpected", finish_reason="stop")

    client._runner = Runner()
    with codex_consumer_scope(CodexConsumer.DIRECT_AGENT_FAST):
        with pytest.raises(CodexProviderError) as captured:
            await client.invoke([{"role": "user", "content": "hello"}])
    assert captured.value.code == "provider_disabled"
    assert calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "consumer",
    [CodexConsumer.DIRECT_AGENT_FAST, CodexConsumer.CONFIG_VALIDATION],
)
async def test_inherited_consumer_scope_does_not_replace_call_permit(monkeypatch, consumer):
    monkeypatch.delenv(CODEX_SUBSCRIPTION_ENABLED_ENV, raising=False)
    client = _codex_client()
    calls = 0

    class Runner:
        async def run(self, **_kwargs):
            nonlocal calls
            calls += 1
            return ProviderTurnResult(content="unexpected", finish_reason="stop")

    client._runner = Runner()
    with codex_consumer_scope(consumer):
        with pytest.raises(CodexProviderError) as captured:
            await client.invoke([{"role": "user", "content": "hello"}])
    assert captured.value.code == "missing_call_permit"
    assert calls == 0


@pytest.mark.asyncio
async def test_config_validation_permit_is_bound_and_one_use(monkeypatch):
    monkeypatch.delenv(CODEX_SUBSCRIPTION_ENABLED_ENV, raising=False)
    client = _codex_client()
    calls = 0

    class Runner:
        async def run(self, **_kwargs):
            nonlocal calls
            calls += 1
            return ProviderTurnResult(content="ok", finish_reason="stop")

    client._runner = Runner()
    permit = issue_codex_call_permit(client, CodexConsumer.CONFIG_VALIDATION)
    response = await client.invoke(
        [{"role": "user", "content": "hello"}],
        **{CODEX_CALL_PERMIT_KWARG: permit},
    )
    assert response.content == "ok"

    with pytest.raises(CodexProviderError) as captured:
        await client.invoke(
            [{"role": "user", "content": "hello again"}],
            **{CODEX_CALL_PERMIT_KWARG: permit},
        )
    assert captured.value.code == "invalid_call_permit"
    assert calls == 1


@pytest.mark.asyncio
async def test_call_permit_rejects_wrong_client_and_reuse(monkeypatch):
    monkeypatch.delenv(CODEX_SUBSCRIPTION_ENABLED_ENV, raising=False)
    owner = _codex_client()
    other = _codex_client()
    calls = 0

    class Runner:
        async def run(self, **_kwargs):
            nonlocal calls
            calls += 1
            return ProviderTurnResult(content="ok", finish_reason="stop")

    owner._runner = Runner()
    other._runner = Runner()
    wrong_client_permit = issue_codex_call_permit(
        owner, CodexConsumer.DIRECT_AGENT_FAST
    )
    with pytest.raises(CodexProviderError) as captured:
        await other.invoke(
            [{"role": "user", "content": "wrong client"}],
            **{CODEX_CALL_PERMIT_KWARG: wrong_client_permit},
        )
    assert captured.value.code == "invalid_call_permit"
    assert calls == 0

    permit = issue_codex_call_permit(owner, CodexConsumer.DIRECT_AGENT_FAST)
    response = await owner.invoke(
        [{"role": "user", "content": "first use"}],
        **{CODEX_CALL_PERMIT_KWARG: permit},
    )
    assert response.content == "ok"
    with pytest.raises(CodexProviderError) as captured:
        await owner.invoke(
            [{"role": "user", "content": "reuse"}],
            **{CODEX_CALL_PERMIT_KWARG: permit},
        )
    assert captured.value.code == "invalid_call_permit"
    assert calls == 1


def test_call_permit_cannot_be_copied(monkeypatch):
    monkeypatch.delenv(CODEX_SUBSCRIPTION_ENABLED_ENV, raising=False)
    client = _codex_client()
    permit = issue_codex_call_permit(client, CodexConsumer.DIRECT_AGENT_FAST)

    with pytest.raises(CodexProviderError) as shallow:
        copy.copy(permit)
    with pytest.raises(CodexProviderError) as deep:
        copy.deepcopy(permit)
    assert shallow.value.code == "invalid_call_permit"
    assert deep.value.code == "invalid_call_permit"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "content",
    [
        [{"type": "text", "text": "hello"}],
        {"type": "text", "text": "hello"},
        123,
        None,
    ],
)
async def test_model_client_rejects_non_text_message_content_before_runner(
    monkeypatch, content,
):
    monkeypatch.delenv(CODEX_SUBSCRIPTION_ENABLED_ENV, raising=False)
    client = _codex_client()
    calls = 0

    class Runner:
        async def run(self, **_kwargs):
            nonlocal calls
            calls += 1
            return ProviderTurnResult(content="unexpected", finish_reason="stop")

    client._runner = Runner()
    with pytest.raises(CodexProviderError) as captured:
        await client.invoke(
            [{"role": "user", "content": content}],
            **{
                CODEX_CALL_PERMIT_KWARG: issue_codex_call_permit(
                    client, CodexConsumer.DIRECT_AGENT_FAST
                )
            },
        )
    assert captured.value.code == "invalid_request"
    assert calls == 0


@pytest.mark.asyncio
async def test_direct_consumer_filters_tools_to_reviewed_allowlist(monkeypatch):
    monkeypatch.delenv(CODEX_SUBSCRIPTION_ENABLED_ENV, raising=False)
    client = _codex_client()
    observed_tools = None

    class Runner:
        async def run(self, **kwargs):
            nonlocal observed_tools
            observed_tools = kwargs["tools"]
            return ProviderTurnResult(content="ok", finish_reason="stop")

    client._runner = Runner()
    tools = [
        {
            "type": "function",
            "function": {
                "name": name,
                "description": name,
                "parameters": {"type": "object", "properties": {}},
            },
        }
        for name in ("cron_list_jobs", "task", "symphony_compose")
    ]
    response = await client.invoke(
        [{"role": "user", "content": "list cron jobs"}],
        tools=tools,
        **{
            CODEX_CALL_PERMIT_KWARG: issue_codex_call_permit(
                client, CodexConsumer.DIRECT_AGENT_FAST
            )
        },
    )
    assert response.content == "ok"
    assert [tool["function"]["name"] for tool in observed_tools] == ["cron_list_jobs"]


def test_provider_registration_and_model_construction_survive_disabled_switch(monkeypatch):
    monkeypatch.setenv(CODEX_SUBSCRIPTION_ENABLED_ENV, "false")
    model = Model(
        model_client_config=ModelClientConfig(
            client_id="disabled-registration-test",
            client_provider=CODEX_PROVIDER_NAME,
            api_key="",
            api_base="",
        ),
        model_config=ModelRequestConfig(model_name=CODEX_MODEL_ALIAS),
    )
    assert isinstance(model._client, CodexSubscriptionModelClient)


@pytest.mark.asyncio
async def test_auth_start_fails_disabled_before_client_start(monkeypatch):
    monkeypatch.setenv(CODEX_SUBSCRIPTION_ENABLED_ENV, "0")
    controller = CodexAuthController()
    called = False

    async def fail_if_called():
        nonlocal called
        called = True
        raise AssertionError("App Server must not start")

    monkeypatch.setattr(controller, "_new_client_with_lock", fail_if_called)
    with pytest.raises(CodexProviderError) as captured:
        await controller.start_device_login()
    assert captured.value.code == "provider_disabled"
    assert called is False


@pytest.mark.asyncio
async def test_auth_status_returns_disabled_without_app_server_start(monkeypatch):
    monkeypatch.setenv(CODEX_SUBSCRIPTION_ENABLED_ENV, "off")
    controller = CodexAuthController()
    called = False

    async def fail_if_called():
        nonlocal called
        called = True
        raise AssertionError("App Server must not start")

    monkeypatch.setattr(controller, "_new_client_with_lock", fail_if_called)
    status = await controller.status()
    assert status == {
        "provider": "AI4ResearchCodex",
        "enabled": False,
        "available": True,
        "connected": False,
        "state": "disabled",
    }
    assert called is False


def test_symphony_rejects_codex_but_api_model_is_unchanged(monkeypatch):
    monkeypatch.delenv(CODEX_SUBSCRIPTION_ENABLED_ENV, raising=False)
    codex_config = LLMConfig(
        model=CODEX_MODEL_ALIAS,
        model_client_config={"client_provider": CODEX_PROVIDER_NAME},
    )
    with pytest.raises(CodexProviderError) as captured:
        create_llm_client(codex_config)
    assert captured.value.code == "unsupported_consumer"

    api_config = LLMConfig(
        model="model-a",
        model_client_config={
            "client_provider": "OpenAI",
            "api_base": "https://example.test/v1",
            "api_key": "key",
        },
    )
    assert type(create_llm_client(api_config)).__name__ == "JiuwenSwarmChatClient"
    require_codex_model_consumer(SimpleNamespace(), CodexConsumer.MEMORY)


@pytest.mark.asyncio
async def test_codex_auto_harness_stream_rejects_before_runtime_or_service(
    monkeypatch,
) -> None:
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._instance = object()
    adapter._is_session_scoped_adapter = True
    adapter._parent_session_id = None
    adapter._stream_event_rail = None
    codex_model = _codex_client()
    effects = {"runtime_config": 0, "auto_harness": 0}

    class ForbiddenAutoHarnessService:
        def __getattribute__(self, _name):
            effects["auto_harness"] += 1
            raise AssertionError("AutoHarness must not be inspected or dispatched")

    async def forbidden_runtime_config(*_args, **_kwargs):
        effects["runtime_config"] += 1
        raise AssertionError("Runtime config must not update before Codex admission")

    adapter._auto_harness_service = ForbiddenAutoHarnessService()
    monkeypatch.setattr(adapter, "_has_valid_model_config", lambda _name: True)
    monkeypatch.setattr(adapter, "_resolve_model_for_request", lambda _request: codex_model)
    monkeypatch.setattr(adapter, "_update_runtime_config", forbidden_runtime_config)
    request = AgentRequest(
        request_id="codex-auto-harness-rejection",
        channel_id="web",
        session_id="codex-auto-harness-session",
        params={"mode": "auto_harness", "query": "run swarmflow"},
    )

    with pytest.raises(CodexProviderError) as captured:
        async for _chunk in adapter.process_message_stream_impl(
            request,
            {
                "query": "run swarmflow",
                "conversation_id": request.session_id,
            },
        ):
            raise AssertionError("Rejected Codex AutoHarness request yielded a chunk")

    assert captured.value.code == "unsupported_consumer"
    assert effects == {"runtime_config": 0, "auto_harness": 0}
    assert request.metadata is None


def _processor_adapter(
    config_processors: list[object],
    context_processors: list[object],
) -> tuple[JiuWenSwarmDeepAdapter, SimpleNamespace, SimpleNamespace]:
    adapter = object.__new__(JiuWenSwarmDeepAdapter)
    adapter._model_turn_lock = deep_module._ModelTurnGate()
    config = SimpleNamespace(context_processors=config_processors)
    context = SimpleNamespace(_processors=context_processors)
    context_engine = SimpleNamespace(get_context=lambda **_kwargs: context)
    react_agent = SimpleNamespace(
        config=config,
        _config=config,
        _llm=None,
        context_engine=context_engine,
    )
    react_agent.set_llm = lambda model: setattr(react_agent, "_llm", model)
    adapter._instance = SimpleNamespace(
        react_agent=react_agent,
        _react_agent=react_agent,
    )
    return adapter, config, context


def _turn_model(name: str) -> SimpleNamespace:
    return SimpleNamespace(
        model_config=SimpleNamespace(model_name=name),
        model_client_config=SimpleNamespace(client_provider="OpenAI"),
    )


async def _run_guarded_model_turn(
    adapter: JiuWenSwarmDeepAdapter,
    model: object,
    probe: SimpleNamespace,
    *,
    exclusive: bool = False,
    delay: float = 0.04,
    release: asyncio.Event | None = None,
    acquired: asyncio.Event | None = None,
) -> None:
    state = await adapter._begin_model_turn(
        "same-session",
        model,
        suspend_context_processors=exclusive,
    )
    react_agent = adapter._instance._react_agent
    probe.active += 1
    probe.max_active = max(probe.max_active, probe.active)
    if probe.active == 2 and hasattr(probe, "two_active"):
        probe.two_active.set()
    if acquired is not None:
        acquired.set()
    try:
        assert react_agent._llm is model
        if release is not None:
            await release.wait()
        await asyncio.sleep(delay)
        assert react_agent._llm is model
    finally:
        probe.active -= 1
        adapter._end_model_turn(state)


@pytest.mark.asyncio
async def test_same_model_api_turns_overlap_for_near_one_delay() -> None:
    adapter, config, context = _processor_adapter(["config"], ["context"])
    model = _turn_model("shared-api-model")
    release = asyncio.Event()
    probe = SimpleNamespace(
        active=0,
        max_active=0,
        two_active=asyncio.Event(),
    )
    delay = 0.08
    turns = [
        asyncio.create_task(
            _run_guarded_model_turn(
                adapter,
                model,
                probe,
                delay=delay,
                release=release,
            )
        )
        for _ in range(2)
    ]
    await asyncio.wait_for(probe.two_active.wait(), timeout=0.5)
    started = asyncio.get_running_loop().time()
    release.set()
    await asyncio.wait_for(asyncio.gather(*turns), timeout=0.5)
    elapsed = asyncio.get_running_loop().time() - started

    assert probe.max_active == 2
    assert elapsed < delay * 1.75
    assert adapter._instance._react_agent._llm is model
    assert config.model_name == "shared-api-model"
    assert adapter._model_request_config is model.model_config
    assert config.context_processors == ["config"]
    assert context._processors == ["context"]
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("reverse_order", [False, True])
async def test_different_api_model_cohorts_serialize_and_preserve_identity(
    reverse_order: bool,
) -> None:
    adapter, _config, _context = _processor_adapter(["config"], ["context"])
    model_a = _turn_model("api-a")
    model_b = _turn_model("api-b")
    first_model, second_model = (
        (model_b, model_a) if reverse_order else (model_a, model_b)
    )
    probe = SimpleNamespace(active=0, max_active=0)
    first_acquired = asyncio.Event()
    first = asyncio.create_task(
        _run_guarded_model_turn(
            adapter,
            first_model,
            probe,
            delay=0.06,
            acquired=first_acquired,
        )
    )
    await asyncio.wait_for(first_acquired.wait(), timeout=0.5)
    second = asyncio.create_task(
        _run_guarded_model_turn(
            adapter,
            second_model,
            probe,
            delay=0.06,
        )
    )

    await asyncio.wait_for(asyncio.gather(first, second), timeout=0.5)
    assert probe.max_active == 1
    assert adapter._instance._react_agent._llm is second_model
    assert adapter._model_request_config is second_model.model_config
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
@pytest.mark.parametrize("codex_first", [False, True])
async def test_codex_and_api_turns_never_overlap_in_either_order(
    codex_first: bool,
) -> None:
    adapter, _config, _context = _processor_adapter(["config"], ["context"])
    codex_model = _turn_model("codex-subscription")
    api_model = _turn_model("api-model")
    first_model, first_exclusive = (
        (codex_model, True) if codex_first else (api_model, False)
    )
    second_model, second_exclusive = (
        (api_model, False) if codex_first else (codex_model, True)
    )
    probe = SimpleNamespace(active=0, max_active=0)
    first_acquired = asyncio.Event()
    first = asyncio.create_task(
        _run_guarded_model_turn(
            adapter,
            first_model,
            probe,
            exclusive=first_exclusive,
            delay=0.06,
            acquired=first_acquired,
        )
    )
    await asyncio.wait_for(first_acquired.wait(), timeout=0.5)
    second = asyncio.create_task(
        _run_guarded_model_turn(
            adapter,
            second_model,
            probe,
            exclusive=second_exclusive,
            delay=0.06,
        )
    )

    await asyncio.wait_for(asyncio.gather(first, second), timeout=0.5)
    assert probe.max_active == 1
    assert adapter._instance._react_agent._llm is second_model
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_model_turn_gates_on_different_adapters_overlap() -> None:
    adapter_a, _config_a, _context_a = _processor_adapter(["a"], ["a"])
    adapter_b, _config_b, _context_b = _processor_adapter(["b"], ["b"])
    probe = SimpleNamespace(
        active=0,
        max_active=0,
        two_active=asyncio.Event(),
    )
    release = asyncio.Event()
    delay = 0.08
    turns = (
        asyncio.create_task(
            _run_guarded_model_turn(
                adapter_a,
                _turn_model("api-a"),
                probe,
                delay=delay,
                release=release,
            )
        ),
        asyncio.create_task(
            _run_guarded_model_turn(
                adapter_b,
                _turn_model("api-b"),
                probe,
                delay=delay,
                release=release,
            )
        ),
    )
    await asyncio.wait_for(probe.two_active.wait(), timeout=0.5)
    started = asyncio.get_running_loop().time()
    release.set()
    await asyncio.wait_for(asyncio.gather(*turns), timeout=0.5)
    elapsed = asyncio.get_running_loop().time() - started

    assert probe.max_active == 2
    assert elapsed < delay * 1.75
    assert not adapter_a._model_turn_lock.locked()
    assert not adapter_b._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_cancelling_waiting_reader_cohort_member_does_not_deadlock() -> None:
    adapter, _config, _context = _processor_adapter(["config"], ["context"])
    active_model = _turn_model("api-a")
    waiting_model = _turn_model("api-b")
    active = await adapter._begin_model_turn(
        "same-session",
        active_model,
        suspend_context_processors=False,
    )
    first_waiter = asyncio.create_task(
        adapter._begin_model_turn(
            "same-session",
            waiting_model,
            suspend_context_processors=False,
        )
    )
    second_waiter = asyncio.create_task(
        adapter._begin_model_turn(
            "same-session",
            waiting_model,
            suspend_context_processors=False,
        )
    )
    await asyncio.sleep(0)
    assert not first_waiter.done()
    assert not second_waiter.done()
    first_waiter.cancel()
    with pytest.raises(asyncio.CancelledError):
        await first_waiter

    adapter._end_model_turn(active)
    admitted = await asyncio.wait_for(second_waiter, timeout=0.5)
    assert adapter._instance._react_agent._llm is waiting_model
    adapter._end_model_turn(admitted)
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_processor_suspension_serializes_overlapping_api_turn() -> None:
    adapter, config, context = _processor_adapter(["config"], ["context"])
    codex_state = await adapter._begin_model_turn(
        "same-session",
        None,
        suspend_context_processors=True,
    )
    assert config.context_processors == []
    assert context._processors == []

    api_acquired = asyncio.Event()

    async def begin_api_turn():
        state = await adapter._begin_model_turn(
            "same-session",
            None,
            suspend_context_processors=False,
        )
        api_acquired.set()
        return state

    api_task = asyncio.create_task(begin_api_turn())
    await asyncio.sleep(0)
    assert not api_acquired.is_set()

    adapter._end_model_turn(codex_state)
    api_state = await api_task
    assert config.context_processors == ["config"]
    assert context._processors == ["context"]
    adapter._end_model_turn(api_state)
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_processor_suspension_rolls_back_when_context_lookup_fails() -> None:
    adapter, config, _context = _processor_adapter(["config"], ["context"])

    def fail_context_lookup(**_kwargs):
        raise RuntimeError("context lookup failed")

    adapter._instance.react_agent.context_engine.get_context = fail_context_lookup
    with pytest.raises(RuntimeError, match="context lookup failed"):
        await adapter._begin_model_turn(
            "same-session",
            None,
            suspend_context_processors=True,
        )
    assert config.context_processors == ["config"]
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_processor_suspension_does_not_block_or_mutate_another_session() -> None:
    codex_adapter, codex_config, codex_context = _processor_adapter(
        ["codex-config"],
        ["codex-context"],
    )
    api_adapter, api_config, api_context = _processor_adapter(
        ["api-config"],
        ["api-context"],
    )
    codex_state = await codex_adapter._begin_model_turn(
        "codex-session",
        None,
        suspend_context_processors=True,
    )
    api_state = await asyncio.wait_for(
        api_adapter._begin_model_turn(
            "api-session",
            None,
            suspend_context_processors=False,
        ),
        timeout=0.5,
    )
    assert codex_config.context_processors == []
    assert codex_context._processors == []
    assert api_config.context_processors == ["api-config"]
    assert api_context._processors == ["api-context"]

    api_adapter._end_model_turn(api_state)
    codex_adapter._end_model_turn(codex_state)
    assert codex_config.context_processors == ["codex-config"]
    assert codex_context._processors == ["codex-context"]


@pytest.mark.asyncio
async def test_mixed_provider_overlap_cannot_replace_active_turn_model(monkeypatch) -> None:
    adapter, _config, _context = _processor_adapter(["config"], ["context"])
    selected = SimpleNamespace(model=None)

    def apply_model(model):
        selected.model = model

    monkeypatch.setattr(adapter, "_apply_model_to_react_agent", apply_model)
    codex_model = object()
    api_model = object()
    codex_state = await adapter._begin_model_turn(
        "same-session",
        codex_model,
        suspend_context_processors=True,
    )
    assert selected.model is codex_model

    api_acquired = asyncio.Event()

    async def begin_api_turn():
        state = await adapter._begin_model_turn(
            "same-session",
            api_model,
            suspend_context_processors=False,
        )
        api_acquired.set()
        return state

    api_task = asyncio.create_task(begin_api_turn())
    await asyncio.sleep(0)
    assert not api_acquired.is_set()
    assert selected.model is codex_model

    adapter._end_model_turn(codex_state)
    api_state = await api_task
    assert selected.model is api_model
    adapter._end_model_turn(api_state)


@pytest.mark.asyncio
async def test_pending_codex_turn_blocks_new_api_readers_and_preserves_model(
    monkeypatch,
) -> None:
    adapter, config, context = _processor_adapter(["config"], ["context"])
    selected = SimpleNamespace(model=None)
    monkeypatch.setattr(
        adapter,
        "_apply_model_to_react_agent",
        lambda model: setattr(selected, "model", model),
    )
    first_api_model = object()
    codex_model = object()
    later_api_model = object()
    first_api_state = await adapter._begin_model_turn(
        "same-session",
        first_api_model,
        suspend_context_processors=False,
    )

    codex_acquired = asyncio.Event()
    later_api_acquired = asyncio.Event()

    async def begin_codex():
        state = await adapter._begin_model_turn(
            "same-session",
            codex_model,
            suspend_context_processors=True,
        )
        codex_acquired.set()
        return state

    async def begin_later_api():
        state = await adapter._begin_model_turn(
            "same-session",
            later_api_model,
            suspend_context_processors=False,
        )
        later_api_acquired.set()
        return state

    codex_task = asyncio.create_task(begin_codex())
    await asyncio.sleep(0)
    later_api_task = asyncio.create_task(begin_later_api())
    await asyncio.sleep(0)
    assert not codex_acquired.is_set()
    assert not later_api_acquired.is_set()
    assert selected.model is first_api_model

    adapter._end_model_turn(first_api_state)
    codex_state = await asyncio.wait_for(codex_task, timeout=0.5)
    assert codex_acquired.is_set()
    assert not later_api_acquired.is_set()
    assert selected.model is codex_model
    assert config.context_processors == []
    assert context._processors == []

    adapter._end_model_turn(codex_state)
    later_api_state = await asyncio.wait_for(later_api_task, timeout=0.5)
    assert selected.model is later_api_model
    assert config.context_processors == ["config"]
    assert context._processors == ["context"]
    adapter._end_model_turn(later_api_state)


@pytest.mark.asyncio
async def test_two_codex_turns_serialize() -> None:
    adapter, _config, _context = _processor_adapter(["config"], ["context"])
    first = await adapter._begin_model_turn(
        "same-session",
        None,
        suspend_context_processors=True,
    )
    second_acquired = asyncio.Event()

    async def begin_second():
        state = await adapter._begin_model_turn(
            "same-session",
            None,
            suspend_context_processors=True,
        )
        second_acquired.set()
        return state

    second_task = asyncio.create_task(begin_second())
    await asyncio.sleep(0)
    assert not second_acquired.is_set()
    adapter._end_model_turn(first)
    second = await asyncio.wait_for(second_task, timeout=0.5)
    adapter._end_model_turn(second)
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_cancelling_waiting_turn_does_not_leak_gate_permit() -> None:
    adapter, _config, _context = _processor_adapter(["config"], ["context"])
    writer = await adapter._begin_model_turn(
        "same-session",
        None,
        suspend_context_processors=True,
    )
    waiting_reader = asyncio.create_task(
        adapter._begin_model_turn(
            "same-session",
            None,
            suspend_context_processors=False,
        )
    )
    await asyncio.sleep(0)
    waiting_reader.cancel()
    with pytest.raises(asyncio.CancelledError):
        await waiting_reader
    adapter._end_model_turn(writer)

    next_reader = await asyncio.wait_for(
        adapter._begin_model_turn(
            "same-session",
            None,
            suspend_context_processors=False,
        ),
        timeout=0.5,
    )
    adapter._end_model_turn(next_reader)
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_cancelling_holding_turn_releases_in_existing_finally() -> None:
    adapter, _config, _context = _processor_adapter(["config"], ["context"])
    acquired = asyncio.Event()

    async def hold_turn():
        state = await adapter._begin_model_turn(
            "same-session",
            None,
            suspend_context_processors=True,
        )
        acquired.set()
        try:
            await asyncio.Event().wait()
        finally:
            adapter._end_model_turn(state)

    task = asyncio.create_task(hold_turn())
    await asyncio.wait_for(acquired.wait(), timeout=0.5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_model_turn_lock_releases_when_processor_restore_fails(monkeypatch) -> None:
    adapter, _config, _context = _processor_adapter(["config"], ["context"])
    state = await adapter._begin_model_turn(
        "same-session",
        None,
        suspend_context_processors=False,
    )

    def fail_restore(_state):
        raise RuntimeError("restore failed")

    monkeypatch.setattr(adapter, "_restore_codex_context_processors", fail_restore)
    with pytest.raises(RuntimeError, match="restore failed"):
        adapter._end_model_turn(state)
    assert not adapter._model_turn_lock.locked()
