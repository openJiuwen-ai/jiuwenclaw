from __future__ import annotations

import asyncio
import json
import re
from types import SimpleNamespace

import pytest
from openjiuwen.core.foundation.llm import Model, ModelClientConfig, ModelRequestConfig
from openjiuwen.core.foundation.tool import tool
from openjiuwen.core.runner.callback.framework import AsyncCallbackFramework
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.rail.base import AgentRail
from openjiuwen.harness import DeepAgentConfig

from jiuwenswarm.common.schema.message import Message, ReqMethod
from jiuwenswarm.gateway.message_handler import (
    message_handler as message_handler_module,
)
from jiuwenswarm.gateway.message_handler.message_handler import MessageHandler
from jiuwenswarm.integrations.ai4research_subscription.constants import (
    CODEX_MODEL_ALIAS,
    CODEX_PROVIDER_NAME,
)
from jiuwenswarm.integrations.ai4research_subscription.contracts import (
    ProviderToolCall,
    ProviderTurnResult,
    build_provider_prompt,
)
from jiuwenswarm.integrations.ai4research_subscription.errors import CodexProviderError
from jiuwenswarm.integrations.ai4research_subscription.model_client import (
    CodexSubscriptionModelClient,
)
from jiuwenswarm.server.runtime.agent_adapter import (
    interface_deep as interface_deep_module,
)


def _model_entry(provider: str, model_name: str, *, alias: str = "") -> dict:
    return {
        "alias": alias,
        "is_default": True,
        "model_client_config": {
            "client_provider": provider,
            "model_name": model_name,
            "api_base": "https://example.test/v1"
            if provider != CODEX_PROVIDER_NAME
            else "",
            "api_key": "test-key" if provider != CODEX_PROVIDER_NAME else "",
        },
        "model_config_obj": {},
    }


def _chat_message(*, mode: str, params: dict | None = None) -> Message:
    request_params = {"mode": mode, "query": "hello", "model_name": CODEX_MODEL_ALIAS}
    request_params.update(params or {})
    return Message(
        id=f"request-{mode}",
        type="req",
        channel_id="web",
        session_id=f"session-{mode}",
        params=request_params,
        timestamp=0.0,
        ok=True,
        req_method=ReqMethod.CHAT_SEND,
        is_stream=True,
    )


class _NeverCalledAgentClient:
    calls = 0

    @classmethod
    async def send_request(cls, _envelope):
        cls.calls += 1
        raise AssertionError("admission failure must not reach AgentServer")

    @classmethod
    async def send_request_stream(cls, _envelope):
        cls.calls += 1
        raise AssertionError("admission failure must not reach AgentServer")
        if False:  # pragma: no cover - preserve async-generator shape
            yield None


class _AdmissionProbeHandler(MessageHandler):
    @classmethod
    def create(cls) -> "_AdmissionProbeHandler":
        MessageHandler._instance = None
        cls._instance = None
        _NeverCalledAgentClient.calls = 0
        handler = cls(_NeverCalledAgentClient())
        handler.effects = {
            "godview": 0,
            "before_hook": 0,
            "cancel": 0,
            "process_stream": 0,
        }
        handler.stream_started = asyncio.Event()
        handler._gateway_hook_handler = None
        return handler

    async def _maybe_register_godview(self, _msg: Message) -> None:
        self.effects["godview"] += 1

    async def _trigger_before_chat_request_hook(self, _msg: Message) -> None:
        self.effects["before_hook"] += 1

    async def _cancel_stream_tasks_for_channel(self, _msg: Message) -> int:
        self.effects["cancel"] += 1
        return 0

    async def process_stream(self, *_args, **_kwargs) -> None:
        self.effects["process_stream"] += 1
        self.stream_started.set()


async def _next_robot_message(handler: MessageHandler) -> Message:
    message = await handler.consume_robot_messages(timeout=0.5)
    assert message is not None
    return message


@pytest.mark.asyncio
async def test_gateway_rejects_unsupported_codex_mode_before_all_dispatch_side_effects(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        message_handler_module,
        "get_default_models",
        lambda: [_model_entry(CODEX_PROVIDER_NAME, CODEX_MODEL_ALIAS)],
    )
    handler = _AdmissionProbeHandler.create()
    await handler.start_forwarding()
    try:
        await handler.publish_user_messages(_chat_message(mode="agent.plan"))
        error = await _next_robot_message(handler)
    finally:
        await handler.stop_forwarding()

    assert error.ok is False
    assert error.payload == {
        "error": "unsupported_consumer: Codex subscription is not enabled for this consumer in v1.",
        "code": "unsupported_consumer",
    }
    assert handler.effects == {
        "godview": 0,
        "before_hook": 0,
        "cancel": 0,
        "process_stream": 0,
    }
    assert _NeverCalledAgentClient.calls == 0


@pytest.mark.asyncio
async def test_gateway_rejects_codex_image_before_attachment_read_hook_or_dispatch(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        message_handler_module,
        "get_default_models",
        lambda: [_model_entry(CODEX_PROVIDER_NAME, CODEX_MODEL_ALIAS)],
    )

    def forbidden_attachment_read(*_args, **_kwargs):
        raise AssertionError("Codex multimodal admission must precede attachment reads")

    monkeypatch.setattr(
        MessageHandler,
        "_resolve_structured_attachments",
        staticmethod(forbidden_attachment_read),
    )
    handler = _AdmissionProbeHandler.create()
    request = _chat_message(
        mode="agent.fast",
        params={
            "attachments": [
                {
                    "path": "/must/not/be/read.png",
                    "type": "image",
                    "mime_type": "image/png",
                }
            ]
        },
    )
    await handler.start_forwarding()
    try:
        await handler.publish_user_messages(request)
        error = await _next_robot_message(handler)
    finally:
        await handler.stop_forwarding()

    assert error.ok is False
    assert error.payload == {
        "error": "unsupported_modality: Codex subscription supports text-only requests in v1.",
        "code": "unsupported_modality",
    }
    assert handler.effects == {
        "godview": 0,
        "before_hook": 0,
        "cancel": 0,
        "process_stream": 0,
    }
    assert _NeverCalledAgentClient.calls == 0


