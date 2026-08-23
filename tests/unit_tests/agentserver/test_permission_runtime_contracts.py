# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Runtime characterization tests for permission integration contracts."""

from __future__ import annotations

from collections.abc import AsyncIterator
from copy import deepcopy
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from openjiuwen.agent_teams.rails.team_permission_rail import TeamPermissionRail
from openjiuwen.core.runner import Runner
from openjiuwen.core.session import InteractiveInput
from openjiuwen.core.single_agent import AgentCard
from openjiuwen.core.single_agent.interrupt.state import RESUME_USER_INPUT_KEY
from openjiuwen.core.single_agent.rail.base import (
    AgentCallbackContext,
    AgentCallbackEvent,
    AgentRail,
)
from openjiuwen.harness import DeepAgent, DeepAgentConfig
from openjiuwen.harness.rails.interrupt.interrupt_base import (
    ApproveResult,
    InterruptResult,
)
from openjiuwen.harness.rails.security.tool_security_rail import (
    PermissionInterruptRail,
)
from openjiuwen.harness.security.core import PermissionEngine
from openjiuwen.harness.security.host import ToolPermissionHost
from openjiuwen.harness.security.models import PermissionLevel

from jiuwenswarm.agents.harness.common.rails.interrupt.interrupt_helpers import (
    build_permission_rail,
)
from jiuwenswarm.common.schema.agent import (
    AgentRequest,
    AgentResponse,
    AgentResponseChunk,
)
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import (
    JiuWenSwarmDeepAdapter,
)


class _ReplaceablePermissionRail(AgentRail):
    """A distinct rail type used to characterize exact-type hot replacement."""

    def __init__(self, *, fail_initialization: bool = False) -> None:
        self._fail_initialization = fail_initialization
        self.callback_calls = 0

    def init(self, agent: DeepAgent) -> None:
        if self._fail_initialization:
            raise RuntimeError("candidate initialization failed")

    async def before_invoke(self, ctx: AgentCallbackContext) -> None:
        self.callback_calls += 1


@pytest.mark.asyncio
async def test_openjiuwen_hot_reload_replaces_same_type_rail_once() -> None:
    """The pinned SDK retires the old rail before registering its replacement."""
    old_rail = _ReplaceablePermissionRail()
    replacement_rail = _ReplaceablePermissionRail()
    agent = DeepAgent(AgentCard(name="permission-runtime-contract"))
    callback_event = agent._agent_callback_manager._get_agent_event(
        AgentCallbackEvent.BEFORE_INVOKE
    )

    try:
        agent.configure(
            DeepAgentConfig(
                rails=[old_rail],
                auto_create_workspace=False,
            )
        )
        await agent.ensure_initialized()
        assert agent._registered_rails == [old_rail]
        assert len(Runner.callback_framework.list_callbacks(callback_event)) == 1

        agent.configure(
            DeepAgentConfig(
                rails=[replacement_rail],
                auto_create_workspace=False,
            )
        )
        assert agent._registered_rails == []
        assert agent._stale_rails == [old_rail]
        assert agent._pending_rails == [replacement_rail]

        await agent.ensure_initialized()
        assert agent._registered_rails == [replacement_rail]
        assert agent._stale_rails == []
        assert agent._pending_rails == []
        assert len(Runner.callback_framework.list_callbacks(callback_event)) == 1

        await agent.ensure_initialized()
        assert agent._registered_rails == [replacement_rail]
        assert len(Runner.callback_framework.list_callbacks(callback_event)) == 1
    finally:
        await agent._agent_callback_manager.clear()


