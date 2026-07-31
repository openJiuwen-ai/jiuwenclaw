# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

import asyncio
from pathlib import Path

from jiuwenclaw.agentserver.interface import JiuWenClaw
from jiuwenclaw.schema.agent import AgentRequest, AgentResponse, AgentResponseChunk
from jiuwenclaw.schema.message import ReqMethod


class _PermissionAdapter:
    def __init__(self) -> None:
        self.runtime_calls: list[str] = []

    async def handle_heartbeat(self, _request):
        return None

    async def process_message_impl(self, request, _inputs):
        self.runtime_calls.append(request.params["request_id"])
        return AgentResponse(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={"content": "", "files": []},
        )

    async def process_message_stream_impl(self, request, _inputs):
        self.runtime_calls.append(request.params["request_id"])
        yield AgentResponseChunk(
            request_id=request.request_id,
            channel_id=request.channel_id,
            payload={"event_type": "chat.delta", "content": "ok"},
        )


def _permission_request(continuation_id: str, *, request_id: str) -> AgentRequest:
    return AgentRequest(
        request_id=request_id,
        channel_id="web",
        session_id="session-1",
        agent_id="agent-1",
        req_method=ReqMethod.CHAT_SEND,
        params={
            "query": "",
            "request_id": continuation_id,
            "answers": [{"selected_options": ["approve"]}],
            "mode": "agent.plan",
        },
    )


def _build_claw(tmp_path: Path, adapter: _PermissionAdapter, monkeypatch) -> JiuWenClaw:
    claw = JiuWenClaw(user_workspace_dir=str(tmp_path), agent_id="agent-1")
    claw._adapter = adapter
    claw._sdk_name = "harness"

    monkeypatch.setattr(claw, "_build_inputs", lambda _request: ({}, "local", ""))
    monkeypatch.setattr(
        claw,
        "_apply_effective_project_dir_to_request",
        lambda *_args: None,
    )

    async def no_op(*_args, **_kwargs):
        return None

    monkeypatch.setattr(claw, "prepare_files_for_agent", no_op)
    monkeypatch.setattr(claw, "upload_agent_files", no_op)
    monkeypatch.setattr(claw, "_cleanup_request_scoped_mcp", no_op)
    return claw


def test_web_unary_duplicate_permission_runs_runtime_once(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        adapter = _PermissionAdapter()
        claw = _build_claw(tmp_path, adapter, monkeypatch)
        history_calls: list[str] = []
        monkeypatch.setattr(
            "jiuwenclaw.agentserver.interface.append_history_record",
            lambda **kwargs: history_calls.append(kwargs["role"]),
        )

        responses = await asyncio.gather(*(
            claw.process_message(
                _permission_request("permission-1", request_id=f"web-{index}")
            )
            for index in range(3)
        ))

        assert adapter.runtime_calls == ["permission-1"]
        assert history_calls.count("user") == 3
        assert sum(
            response.payload.get("code") == "duplicate_permission_response"
            for response in responses
        ) == 2
        await claw.cleanup()

    asyncio.run(scenario())


def test_permission_ids_in_same_session_are_not_deduplicated_together(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        adapter = _PermissionAdapter()
        claw = _build_claw(tmp_path, adapter, monkeypatch)
        monkeypatch.setattr(
            "jiuwenclaw.agentserver.interface.append_history_record",
            lambda **_kwargs: None,
        )

        await asyncio.gather(
            claw.process_message(
                _permission_request("permission-a", request_id="web-a")
            ),
            claw.process_message(
                _permission_request("permission-b", request_id="web-b")
            ),
        )

        assert sorted(adapter.runtime_calls) == ["permission-a", "permission-b"]
        await claw.cleanup()

    asyncio.run(scenario())


def test_permission_response_preserves_opaque_request_id(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        adapter = _PermissionAdapter()
        claw = _build_claw(tmp_path, adapter, monkeypatch)
        monkeypatch.setattr(
            "jiuwenclaw.agentserver.interface.append_history_record",
            lambda **_kwargs: None,
        )

        responses = await asyncio.gather(
            claw.process_message(
                _permission_request("permission-1", request_id="web-a")
            ),
            claw.process_message(
                _permission_request(" permission-1 ", request_id="web-b")
            ),
        )

        assert sorted(adapter.runtime_calls) == [" permission-1 ", "permission-1"]
        assert all(
            response.payload.get("code") != "duplicate_permission_response"
            for response in responses
        )
        await claw.cleanup()

    asyncio.run(scenario())


def test_stream_duplicate_permission_runs_runtime_once(
    tmp_path,
    monkeypatch,
) -> None:
    async def scenario() -> None:
        adapter = _PermissionAdapter()
        claw = _build_claw(tmp_path, adapter, monkeypatch)
        history_calls: list[str] = []
        monkeypatch.setattr(
            "jiuwenclaw.agentserver.interface.append_history_record",
            lambda **kwargs: history_calls.append(kwargs["role"]),
        )

        async def collect(request):
            return [chunk async for chunk in claw.process_message_stream(request)]

        results = await asyncio.gather(*(
            collect(
                _permission_request("permission-1", request_id=f"stream-{index}")
            )
            for index in range(3)
        ))

        assert adapter.runtime_calls == ["permission-1"]
        assert history_calls.count("user") == 3
        assert sum(
            chunks[-1].payload.get("code") == "duplicate_permission_response"
            for chunks in results
        ) == 2
        await claw.cleanup()

    asyncio.run(scenario())