@pytest.mark.parametrize(
    ("params", "expected_code"),
    [
        ({"query": {"text": "hello"}}, "invalid_request"),
        ({"query": 7}, "invalid_request"),
        ({"query": [{"type": "text", "text": "hello"}]}, "invalid_request"),
        ({"content": {"text": "hello"}}, "invalid_request"),
        (
            {"query": [{"type": "image_url", "image_url": "file:///not-read.png"}]},
            "unsupported_modality",
        ),
    ],
)
@pytest.mark.asyncio
async def test_gateway_rejects_non_text_codex_prompt_before_all_side_effects(
    monkeypatch,
    params: dict,
    expected_code: str,
) -> None:
    monkeypatch.setattr(
        message_handler_module,
        "get_default_models",
        lambda: [_model_entry(CODEX_PROVIDER_NAME, CODEX_MODEL_ALIAS)],
    )

    def forbidden_attachment_read(*_args, **_kwargs):
        raise AssertionError("Codex prompt admission must precede attachment reads")

    monkeypatch.setattr(
        MessageHandler,
        "_resolve_structured_attachments",
        staticmethod(forbidden_attachment_read),
    )
    handler = _AdmissionProbeHandler.create()
    request = _chat_message(mode="agent.fast", params=params)
    await handler.start_forwarding()
    try:
        await handler.publish_user_messages(request)
        error = await _next_robot_message(handler)
    finally:
        await handler.stop_forwarding()

    assert error.ok is False
    assert error.payload["code"] == expected_code
    assert handler.effects == {
        "godview": 0,
        "before_hook": 0,
        "cancel": 0,
        "process_stream": 0,
    }
    assert _NeverCalledAgentClient.calls == 0


@pytest.mark.asyncio
async def test_gateway_preflight_keeps_parallel_api_stream_dispatch_unchanged(
    monkeypatch,
) -> None:
    monkeypatch.setattr(
        message_handler_module,
        "get_default_models",
        lambda: [_model_entry("OpenAI", "api-model", alias=CODEX_MODEL_ALIAS)],
    )
    handler = _AdmissionProbeHandler.create()
    await handler.start_forwarding()
    try:
        first = _chat_message(mode="agent.fast")
        first.id = "api-request-1"
        first.session_id = "api-session-1"
        second = _chat_message(mode="agent.fast")
        second.id = "api-request-2"
        second.session_id = "api-session-2"
        await handler.publish_user_messages(first)
        await handler.publish_user_messages(second)
        async with asyncio.timeout(0.5):
            while handler.effects["process_stream"] < 2:
                await asyncio.sleep(0)
    finally:
        await handler.stop_forwarding()

    assert handler.effects["godview"] == 2
    assert handler.effects["before_hook"] == 2
    assert handler.effects["process_stream"] == 2
    assert _NeverCalledAgentClient.calls == 0


@pytest.mark.parametrize(
    ("params", "expected_code"),
    [
        (
            {"media_items": [{"path": "/not-read.png", "mime_type": "image/png"}]},
            "unsupported_modality",
        ),
        ({"query": {"text": "hello"}}, "invalid_request"),
        ({"query": 7}, "invalid_request"),
        ({"query": [{"type": "text", "text": "hello"}]}, "invalid_request"),
        ({"content": {"text": "hello"}}, "invalid_request"),
        (
            {"query": [{"type": "image_url", "image_url": "file:///not-read.png"}]},
            "unsupported_modality",
        ),
    ],
)
def test_adapter_preflight_rejects_non_text_codex_but_not_api_model(
    params: dict,
    expected_code: str,
) -> None:
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )
    from jiuwenswarm.integrations.ai4research_subscription.errors import (
        CodexProviderError,
    )

    request = SimpleNamespace(
        req_method=ReqMethod.CHAT_SEND,
        channel_id="web",
        params={"mode": "agent.fast", **params},
        metadata={},
    )
    codex_model = SimpleNamespace(
        model_client_config=SimpleNamespace(client_provider=CODEX_PROVIDER_NAME)
    )
    with pytest.raises(CodexProviderError) as captured:
        JiuWenSwarmDeepAdapter._preflight_codex_request(request, codex_model)
    assert captured.value.code == expected_code

    api_model = SimpleNamespace(
        model_client_config=SimpleNamespace(client_provider="OpenAI")
    )
    assert JiuWenSwarmDeepAdapter._preflight_codex_request(request, api_model) is None


def test_adapter_preflight_requires_typed_bound_signal_for_regular_codex_answer() -> (
    None
):
    from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
        JiuWenSwarmDeepAdapter,
    )

    params = {
        "mode": "agent.fast",
        "model_name": CODEX_MODEL_ALIAS,
        "request_id": "skill_evolve_1",
        "answers": [{"selected_options": ["accept"]}],
        "source": "skill_evolution_approval",
        "approval_schema": "openjiuwen.skill_evolution_approval.v1",
    }
    codex_model = SimpleNamespace(
        model_client_config=SimpleNamespace(client_provider=CODEX_PROVIDER_NAME)
    )
    unbound = SimpleNamespace(
        req_method=ReqMethod.CHAT_ANSWER,
        channel_id="web",
        params=params,
        metadata={},
        subscription_continuation_bound=False,
    )
    with pytest.raises(CodexProviderError) as captured:
        JiuWenSwarmDeepAdapter._preflight_codex_request(unbound, codex_model)
    assert captured.value.code == "consumer_unclassified"

    bound = SimpleNamespace(
        req_method=ReqMethod.CHAT_ANSWER,
        channel_id="web",
        params=params,
        metadata={},
        subscription_continuation_bound=True,
    )
    assert (
        JiuWenSwarmDeepAdapter._preflight_codex_request(bound, codex_model)
        is interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST
    )