@pytest.mark.asyncio
async def test_openjiuwen_initialization_failure_blocks_public_invoke(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A failed candidate does not mark the agent initialized for invocation."""
    old_rail = _ReplaceablePermissionRail()
    failing_rail = _ReplaceablePermissionRail(fail_initialization=True)
    agent = DeepAgent(AgentCard(name="permission-runtime-failure-contract"))
    execute_round = AsyncMock()
    monkeypatch.setattr(agent, "_run_single_round_invoke", execute_round)

    try:
        agent.configure(
            DeepAgentConfig(
                rails=[old_rail],
                auto_create_workspace=False,
            )
        )
        await agent.ensure_initialized()

        agent.configure(
            DeepAgentConfig(
                rails=[failing_rail],
                auto_create_workspace=False,
            )
        )
        with pytest.raises(RuntimeError, match="candidate initialization failed"):
            await agent.invoke({"query": "must not execute"})

        assert agent._initialized is False
        assert old_rail not in agent._registered_rails
        assert failing_rail not in agent._registered_rails
        assert failing_rail in agent._pending_rails
        execute_round.assert_not_awaited()
    finally:
        await agent._agent_callback_manager.clear()


@pytest.mark.asyncio
async def test_adapter_candidate_build_failure_precedes_sdk_configure(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A candidate-build exception leaves the live rail and SDK config untouched."""
    from jiuwenswarm.server.runtime.agent_adapter import (
        interface_deep as interface_module,
    )

    adapter = JiuWenSwarmDeepAdapter()
    old_rail = object()
    adapter._permission_rail = old_rail
    adapter._instance = MagicMock()
    adapter._instance.configure = MagicMock()

    monkeypatch.setattr(interface_module, "clear_config_cache", MagicMock())
    monkeypatch.setattr(
        "openjiuwen.core.memory.lite.manager.aclose_memory_manager_cache",
        AsyncMock(),
    )
    for method_name in (
        "_refresh_multimodal_configs",
        "_sync_multimodal_tools_for_runtime",
        "_sync_paid_search_tool_for_runtime",
        "_sync_symphony_tools_for_runtime",
        "_sync_skill_retrieval_tools_for_runtime",
    ):
        monkeypatch.setattr(adapter, method_name, MagicMock())
    monkeypatch.setattr(adapter, "_create_model", MagicMock(return_value=MagicMock()))
    monkeypatch.setattr(
        adapter,
        "_sync_skill_retrieval_prompt_rail_for_runtime",
        AsyncMock(),
    )
    monkeypatch.setattr(
        adapter,
        "_filesystem_rail_enabled_for_profile",
        MagicMock(return_value=False),
    )
    monkeypatch.setattr(
        adapter,
        "_get_current_agent_rails",
        MagicMock(side_effect=RuntimeError("candidate build failed")),
    )

    with pytest.raises(RuntimeError, match="candidate build failed"):
        await adapter.reload_agent_config(
            {"react": {"agent_name": "main_agent"}},
            {},
        )

    assert adapter._permission_rail is old_rail
    adapter._instance.configure.assert_not_called()


@pytest.mark.asyncio
async def test_manual_resume_identity_survives_permission_rail_replacement() -> None:
    """The replacement rail resolves only the answer for the original tool-call id."""
    old_rail = build_permission_rail({"permissions": {"enabled": True}})
    replacement_rail = build_permission_rail({"permissions": {"enabled": True}})
    assert old_rail is not None
    assert replacement_rail is not None
    agent = DeepAgent(AgentCard(name="permission-manual-resume"))

    try:
        agent.configure(
            DeepAgentConfig(
                rails=[old_rail],
                auto_create_workspace=False,
            )
        )
        await agent.ensure_initialized()
        agent.configure(
            DeepAgentConfig(
                rails=[replacement_rail],
                auto_create_workspace=False,
            )
        )
        await agent.ensure_initialized()

        resume_input = InteractiveInput()
        resume_input.update(
            "tool-call-reload",
            {
                "approved": True,
                "auto_confirm": False,
                "persist_allow": False,
            },
        )
        ctx = AgentCallbackContext(
            agent=agent,
            extra={RESUME_USER_INPUT_KEY: resume_input},
        )
        tool_call = SimpleNamespace(
            id="tool-call-reload",
            name="bash",
            arguments={"command": "pwd"},
        )

        tool_call_id = replacement_rail._resolve_tool_call_id(tool_call)
        user_input = replacement_rail._get_user_input(ctx, tool_call_id)
        decision = await replacement_rail.resolve_interrupt(
            ctx,
            tool_call,
            user_input,
        )

        assert tool_call_id == "tool-call-reload"
        assert isinstance(decision, ApproveResult)
        assert replacement_rail._get_user_input(ctx, "different-call") is None
    finally:
        await agent._agent_callback_manager.clear()
        if agent._react_agent is not None:
            await agent._react_agent.agent_callback_manager.clear()


def test_team_permission_rail_keeps_leader_scoped_confirmation_contract() -> None:
    """Current TeamPermissionRail does not persist and records the leader owner."""
    rail = TeamPermissionRail(config={"enabled": True})

    response = rail.parse_confirm_payload(
        {
            "approved": True,
            "auto_confirm": True,
            "persist_allow": True,
        }
    )

    assert rail._persist_allow_always("bash", {"command": "pwd"}) is False
    assert response is not None
    assert response.approved is True
    assert response.decided_by == "leader"


def _native_file_guard_config(
    *,
    workspace_root: Path,
    denied_root: Path | None = None,
    allowed_root: Path | None = None,
) -> dict[str, object]:
    path_rules: list[dict[str, str]] = []
    if denied_root is not None:
        path_rules.append(
            {
                "path": denied_root.as_posix(),
                "read": "deny",
                "write": "deny",
                "exec": "deny",
                "match": "prefix",
            }
        )
    if allowed_root is not None:
        path_rules.append(
            {
                "path": allowed_root.as_posix(),
                "read": "allow",
                "write": "allow",
                "exec": "ask",
                "match": "prefix",
            }
        )
    return {
        "enabled": True,
        "schema": "tiered_policy",
        "defaults": {"*": "allow"},
        "tools": {"write_file": "allow"},
        "file_guard": {
            "enabled": True,
            "defaults": {"read": "ask", "write": "ask", "exec": "ask"},
            "workspace": {"read": "allow", "write": "allow", "exec": "ask"},
            "paths": path_rules,
        },
    }


@pytest.mark.asyncio
async def test_latest_openjiuwen_file_guard_combines_native_path_matrix(
    tmp_path: Path,
) -> None:
    """Pipeline B keeps workspace allow, external ask, and deny prefix terminal."""
    workspace_root = tmp_path / "workspace"
    denied_root = tmp_path / "external" / "denied"
    config = _native_file_guard_config(
        workspace_root=workspace_root,
        denied_root=denied_root,
    )
    engine = PermissionEngine(config=config, workspace_root=workspace_root)

    workspace_result = await engine.check_permission(
        "write_file",
        {"path": (workspace_root / "report.md").as_posix(), "content": "ok"},
    )
    external_result = await engine.check_permission(
        "write_file",
        {"path": (tmp_path / "external" / "report.md").as_posix(), "content": "ask"},
    )
    denied_result = await engine.check_permission(
        "write_file",
        {"path": (denied_root / "report.md").as_posix(), "content": "deny"},
    )

    assert workspace_result.permission == PermissionLevel.ALLOW
    assert external_result.permission == PermissionLevel.ASK
    assert denied_result.permission == PermissionLevel.DENY
    assert "file_guard:defaults" in str(external_result.matched_rule)
    assert f"file_guard:prefix:{denied_root.as_posix()}" in str(
        denied_result.matched_rule
    )


@pytest.mark.asyncio
async def test_same_permission_rail_reads_updated_host_snapshot(
    tmp_path: Path,
) -> None:
    """The next check on one rail rebuilds file_guard from the host snapshot."""
    workspace_root = tmp_path / "workspace"
    external_root = tmp_path / "external"
    snapshot = _native_file_guard_config(workspace_root=workspace_root)
    host = ToolPermissionHost(
        get_permissions_snapshot=lambda: deepcopy(snapshot),
        resolve_workspace_dir=lambda: workspace_root,
    )
    rail = PermissionInterruptRail(config=deepcopy(snapshot), host=host)
    tool_call = SimpleNamespace(
        id="tool-call-file-guard-refresh",
        name="write_file",
        arguments={
            "path": (external_root / "report.md").as_posix(),
            "content": "updated",
        },
    )
    ctx = SimpleNamespace(session=None)

    before = await rail.resolve_interrupt(ctx, tool_call, None)
    snapshot = _native_file_guard_config(
        workspace_root=workspace_root,
        allowed_root=external_root,
    )
    after = await rail.resolve_interrupt(ctx, tool_call, None)

    assert isinstance(before, InterruptResult)
    assert isinstance(after, ApproveResult)


@pytest.mark.asyncio
async def test_failed_file_guard_persistence_rolls_back_live_engine(
    tmp_path: Path,
) -> None:
    """A failed host persistence callback restores the prior path decision."""
    workspace_root = tmp_path / "workspace"
    external_path = tmp_path / "external" / "report.md"
    snapshot = _native_file_guard_config(workspace_root=workspace_root)
    persisted_candidates: list[dict[str, object]] = []

    def _reject_persistence(candidate: dict[str, object]) -> bool:
        persisted_candidates.append(deepcopy(candidate))
        return False

    rail = PermissionInterruptRail(
        config=deepcopy(snapshot),
        host=ToolPermissionHost(
            get_permissions_snapshot=lambda: deepcopy(snapshot),
            persist_allow_rule=_reject_persistence,
            resolve_workspace_dir=lambda: workspace_root,
        ),
    )
    args = {"path": external_path.as_posix(), "content": "blocked"}
    before = await rail._engine.check_permission("write_file", args)

    persisted = rail._persist_allow_always("write_file", args)
    after = await rail._engine.check_permission("write_file", args)

    assert before.permission == PermissionLevel.ASK
    assert persisted is False
    assert persisted_candidates
    assert persisted_candidates[0]["file_guard"] != snapshot["file_guard"]
    assert after.permission == PermissionLevel.ASK


class _SessionChild:
    def __init__(self) -> None:
        self.calls: list[tuple[str, AgentRequest, object | None]] = []
        self.active_count = 0
        self.request_reserved = False

    def _mark_session_active(self, _session_id: str) -> None:
        self.active_count += 1

    def _unmark_session_active(self, _session_id: str) -> None:
        self.active_count -= 1

    def _register_session_agent_task(self, _session_id: str) -> None:
        self.request_reserved = True

    def _unregister_session_agent_task(self, _session_id: str) -> None:
        self.request_reserved = False

    async def process_message_impl(
        self,
        request: AgentRequest,
        inputs: dict[str, object],
    ) -> AgentResponse:
        self.calls.append(("message", request, inputs))
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={"owner": "session-child"},
        )

    async def process_message_stream_impl(
        self,
        request: AgentRequest,
        inputs: dict[str, object],
    ) -> AsyncIterator[AgentResponseChunk]:
        self.calls.append(("stream", request, inputs))
        yield AgentResponseChunk(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={"owner": "session-child"},
            is_complete=True,
        )

    async def handle_user_answer(self, request: AgentRequest) -> AgentResponse:
        self.calls.append(("answer", request, None))
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
        )

    async def handle_heartbeat(self, request: AgentRequest) -> AgentResponse:
        self.calls.append(("heartbeat", request, None))
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
        )

    async def process_interrupt(self, request: AgentRequest) -> AgentResponse:
        self.calls.append(("interrupt", request, None))
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
        )


