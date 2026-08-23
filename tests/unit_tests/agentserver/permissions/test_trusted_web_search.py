# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for the JiuwenSwarm-owned structured free-search producer."""

from __future__ import annotations

import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from openjiuwen.core.foundation.llm import ToolCall
from openjiuwen.core.foundation.llm.schema.message import ToolMessage
from openjiuwen.core.single_agent.ability_manager import AbilityManager
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
)

from jiuwenswarm.agents.harness.common.rails.permissions.tool_invocation_key import (
    ToolInvocationKeyV1,
)
from jiuwenswarm.agents.harness.common.rails.permissions.trusted_search_urls import (
    SessionTrustedSearchUrls,
    bind_trusted_search_producer,
    clear_trusted_search_producer,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_permission_queue import (
    RootPermissionQueue,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_permission_queue_rail import (
    RootPermissionQueueRail,
    bind_root_permission_request,
    reset_root_permission_request,
)
from jiuwenswarm.agents.harness.common.rails.permissions.root_context import (
    RootDecisionContext,
    RootIntentTurn,
    RootIntentTurnKind,
    bind_root_decision_context,
    reset_root_decision_context,
)
from jiuwenswarm.server.runtime.agent_adapter import trusted_web_search
from jiuwenswarm.server.runtime.agent_adapter.trusted_web_search import (
    TrustedWebFreeSearchTool,
)
from tests.unit_tests.agentserver.permissions.auto_permission_test_support import (
    AutoPermissionInterruptRail,
    AutoReviewer,
    FakeBaseRail,
    PolicyEvaluation,
    ReviewerOutcome,
    StaticPolicyEvaluator,
    StaticReviewerClient,
)


def _key() -> ToolInvocationKeyV1:
    return ToolInvocationKeyV1(
        invocation_id="invoke-1",
        root_session_id="session-1",
        request_id="request-1",
        executor_kind="agent",
        execution_session_id="session-1",
        tool_call_id="call-1",
    )


def _effective_intent_context(
    text: str,
    *,
    request_id: str,
) -> RootDecisionContext:
    return RootDecisionContext(
        session_id="session-1",
        request_id=request_id,
        channel_id="web",
        trusted_turns=(
            RootIntentTurn(
                request_id=request_id,
                kind=RootIntentTurnKind.FRESH,
                text=text,
            ),
        ),
    )


class _ProductionSearchCallbacks:
    def __init__(
        self,
        queue_rail: RootPermissionQueueRail,
        permission_rail: AutoPermissionInterruptRail,
    ) -> None:
        self.queue_rail = queue_rail
        self.permission_rail = permission_rail

    async def execute(
        self,
        event: AgentCallbackEvent,
        ctx: AgentCallbackContext,
    ) -> None:
        if event is AgentCallbackEvent.BEFORE_TOOL_CALL:
            await self.queue_rail.before_tool_call(ctx)
            await self.permission_rail.before_tool_call(ctx)
        elif event is AgentCallbackEvent.AFTER_TOOL_CALL:
            await self.permission_rail.after_tool_call(ctx)
        elif event is AgentCallbackEvent.ON_TOOL_EXCEPTION:
            await self.queue_rail.on_tool_exception(ctx)


@pytest.mark.asyncio
async def test_adapter_records_only_host_structured_search_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://news.example.test/typhoon"
    ledger = SessionTrustedSearchUrls("session-1")
    bind_trusted_search_producer(
        ledger=ledger,
        key=_key(),
        tool_name="free_search",
        max_results=8,
    )
    monkeypatch.setattr(
        trusted_web_search,
        "run_free_search_structured",
        lambda query, max_results, timeout_seconds: (
            "duckduckgo_html",
            ({"title": "Result", "url": url, "snippet": "Summary"},),
        ),
    )
    tool = TrustedWebFreeSearchTool(agent_id="agent-1")

    result = await tool.invoke({"query": "typhoon"})

    assert tool.card.name == "free_search"
    assert url in result
    assert ledger.contains(root_session_id="session-1", url=url)
    clear_trusted_search_producer()


@pytest.mark.asyncio
async def test_root_queue_auto_permission_and_real_tool_execution_record_provenance(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    url = "https://news.example.test/typhoon"
    ledger = SessionTrustedSearchUrls("session-1")
    invocation_ids = iter(("invoke-production-search", "invoke-production-fetch"))
    queue = RootPermissionQueue(id_factory=lambda: next(invocation_ids))
    queue_rail = RootPermissionQueueRail(queue)
    reviewer = StaticReviewerClient(outcome=ReviewerOutcome.ALLOW_ONCE)
    permission_rail = AutoPermissionInterruptRail(
        base_rail=FakeBaseRail(),
        permission_config={"mode": "auto", "enabled": True},
        workspace_root=tmp_path,
        policy_evaluator=StaticPolicyEvaluator(
            PolicyEvaluation(level="ask", reason="policy_ask")
        ),
        auto_reviewer=AutoReviewer(client=reviewer),
        trusted_search_urls=ledger,
    )
    callbacks = _ProductionSearchCallbacks(queue_rail, permission_rail)
    manager = AbilityManager()
    tool = TrustedWebFreeSearchTool(agent_id="agent-1")
    monkeypatch.setattr(
        trusted_web_search,
        "run_free_search_structured",
        lambda query, max_results, timeout_seconds: (
            "duckduckgo_html",
            ({"title": "Result", "url": url, "snippet": "Summary"},),
        ),
    )

    async def execute_tool(**kwargs: Any) -> tuple[str, ToolMessage]:
        tool_call = kwargs["tool_call"]
        if tool_call.name == "free_search":
            result = await tool.invoke(json.loads(tool_call.arguments))
        else:
            result = "fetched"
        return result, ToolMessage(content=result, tool_call_id=tool_call.id)

    monkeypatch.setattr(manager, "_execute_single_tool_call", execute_tool)
    tool_call = ToolCall(
        id="call-1",
        type="function",
        name="free_search",
        arguments=json.dumps({"query": "typhoon", "max_results": 8}),
    )
    parent = AgentCallbackContext(
        agent=SimpleNamespace(agent_callback_manager=callbacks)
    )
    session = SimpleNamespace(session_id="session-1")
    token = bind_root_permission_request(
        root_session_id="session-1",
        request_id="request-1",
        runtime_mode="agent",
        agent_id="agent-1",
        enabled=True,
        queue=queue,
    )
    try:
        result = await manager.execute(parent, tool_call, session=session)
    finally:
        reset_root_permission_request(token)
        clear_trusted_search_producer()

    assert url in result[0][0]
    assert ledger.contains(root_session_id="session-1", url=url)

    fetch_call = ToolCall(
        id="call-fetch",
        type="function",
        name="fetch_webpage",
        arguments=json.dumps({"url": url}),
    )
    intent_token = bind_root_decision_context(
        _effective_intent_context(
            "Search public typhoon news and read the first result.",
            request_id="request-2",
        )
    )
    request_token = bind_root_permission_request(
        root_session_id="session-1",
        request_id="request-2",
        runtime_mode="agent",
        agent_id="agent-1",
        enabled=True,
        queue=queue,
    )
    try:
        fetch_result = await manager.execute(parent, fetch_call, session=session)
    finally:
        reset_root_permission_request(request_token)
        reset_root_decision_context(intent_token)

    assert fetch_result[0][0] == "fetched"
    assert len(reviewer.requests) == 1
    metadata = parent.extra["permission_reviewer_metadata_by_tool_call_id"][
        "call-fetch"
    ]
    assert metadata["decision_source"] == "deterministic_bounded_scope"
    assert metadata["host_route_source"] == "recent_search_result"
    assert metadata["reviewer_called"] is False


@pytest.mark.asyncio
async def test_adapter_failure_records_no_provenance(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    ledger = SessionTrustedSearchUrls("session-1")
    bind_trusted_search_producer(
        ledger=ledger,
        key=_key(),
        tool_name="free_search",
        max_results=8,
    )

    def fail(*args: object) -> object:
        raise RuntimeError("search unavailable")

    monkeypatch.setattr(trusted_web_search, "run_free_search_structured", fail)

    result = await TrustedWebFreeSearchTool(agent_id="agent-1").invoke(
        {"query": "typhoon"}
    )

    assert result.startswith("[ERROR]:")
    assert len(ledger) == 0
    clear_trusted_search_producer()


@pytest.mark.asyncio
async def test_adapter_keeps_provenance_when_rendering_fails_after_rows(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    url = "https://news.example.test/typhoon"
    ledger = SessionTrustedSearchUrls("session-1")
    bind_trusted_search_producer(
        ledger=ledger,
        key=_key(),
        tool_name="free_search",
        max_results=8,
    )
    monkeypatch.setattr(
        trusted_web_search,
        "run_free_search_structured",
        lambda query, max_results, timeout_seconds: (
            "duckduckgo_html",
            ({"title": "Result", "url": url, "snippet": "Summary"},),
        ),
    )

    def fail_render(**kwargs: object) -> str:
        raise RuntimeError("render failed")

    monkeypatch.setattr(trusted_web_search, "render_free_search_result", fail_render)

    with pytest.raises(RuntimeError, match="render failed"):
        await TrustedWebFreeSearchTool(agent_id="agent-1").invoke({"query": "typhoon"})

    assert ledger.contains(root_session_id="session-1", url=url)
    clear_trusted_search_producer()


def test_deep_and_code_register_only_the_trusted_free_search_adapter() -> None:
    repository_root = Path(__file__).resolve().parents[4]
    for relative_path in (
        "jiuwenswarm/server/runtime/agent_adapter/interface_deep.py",
        "jiuwenswarm/server/runtime/agent_adapter/interface_code.py",
    ):
        source = (repository_root / relative_path).read_text(encoding="utf-8")
        assert "TrustedWebFreeSearchTool" in source
        assert re.search(r"(?<!Trusted)WebFreeSearchTool\(", source) is None