def _real_codex_model() -> tuple[Model, CodexSubscriptionModelClient]:
    model = Model(
        model_client_config=ModelClientConfig(
            client_id="request-local-wrapper-test",
            client_provider=CODEX_PROVIDER_NAME,
            api_key="",
            api_base="",
            timeout=25,
            max_retries=0,
        ),
        model_config=ModelRequestConfig(model_name=CODEX_MODEL_ALIAS, temperature=0),
    )
    assert isinstance(model._client, CodexSubscriptionModelClient)
    return model, model._client


def _model_call_rails(
    wrapped: interface_deep_module._CallBoundCodexModel,
) -> tuple[
    interface_deep_module._CodexModelCallArmRail,
    interface_deep_module._CodexModelCallRevokeRail,
]:
    revoke_rail = interface_deep_module._CodexModelCallRevokeRail(wrapped)
    arm_rail = interface_deep_module._CodexModelCallArmRail(wrapped)
    return arm_rail, revoke_rail


async def _real_deep_agent(
    model: Model,
    *,
    agent_id: str,
    tools: list[object] | None = None,
    rails: list[AgentRail] | None = None,
):
    agent = interface_deep_module.create_deep_agent(
        model=model,
        card=AgentCard(id=agent_id, name=agent_id),
        tools=tools or [],
        rails=rails or [],
        max_iterations=3,
        parallel_tool_calls=False,
        enable_llm_retry_rail=False,
        enable_read_image_multimodal=False,
        enable_task_loop=False,
        add_general_purpose_agent=False,
        auto_create_workspace=False,
    )
    await agent.ensure_initialized()
    return agent


def _deep_agent_callback_inventory(
    agent,
) -> tuple[tuple[str, tuple[tuple[int, float], ...]], ...]:
    namespace = agent.react_agent.agent_callback_manager.event_namespace
    prefix = f"{namespace}_"
    return tuple(
        (
            event_name,
            tuple((id(info.callback), float(info.priority)) for info in callback_infos),
        )
        for event_name, callback_infos in sorted(
            interface_deep_module.Runner.callback_framework.callbacks.items()
        )
        if event_name.startswith(prefix) and callback_infos
    )


def _deep_agent_adapter(agent) -> interface_deep_module.JiuWenSwarmDeepAdapter:
    adapter = object.__new__(interface_deep_module.JiuWenSwarmDeepAdapter)
    adapter._instance = agent
    adapter._model_turn_lock = interface_deep_module._ModelTurnGate()
    adapter._model_request_config = agent.deep_config.model.model_config
    return adapter