def _request(
    channel_id: str,
    *,
    session_id: str = "session-runtime-contract",
    params: dict[str, object] | None = None,
) -> AgentRequest:
    return AgentRequest(
        request_id=f"request-{channel_id}",
        channel_id=channel_id,
        session_id=session_id,
        params=params or {},
    )


def _install_session_child(
    monkeypatch: pytest.MonkeyPatch,
    parent: JiuWenSwarmDeepAdapter,
    child: _SessionChild,
) -> list[str | None]:
    lookups: list[str | None] = []

    async def _get_or_create(
        session_id: str | None,
        *,
        reserve_activity: bool = False,
    ) -> _SessionChild:
        lookups.append(session_id)
        if reserve_activity:
            child._register_session_agent_task(str(session_id or "default"))
        return child

    async def _evict_idle() -> None:
        return None

    monkeypatch.setattr(parent, "_get_or_create_session_adapter", _get_or_create)
    monkeypatch.setattr(parent, "_evict_idle_session_adapters", _evict_idle)
    return lookups


@pytest.mark.asyncio
@pytest.mark.parametrize("channel_id", ["web", "ws", "tui"])
async def test_non_stream_entrypoints_preserve_identity_and_use_session_child(
    monkeypatch: pytest.MonkeyPatch,
    channel_id: str,
) -> None:
    parent = JiuWenSwarmDeepAdapter()
    child = _SessionChild()
    lookups = _install_session_child(monkeypatch, parent, child)
    request = _request(channel_id)

    response = await parent.process_message_impl(request, {"query": "runtime-contract"})

    assert response.payload == {"owner": "session-child"}
    assert lookups == [request.session_id]
    assert child.calls == [("message", request, {"query": "runtime-contract"})]
    assert child.active_count == 0
    assert child.request_reserved is False


@pytest.mark.asyncio
async def test_stream_entrypoint_preserves_identity_and_uses_session_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = JiuWenSwarmDeepAdapter()
    child = _SessionChild()
    lookups = _install_session_child(monkeypatch, parent, child)
    request = _request("web")

    chunks = [
        chunk
        async for chunk in parent.process_message_stream_impl(
            request,
            {"query": "runtime-contract"},
        )
    ]

    assert [chunk.payload for chunk in chunks] == [{"owner": "session-child"}]
    assert lookups == [request.session_id]
    assert child.calls == [("stream", request, {"query": "runtime-contract"})]
    assert child.active_count == 0
    assert child.request_reserved is False


@pytest.mark.asyncio
async def test_control_entrypoints_use_the_same_session_child(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    parent = JiuWenSwarmDeepAdapter()
    child = _SessionChild()
    lookups = _install_session_child(monkeypatch, parent, child)
    answer = _request("web")
    heartbeat = _request("web", session_id="heartbeat-runtime-contract")
    pause = _request("tui", params={"intent": "pause"})

    await parent.handle_user_answer(answer)
    await parent.handle_heartbeat(heartbeat)
    await parent.process_interrupt(pause)

    assert lookups == [
        answer.session_id,
        heartbeat.session_id,
        pause.session_id,
    ]
    assert child.calls == [
        ("answer", answer, None),
        ("heartbeat", heartbeat, None),
        ("interrupt", pause, None),
    ]