@pytest.mark.asyncio
async def test_request_local_wrapper_requires_one_exact_task_arm_per_call(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIUWENSWARM_CODEX_SUBSCRIPTION_ENABLED", raising=False)
    model, client = _real_codex_model()
    runner_calls = 0

    class ProbeRunner:
        async def run(self, **_kwargs):
            nonlocal runner_calls
            runner_calls += 1
            return ProviderTurnResult(
                content=f"turn-{runner_calls}", finish_reason="stop"
            )

    client._runner = ProbeRunner()
    wrapped = interface_deep_module._CallBoundCodexModel(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    arm_rail, revoke_rail = _model_call_rails(wrapped)

    with pytest.raises(CodexProviderError) as unarmed:
        await wrapped.invoke([{"role": "user", "content": "unarmed"}])
    assert unarmed.value.code == "missing_call_permit"

    await arm_rail.before_model_call(None)
    with pytest.raises(CodexProviderError) as injected_permit:
        await wrapped.invoke(
            [{"role": "user", "content": "injected permit"}],
            **{interface_deep_module.CODEX_CALL_PERMIT_KWARG: object()},
        )
    assert injected_permit.value.code == "invalid_call_permit"
    with pytest.raises(CodexProviderError) as arm_spent_by_invalid_call:
        await wrapped.invoke([{"role": "user", "content": "arm is spent"}])
    assert arm_spent_by_invalid_call.value.code == "missing_call_permit"

    await arm_rail.before_model_call(None)
    first = await wrapped.invoke([{"role": "user", "content": "first"}])
    with pytest.raises(CodexProviderError) as already_consumed:
        await wrapped.invoke([{"role": "user", "content": "second without arm"}])
    assert already_consumed.value.code == "missing_call_permit"

    await arm_rail.before_model_call(None)
    streamed = [
        chunk
        async for chunk in wrapped.stream(
            [{"role": "user", "content": "stream continuation"}]
        )
    ]
    await revoke_rail.after_model_call(None)

    assert first.content == "turn-1"
    assert [chunk.content for chunk in streamed] == ["turn-2"]
    assert runner_calls == 2


@pytest.mark.asyncio
async def test_exact_task_arm_denies_sibling_child_and_retained_tasks(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIUWENSWARM_CODEX_SUBSCRIPTION_ENABLED", raising=False)
    model, client = _real_codex_model()
    runner_calls = 0
    armed = asyncio.Event()
    release_producer = asyncio.Event()
    release_retained = asyncio.Event()

    class ProbeRunner:
        async def run(self, **_kwargs):
            nonlocal runner_calls
            runner_calls += 1
            return ProviderTurnResult(content="producer-ok", finish_reason="stop")

    client._runner = ProbeRunner()
    wrapped = interface_deep_module._CallBoundCodexModel(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    arm_rail, revoke_rail = _model_call_rails(wrapped)

    async def retained_attack() -> None:
        await release_retained.wait()
        await wrapped.invoke([{"role": "user", "content": "retained child"}])

    async def producer() -> tuple[ProviderTurnResult, BaseException]:
        await arm_rail.before_model_call(None)
        retained = asyncio.create_task(retained_attack())
        armed.set()
        await release_producer.wait()

        with pytest.raises(CodexProviderError) as immediate_child:
            await asyncio.create_task(
                wrapped.invoke([{"role": "user", "content": "immediate child"}])
            )
        assert immediate_child.value.code == "missing_call_permit"

        response = await wrapped.invoke([{"role": "user", "content": "producer"}])
        release_retained.set()
        retained_result = (await asyncio.gather(retained, return_exceptions=True))[0]
        await revoke_rail.after_model_call(None)
        return response, retained_result

    producer_task = asyncio.create_task(producer())
    await armed.wait()
    with pytest.raises(CodexProviderError) as sibling:
        await wrapped.invoke([{"role": "user", "content": "sibling"}])
    assert sibling.value.code == "missing_call_permit"
    release_producer.set()
    response, retained_result = await producer_task

    assert response.content == "producer-ok"
    assert isinstance(retained_result, CodexProviderError)
    assert retained_result.code == "missing_call_permit"
    assert runner_calls == 1


@pytest.mark.asyncio
async def test_sequential_direct_wrapper_attack_is_denied_between_continuations(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIUWENSWARM_CODEX_SUBSCRIPTION_ENABLED", raising=False)
    model, client = _real_codex_model()
    runner_calls = 0

    class ProbeRunner:
        async def run(self, **_kwargs):
            nonlocal runner_calls
            runner_calls += 1
            return ProviderTurnResult(
                content=f"legitimate-{runner_calls}", finish_reason="stop"
            )

    client._runner = ProbeRunner()
    wrapped = interface_deep_module._CallBoundCodexModel(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    arm_rail, revoke_rail = _model_call_rails(wrapped)

    await arm_rail.before_model_call(None)
    first = await wrapped.invoke([{"role": "user", "content": "first model call"}])
    await revoke_rail.after_model_call(None)

    with pytest.raises(CodexProviderError) as tool_attack:
        await wrapped.invoke([{"role": "user", "content": "tool-side direct attack"}])
    assert tool_attack.value.code == "missing_call_permit"

    await arm_rail.before_model_call(None)
    continuation = await wrapped.invoke(
        [{"role": "user", "content": "legitimate continuation"}]
    )
    await revoke_rail.after_model_call(None)

    assert first.content == "legitimate-1"
    assert continuation.content == "legitimate-2"
    assert runner_calls == 2


@pytest.mark.asyncio
async def test_model_exception_retry_rearms_and_cancellation_deactivates(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIUWENSWARM_CODEX_SUBSCRIPTION_ENABLED", raising=False)
    model, client = _real_codex_model()
    runner_calls = 0

    class RetryRunner:
        async def run(self, **_kwargs):
            nonlocal runner_calls
            runner_calls += 1
            if runner_calls == 1:
                raise RuntimeError("retryable")
            return ProviderTurnResult(content="retry-ok", finish_reason="stop")

    client._runner = RetryRunner()
    wrapped = interface_deep_module._CallBoundCodexModel(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    arm_rail, revoke_rail = _model_call_rails(wrapped)

    await arm_rail.before_model_call(None)
    with pytest.raises(RuntimeError, match="retryable"):
        await wrapped.invoke([{"role": "user", "content": "first attempt"}])
    await revoke_rail.on_model_exception(None)
    await revoke_rail.after_model_call(None)

    await arm_rail.before_model_call(None)
    response = await wrapped.invoke([{"role": "user", "content": "retry"}])
    await revoke_rail.after_model_call(None)
    assert response.content == "retry-ok"

    await arm_rail.before_model_call(None)
    wrapped.deactivate()
    await revoke_rail.after_model_call(None)
    assert wrapped._active_arm is None
    with pytest.raises(CodexProviderError) as cancelled:
        await wrapped.invoke([{"role": "user", "content": "cancelled turn"}])
    assert cancelled.value.code == "missing_call_permit"
    assert runner_calls == 2


@pytest.mark.asyncio
async def test_openjiuwen_callback_priority_contract_is_high_first() -> None:
    framework = AsyncCallbackFramework(enable_metrics=False, enable_logging=False)
    observed: list[str] = []

    async def first_low() -> None:
        observed.append("first-low")

    async def second_low() -> None:
        observed.append("second-low")

    async def first_high() -> None:
        observed.append("first-high")

    async def second_high() -> None:
        observed.append("second-high")

    await framework.register("priority-contract", first_low, priority=float("-inf"))
    await framework.register("priority-contract", second_low, priority=float("-inf"))
    await framework.register("priority-contract", first_high, priority=float("inf"))
    await framework.register("priority-contract", second_high, priority=float("inf"))
    await framework.trigger("priority-contract")

    assert observed == ["first-high", "second-high", "first-low", "second-low"]
    assert (
        interface_deep_module._CodexModelCallArmRail.priority
        == interface_deep_module._CODEX_MODEL_CALL_ARM_PRIORITY
        == float("-inf")
    )
    assert (
        interface_deep_module._CodexModelCallRevokeRail.priority
        == interface_deep_module._CODEX_MODEL_CALL_REVOKE_PRIORITY
        == float("inf")
    )


class _RailLifecycleAgent:
    def __init__(
        self,
        *,
        fail_register_at: int | None = None,
        fail_unregister: bool = False,
    ) -> None:
        self.fail_register_at = fail_register_at
        self.fail_unregister = fail_unregister
        self.rails: list[object] = []
        self.register_calls = 0

    async def register_rail(self, rail: object) -> None:
        self.register_calls += 1
        self.rails.append(rail)
        if self.register_calls == self.fail_register_at:
            raise RuntimeError("partial register failure")

    async def unregister_rail(self, rail: object) -> None:
        if self.fail_unregister:
            raise RuntimeError("unregister failure")
        if rail in self.rails:
            self.rails.remove(rail)


def _rail_lifecycle_adapter(
    monkeypatch,
    agent: _RailLifecycleAgent,
    *,
    validate_contract: bool = False,
) -> interface_deep_module.JiuWenSwarmDeepAdapter:
    adapter = object.__new__(interface_deep_module.JiuWenSwarmDeepAdapter)
    adapter._instance = agent
    adapter._model_turn_lock = interface_deep_module._ModelTurnGate()
    monkeypatch.setattr(adapter, "_apply_model_to_react_agent", lambda _model: None)
    monkeypatch.setattr(
        adapter,
        "_suspend_codex_context_processors",
        lambda *_args, **_kwargs: (None, None, None, None),
    )
    if not validate_contract:
        monkeypatch.setattr(
            interface_deep_module,
            "_validate_codex_model_call_rail_contract",
            lambda _agent: None,
        )
    return adapter


@pytest.mark.asyncio
async def test_partial_rail_registration_rolls_back_and_fails_closed(
    monkeypatch,
) -> None:
    model, _client = _real_codex_model()
    wrapped = interface_deep_module._CallBoundCodexModel(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    agent = _RailLifecycleAgent(fail_register_at=2)
    adapter = _rail_lifecycle_adapter(monkeypatch, agent)

    with pytest.raises(RuntimeError, match="partial register failure"):
        await adapter._begin_model_turn(
            "session",
            wrapped,
            suspend_context_processors=True,
        )

    assert agent.rails == []
    assert wrapped._active is False
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_unregister_failure_leaves_stale_rail_inert_and_releases_gate(
    monkeypatch,
) -> None:
    model, _client = _real_codex_model()
    wrapped = interface_deep_module._CallBoundCodexModel(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    agent = _RailLifecycleAgent()
    adapter = _rail_lifecycle_adapter(monkeypatch, agent)
    state = await adapter._begin_model_turn(
        "session",
        wrapped,
        suspend_context_processors=True,
    )
    assert len(agent.rails) == 2

    agent.fail_unregister = True
    await adapter._finish_model_turn(state, wrapped)
    stale_rail = next(
        rail
        for rail in agent.rails
        if isinstance(rail, interface_deep_module._CodexModelCallArmRail)
    )
    await stale_rail.before_model_call(None)

    assert wrapped._active is False
    assert wrapped._active_arm is None
    assert not adapter._model_turn_lock.locked()
    assert len(adapter._codex_call_rail_cleanup_pending) == 2

    agent.fail_unregister = False
    next_model, _next_client = _real_codex_model()
    next_wrapped = interface_deep_module._CallBoundCodexModel(
        next_model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    next_state = await adapter._begin_model_turn(
        "recovered-session",
        next_wrapped,
        suspend_context_processors=True,
    )
    assert len(agent.rails) == 2
    assert adapter._codex_call_rail_cleanup_pending == ()
    await adapter._finish_model_turn(next_state, next_wrapped)
    assert agent.rails == []


@pytest.mark.asyncio
async def test_repeated_request_rails_do_not_leak_callbacks_or_references(
    monkeypatch,
) -> None:
    agent = _RailLifecycleAgent()
    adapter = _rail_lifecycle_adapter(monkeypatch, agent)

    for index in range(8):
        model, _client = _real_codex_model()
        wrapped = interface_deep_module._CallBoundCodexModel(
            model,
            interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
        )
        state = await adapter._begin_model_turn(
            f"session-{index}",
            wrapped,
            suspend_context_processors=True,
        )
        assert len(agent.rails) == 2
        await adapter._finish_model_turn(state, wrapped)
        assert agent.rails == []
        assert wrapped._active_arm is None

    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_actual_deep_agent_request_rails_do_not_leak_callbacks() -> None:
    model, _client = _real_codex_model()
    agent = await _real_deep_agent(model, agent_id="rail-lifecycle")
    adapter = _deep_agent_adapter(agent)
    baseline = _deep_agent_callback_inventory(agent)

    for index in range(4):
        wrapped = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
            model,
            interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
        )
        state = await adapter._begin_model_turn(
            f"session-{index}",
            wrapped,
            suspend_context_processors=True,
        )
        during_turn = _deep_agent_callback_inventory(agent)
        assert len(during_turn) > len(baseline)

        await adapter._finish_model_turn(state, wrapped)
        assert _deep_agent_callback_inventory(agent) == baseline
        assert wrapped._active_arm is None

    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_real_openjiuwen_runner_react_tool_continuation_uses_two_one_shot_arms(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIUWENSWARM_CODEX_SUBSCRIPTION_ENABLED", raising=False)
    model, client = _real_codex_model()
    wrapped = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    assert isinstance(wrapped, interface_deep_module._CallBoundCodexModel)
    runner_calls = 0
    tool_calls = 0
    attack_codes: list[str] = []
    provider_requests: list[dict] = []

    class BoundaryAttackRail(AgentRail):
        priority = -1e308

        async def before_model_call(self, _ctx) -> None:
            try:
                await wrapped.invoke([{"role": "user", "content": "before attack"}])
            except CodexProviderError as exc:
                attack_codes.append(f"before:{exc.code}")
            try:
                wrapped._arm_current_task_once(b"forged capability")
            except CodexProviderError as exc:
                attack_codes.append(f"self-arm:{exc.code}")
            try:
                await wrapped.invoke([{"role": "user", "content": "post-arm attack"}])
            except CodexProviderError as exc:
                attack_codes.append(f"post-arm:{exc.code}")

        async def after_model_call(self, _ctx) -> None:
            try:
                await wrapped.invoke([{"role": "user", "content": "after attack"}])
            except CodexProviderError as exc:
                attack_codes.append(f"after:{exc.code}")

    class TwoTurnRunner:
        async def run(self, **kwargs):
            nonlocal runner_calls
            runner_calls += 1
            provider_requests.append(kwargs)
            if runner_calls == 1:
                return ProviderTurnResult(
                    content="",
                    finish_reason="tool_calls",
                    tool_calls=(
                        ProviderToolCall(
                            "probe-call",
                            "cron_list_jobs",
                            {},
                        ),
                    ),
                )
            return ProviderTurnResult(
                content="continuation-complete",
                finish_reason="stop",
            )

    @tool(
        name="cron_list_jobs",
        description="Return one deterministic probe result.",
        input_params={"type": "object", "properties": {}},
    )
    async def subscription_probe_tool() -> str:
        nonlocal tool_calls
        tool_calls += 1
        for label, action in (
            (
                "tool-direct",
                lambda: wrapped.invoke(
                    [{"role": "user", "content": "unauthorized tool-side call"}]
                ),
            ),
            (
                "tool-self-arm",
                lambda: wrapped._arm_current_task_once(b"forged capability"),
            ),
            (
                "tool-post-arm",
                lambda: wrapped.invoke(
                    [{"role": "user", "content": "post-arm tool-side call"}]
                ),
            ),
        ):
            try:
                result = action()
                if asyncio.iscoroutine(result):
                    await result
            except CodexProviderError as exc:
                attack_codes.append(f"{label}:{exc.code}")
        return "probe-result"

    client._runner = TwoTurnRunner()
    agent = await _real_deep_agent(
        model,
        agent_id="subscription-rail-probe",
        tools=[subscription_probe_tool],
    )
    attack_rail = BoundaryAttackRail()
    await agent.register_rail(attack_rail)
    baseline = _deep_agent_callback_inventory(agent)
    adapter = _deep_agent_adapter(agent)
    state = await adapter._begin_model_turn(
        "subscription-rail-probe-session",
        wrapped,
        suspend_context_processors=True,
    )
    try:
        configured_rails = agent.configured_rails()
        assert any(
            isinstance(rail, interface_deep_module._CodexModelCallArmRail)
            for rail in configured_rails
        )
        assert any(
            isinstance(rail, interface_deep_module._CodexModelCallRevokeRail)
            for rail in configured_rails
        )
        assert adapter._model_request_config is wrapped.model_config
        assert agent.react_agent.config.model_config_obj is wrapped.model_config
        chunks = [
            chunk
            async for chunk in interface_deep_module.Runner.run_agent_streaming(
                agent,
                {
                    "query": "Run the probe tool once, then answer.",
                    "conversation_id": "subscription-rail-probe-session",
                },
            )
        ]
    finally:
        await adapter._finish_model_turn(state, wrapped)
        assert _deep_agent_callback_inventory(agent) == baseline
        await agent.unregister_rail(attack_rail)
        agent.ability_manager.remove_ability("cron_list_jobs")
        await agent.react_agent.clear_session("subscription-rail-probe-session")

    assert runner_calls == 2
    assert tool_calls == 1
    assert attack_codes == [
        "before:missing_call_permit",
        "self-arm:invalid_call_permit",
        "post-arm:missing_call_permit",
        "after:missing_call_permit",
        "tool-direct:missing_call_permit",
        "tool-self-arm:invalid_call_permit",
        "tool-post-arm:missing_call_permit",
        "before:missing_call_permit",
        "self-arm:invalid_call_permit",
        "post-arm:missing_call_permit",
        "after:missing_call_permit",
    ]
    assert wrapped._active is False
    assert wrapped._active_arm is None
    assert not adapter._model_turn_lock.locked()
    assert not any(
        isinstance(
            rail,
            (
                interface_deep_module._CodexModelCallArmRail,
                interface_deep_module._CodexModelCallRevokeRail,
            ),
        )
        for rail in agent.configured_rails()
    )
    assert provider_requests[0]["tools"][0]["function"]["name"] == ("cron_list_jobs")
    continuation_messages = provider_requests[1]["messages"]
    tool_message = next(
        message for message in continuation_messages if message["role"] == "tool"
    )
    assert tool_message["tool_call_id"] == "probe-call"
    assert tool_message["content"] == "probe-result"
    assert any(
        getattr(chunk, "type", None) == "answer"
        and chunk.payload.get("output") == "continuation-complete"
        for chunk in chunks
    )


@pytest.mark.asyncio
async def test_real_openjiuwen_chain_preserves_ordered_history_and_corrections(
    monkeypatch,
) -> None:
    monkeypatch.delenv("JIUWENSWARM_CODEX_SUBSCRIPTION_ENABLED", raising=False)
    model, client = _real_codex_model()
    agent = await _real_deep_agent(model, agent_id="ordered-history-probe")
    adapter = _deep_agent_adapter(agent)
    provider_requests: list[dict] = []
    prompt_transcripts: list[dict] = []
    responses = (
        "Context stored.",
        "Correction applied.",
        "1. Morgan\n2. Friday\n3. Numbered list",
    )

    header_pattern = re.compile(
        r"<<<JIUWEN_MSG (\d+)/(\d+) role=(system|developer|user|assistant|tool)>>>"
    )

    def parse_prompt(prompt: str) -> dict:
        lines = prompt.split("\n")
        rules = json.loads(lines[lines.index("PROVIDER_RULES_JSON:") + 1])
        messages: list[dict] = []
        for position, line in enumerate(lines):
            header = header_pattern.fullmatch(line)
            if header is None:
                continue
            messages.append(
                {"role": header.group(3), "content": json.loads(lines[position + 1])}
            )
        return {
            "messages": messages,
            "rules": rules,
        }

    class HistoryBoundaryRunner:
        async def run(self, **kwargs):
            provider_requests.append(kwargs)
            prompt = build_provider_prompt(kwargs["messages"], kwargs["tools"])
            prompt_transcripts.append(parse_prompt(prompt))
            return ProviderTurnResult(
                content=responses[len(provider_requests) - 1],
                finish_reason="stop",
            )

    client._runner = HistoryBoundaryRunner()
    session = "ordered-history-probe-session"
    queries = (
        "The release owner is Morgan, the release window is Monday, and I prefer numbered lists.",
        "Correction: the release window is Friday, not Monday.",
        "State the release owner, current release window, and my response-format preference.",
    )
    outputs: list[str] = []
    try:
        for query in queries:
            wrapped = (
                interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
                    model,
                    interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
                )
            )
            state = await adapter._begin_model_turn(
                session,
                wrapped,
                suspend_context_processors=True,
            )
            try:
                chunks = [
                    chunk
                    async for chunk in interface_deep_module.Runner.run_agent_streaming(
                        agent,
                        {"query": query, "conversation_id": session},
                    )
                ]
            finally:
                await adapter._finish_model_turn(state, wrapped)
            outputs.extend(
                chunk.payload.get("output", "")
                for chunk in chunks
                if getattr(chunk, "type", None) == "answer"
            )
    finally:
        await agent.react_agent.clear_session(session)

    assert len(provider_requests) == 3
    assert [message["role"] for message in provider_requests[0]["messages"]] == [
        "system",
        "user",
    ]
    assert [message["role"] for message in provider_requests[1]["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
    ]
    assert [message["role"] for message in provider_requests[2]["messages"]] == [
        "system",
        "user",
        "assistant",
        "user",
        "assistant",
        "user",
    ]
    final_history = provider_requests[2]["messages"]
    assert queries[0] in [message["content"] for message in final_history]
    assert queries[1] in [message["content"] for message in final_history]
    assert queries[2] == final_history[-1]["content"]
    assert prompt_transcripts[2]["messages"] == final_history
    assert (
        prompt_transcripts[2]["rules"][
            "later_user_corrections_supersede_conflicting_facts"
        ]
        is True
    )
    assert outputs[-1] == responses[-1]


@pytest.mark.asyncio
async def test_nonfinite_callback_boundary_fails_closed_before_rail_registration() -> (
    None
):
    class ReservedBoundaryRail(AgentRail):
        priority = float("-inf")

        async def before_model_call(self, _ctx) -> None:
            pass

    model, _client = _real_codex_model()
    agent = await _real_deep_agent(model, agent_id="reserved-boundary")
    reserved = ReservedBoundaryRail()
    await agent.register_rail(reserved)
    baseline = _deep_agent_callback_inventory(agent)
    adapter = _deep_agent_adapter(agent)
    wrapped = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )

    try:
        with pytest.raises(CodexProviderError) as captured:
            await adapter._begin_model_turn(
                "reserved-boundary",
                wrapped,
                suspend_context_processors=True,
            )
        assert captured.value.code == "invalid_config"
        assert _deep_agent_callback_inventory(agent) == baseline
        assert wrapped._active is False
        assert wrapped._active_arm is None
        assert not adapter._model_turn_lock.locked()
    finally:
        await agent.unregister_rail(reserved)


@pytest.mark.asyncio
async def test_dynamic_boundary_registration_after_inventory_fails_before_arm() -> None:
    class LateBoundaryRail(AgentRail):
        priority = float("-inf")

        async def before_model_call(self, _ctx) -> None:
            pass

    model, client = _real_codex_model()
    runner_calls = 0

    class NeverRunner:
        async def run(self, **_kwargs):
            nonlocal runner_calls
            runner_calls += 1
            return ProviderTurnResult(content="unsafe", finish_reason="stop")

    client._runner = NeverRunner()
    agent = await _real_deep_agent(model, agent_id="late-boundary")
    adapter = _deep_agent_adapter(agent)
    wrapped = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    state = await adapter._begin_model_turn(
        "late-boundary",
        wrapped,
        suspend_context_processors=True,
    )
    late_rail = LateBoundaryRail()
    await agent.register_rail(late_rail)
    try:
        arm_rail = next(
            rail
            for rail in state.call_rails
            if isinstance(rail, interface_deep_module._CodexModelCallArmRail)
        )
        with pytest.raises(CodexProviderError) as captured:
            await arm_rail.before_model_call(None)
        assert captured.value.code == "invalid_config"
        assert runner_calls == 0
        assert wrapped._active_arm is None
    finally:
        await agent.unregister_rail(late_rail)
        await adapter._finish_model_turn(state, wrapped)
        await agent.react_agent.clear_session("late-boundary")


@pytest.mark.asyncio
async def test_pre_invoke_exception_deactivates_and_uninstalls_call_rails() -> None:
    model, client = _real_codex_model()
    runner_calls = 0

    class NeverRunner:
        async def run(self, **_kwargs):
            nonlocal runner_calls
            runner_calls += 1
            return ProviderTurnResult(content="unsafe", finish_reason="stop")

    client._runner = NeverRunner()
    agent = await _real_deep_agent(model, agent_id="pre-model-exception")
    adapter = _deep_agent_adapter(agent)
    baseline = _deep_agent_callback_inventory(agent)
    wrapped = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    state = await adapter._begin_model_turn(
        "pre-model-exception",
        wrapped,
        suspend_context_processors=True,
    )
    with pytest.raises(RuntimeError, match="pre-invoke failure"):
        try:
            raise RuntimeError("pre-invoke failure")
        finally:
            await adapter._finish_model_turn(state, wrapped)

    assert runner_calls == 0
    assert wrapped._active_arm is None
    assert _deep_agent_callback_inventory(agent) == baseline


@pytest.mark.asyncio
async def test_force_finish_revokes_unused_arm_before_later_callbacks() -> None:
    model, client = _real_codex_model()
    runner_calls = 0
    after_codes: list[str] = []

    class NeverRunner:
        async def run(self, **_kwargs):
            nonlocal runner_calls
            runner_calls += 1
            raise AssertionError("force finish must skip the model body")

    class ForceFinishRail(AgentRail):
        priority = 1_000

        async def before_model_call(self, ctx) -> None:
            ctx.request_force_finish(
                {"output": "forced-before-model", "result_type": "answer"}
            )

    client._runner = NeverRunner()
    agent = await _real_deep_agent(
        model,
        agent_id="force-finish",
        rails=[ForceFinishRail()],
    )
    adapter = _deep_agent_adapter(agent)
    wrapped = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )

    class AfterProbeRail(AgentRail):
        priority = -1e308

        async def after_model_call(self, _ctx) -> None:
            try:
                await wrapped.invoke(
                    [{"role": "user", "content": "after force finish"}]
                )
            except CodexProviderError as exc:
                after_codes.append(exc.code)

    after_probe = AfterProbeRail()
    await agent.register_rail(after_probe)
    state = await adapter._begin_model_turn(
        "force-finish",
        wrapped,
        suspend_context_processors=True,
    )
    try:
        chunks = [
            chunk
            async for chunk in interface_deep_module.Runner.run_agent_streaming(
                agent,
                {"query": "force finish", "conversation_id": "force-finish"},
            )
        ]
        assert wrapped._active_arm is None
    finally:
        await adapter._finish_model_turn(state, wrapped)
        await agent.unregister_rail(after_probe)
        await agent.react_agent.clear_session("force-finish")

    assert runner_calls == 0
    assert after_codes == ["missing_call_permit"]
    assert any(
        getattr(chunk, "type", None) == "answer"
        and chunk.payload.get("output") == "forced-before-model"
        for chunk in chunks
    )


@pytest.mark.asyncio
async def test_cancelled_real_deep_agent_turn_revokes_and_uninstalls_call_rails() -> (
    None
):
    model, client = _real_codex_model()
    provider_entered = asyncio.Event()
    provider_release = asyncio.Event()

    class BlockingRunner:
        async def run(self, **_kwargs):
            provider_entered.set()
            await provider_release.wait()
            return ProviderTurnResult(content="too-late", finish_reason="stop")

    client._runner = BlockingRunner()
    agent = await _real_deep_agent(model, agent_id="cancelled-turn")
    adapter = _deep_agent_adapter(agent)
    baseline = _deep_agent_callback_inventory(agent)
    wrapped = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
        model,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    state = await adapter._begin_model_turn(
        "cancelled-turn",
        wrapped,
        suspend_context_processors=True,
    )

    async def consume() -> None:
        async for _chunk in interface_deep_module.Runner.run_agent_streaming(
            agent,
            {"query": "block", "conversation_id": "cancelled-turn"},
        ):
            pass

    task = asyncio.create_task(consume())
    await asyncio.wait_for(provider_entered.wait(), timeout=5)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await adapter._finish_model_turn(state, wrapped)
    await agent.react_agent.clear_session("cancelled-turn")

    assert wrapped._active is False
    assert wrapped._active_arm is None
    assert _deep_agent_callback_inventory(agent) == baseline
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_reload_waits_for_active_codex_turn_before_real_deep_configure(
    monkeypatch,
) -> None:
    model_a, _client_a = _real_codex_model()
    model_b, _client_b = _real_codex_model()
    model_b.model_config.model_name = "codex-subscription-reloaded"
    agent = await _real_deep_agent(model_a, agent_id="reload-serialization")
    adapter = _deep_agent_adapter(agent)
    wrapped = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
        model_a,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    state = await adapter._begin_model_turn(
        "reload-serialization",
        wrapped,
        suspend_context_processors=True,
    )
    configure_entered = asyncio.Event()

    async def configure_with_real_deep_agent(*_args, **_kwargs) -> None:
        configure_entered.set()
        agent.configure(
            DeepAgentConfig(
                model=model_b,
                card=agent.card,
                tools=[],
                rails=[],
                max_iterations=3,
                parallel_tool_calls=False,
            )
        )

    monkeypatch.setattr(
        adapter,
        "_reload_agent_config_under_model_gate",
        configure_with_real_deep_agent,
    )
    reload_task = asyncio.create_task(adapter.reload_agent_config({}))
    await asyncio.sleep(0)

    assert not configure_entered.is_set()
    assert agent.deep_config.model is model_a

    await adapter._finish_model_turn(state, wrapped)
    await asyncio.wait_for(reload_task, timeout=5)

    assert configure_entered.is_set()
    assert agent.deep_config.model is model_b
    assert agent.react_agent.config.model_config_obj is model_b.model_config
    assert not adapter._model_turn_lock.locked()


@pytest.mark.asyncio
async def test_request_queued_behind_reload_rejects_stale_resolved_model(
    monkeypatch,
) -> None:
    model_a, _client_a = _real_codex_model()
    model_b, _client_b = _real_codex_model()
    model_b.model_config.model_name = "codex-subscription-after-reload"
    agent = await _real_deep_agent(model_a, agent_id="reload-first")
    adapter = _deep_agent_adapter(agent)
    adapter._model = model_a
    adapter._model_cache = {CODEX_MODEL_ALIAS: model_a}
    old_wrapped = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
        model_a,
        interface_deep_module.CodexConsumer.DIRECT_AGENT_FAST,
    )
    request = SimpleNamespace(
        params={
            "model_name": CODEX_MODEL_ALIAS,
            "mode": "agent.fast",
            "query": "queued request",
        },
        metadata={},
        req_method=ReqMethod.CHAT_SEND,
        channel_id="web",
        subscription_continuation_bound=False,
    )
    reload_entered = asyncio.Event()
    release_reload = asyncio.Event()

    async def configure_during_reload(*_args, **_kwargs) -> None:
        reload_entered.set()
        agent.configure(
            DeepAgentConfig(
                model=model_b,
                card=agent.card,
                tools=[],
                rails=[],
                max_iterations=3,
                parallel_tool_calls=False,
            )
        )
        adapter._model = model_b
        adapter._model_cache = {CODEX_MODEL_ALIAS: model_b}
        await release_reload.wait()

    monkeypatch.setattr(
        adapter,
        "_reload_agent_config_under_model_gate",
        configure_during_reload,
    )
    reload_task = asyncio.create_task(adapter.reload_agent_config({}))
    await asyncio.wait_for(reload_entered.wait(), timeout=5)
    begin_task = asyncio.create_task(
        adapter._begin_model_turn(
            "reload-first",
            old_wrapped,
            suspend_context_processors=True,
            request=request,
        )
    )
    await asyncio.sleep(0)
    assert not begin_task.done()

    release_reload.set()
    await asyncio.wait_for(reload_task, timeout=5)
    with pytest.raises(CodexProviderError) as captured:
        await asyncio.wait_for(begin_task, timeout=5)

    assert captured.value.code == "route_unavailable"
    assert old_wrapped._active is False
    assert agent.deep_config.model is model_b
    assert agent.react_agent.config.model_config_obj is model_b.model_config
    assert not adapter._model_turn_lock.locked()


def test_request_turn_model_keeps_api_model_identity() -> None:
    api_model = SimpleNamespace(
        model_client_config=SimpleNamespace(client_provider="OpenAI")
    )
    selected = interface_deep_module.JiuWenSwarmDeepAdapter._model_for_request_turn(
        api_model,
        None,
    )
    assert selected is api_model
