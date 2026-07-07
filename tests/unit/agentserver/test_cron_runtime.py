from __future__ import annotations

import asyncio
from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.cron import cron_runtime as runtime_module
from jiuwenswarm.agents.harness.common.tools.cron.cron_runtime import (
    _CronToolsCronBackend,
    _add_xiaoyi_device_fields_to_cron_tools,
    _extract_legacy_params,
)
from jiuwenswarm.agents.harness.common.tools.xiaoyi_phone_tools.device_tool_planner import (
    CronDeviceToolPlan,
)
from openjiuwen.harness.tools.cron import create_cron_tools
from jiuwenswarm.agents.harness.common.tools.cron.cron_tools import CronTools


class _FakeCronTools:
    def __init__(self) -> None:
        self.routes: list[object] = []
        self.reset_tokens: list[str] = []
        self.create_payloads: list[dict] = []

    def push_cron_route(self, route):
        self.routes.append(route)
        return "token-1"

    def reset_cron_route(self, token):
        self.reset_tokens.append(token)

    async def create_job(self, payload: dict):
        self.create_payloads.append(payload)
        return payload

    async def list_jobs(self):
        return []

    async def get_job(self, job_id: str):
        _ = job_id
        return None

    async def update_job(self, job_id: str, payload: dict):
        return {"id": job_id, **payload}

    async def delete_job(self, job_id: str):
        _ = job_id
        return True

    async def toggle_job(self, job_id: str, enabled: bool):
        return {"id": job_id, "enabled": enabled}

    async def preview_job(self, job_id: str, count: int = 5):
        _ = (job_id, count)
        return []

    async def run_now(self, job_id: str):
        _ = job_id
        return {"run_id": "r-1"}


class _FakePlanner:
    def __init__(
        self,
        result: CronDeviceToolPlan | None = None,
        error: BaseException | None = None,
    ) -> None:
        self.result = result or CronDeviceToolPlan((), (), ())
        self.error = error
        self.calls: list[dict] = []

    async def plan(self, **kwargs):
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.result


def test_extract_legacy_params_maps_implicit_web_to_context_channel() -> None:
    context = SimpleNamespace(
        channel_id="feishu_enterprise:open_id:abc",
        session_id="sess-1",
        metadata={"request_id": "req-1"},
    )
    payload = {
        "schedule": {"kind": "cron", "expr": "*/5 * * * *"},
        "payload": {"kind": "agentTurn", "message": "ping"},
        "delivery": {"channel": "web"},
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    # normalize_target_channel_id keeps the canonical enterprise channel prefix.
    assert out["targets"] == "feishu_enterprise:open_id"


def test_extract_legacy_params_delivery_channel_takes_priority_over_targets() -> None:
    context = SimpleNamespace(channel_id="feishu_enterprise:open_id:abc")
    payload = {
        "schedule": {"kind": "cron", "expr": "*/5 * * * *"},
        "payload": {"kind": "agentTurn", "message": "ping"},
        "delivery": {"channel": "web"},
        "targets": "wecom",
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    assert out["targets"] == "web"


def test_extract_legacy_params_context_mode_takes_priority_over_payload() -> None:
    context = SimpleNamespace(
        channel_id="web",
        session_id="sess-1",
        mode="agent.fast",
    )
    payload = {
        "schedule": {"kind": "cron", "expr": "0 9 * * *"},
        "payload": {"kind": "agentTurn", "message": "daily report"},
        "mode": "team",
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    assert out["mode"] == "agent.fast"


def test_extract_legacy_params_inherits_context_mode_when_missing() -> None:
    context = SimpleNamespace(channel_id="web", session_id="sess-1", mode="team")
    payload = {
        "schedule": {"kind": "cron", "expr": "0 9 * * *"},
        "payload": {"kind": "agentTurn", "message": "daily report"},
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    assert out["mode"] == "team"


def test_extract_legacy_params_defaults_to_agent_fast_without_context_mode() -> None:
    context = SimpleNamespace(channel_id="web", session_id="sess-1")
    payload = {
        "schedule": {"kind": "cron", "expr": "0 9 * * *"},
        "payload": {"kind": "agentTurn", "message": "daily report"},
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    assert out["mode"] == "agent.fast"


def test_runtime_cron_schemas_keep_device_intents_internal() -> None:
    tools = create_cron_tools(_FakeCronTools(), context=None)

    _add_xiaoyi_device_fields_to_cron_tools(tools)

    cards = {tool.card.name: tool.card for tool in tools}
    assert (
        "required_device_intents"
        not in cards["cron_create_job"].input_params["properties"]
    )
    assert "required_device_intents" not in (
        cards["cron"].input_params["properties"]["job"]["properties"]
    )


def test_extract_legacy_params_passthrough_unknown_mode() -> None:
    context = SimpleNamespace(channel_id="web", session_id="sess-1", mode="future.mode")
    payload = {
        "schedule": {"kind": "cron", "expr": "0 9 * * *"},
        "payload": {"kind": "agentTurn", "message": "daily report"},
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    assert out["mode"] == "future.mode"


@pytest.mark.asyncio
async def test_ensure_scheduler_requires_message_handler() -> None:
    tools = CronTools(agent_client=object(), message_handler=None)
    scheduler = await tools.ensure_scheduler()
    assert scheduler is None


@pytest.mark.asyncio
async def test_cron_backend_create_job_pushes_and_resets_route() -> None:
    cron_tools = _FakeCronTools()
    backend = _CronToolsCronBackend(cron_tools=cron_tools, message_handler=None)
    context = SimpleNamespace(
        channel_id="web",
        session_id="sess-1",
        metadata={"request_id": "req-123"},
    )

    await backend.create_job(
        {
            "id": "job-1",
            "schedule": {"kind": "cron", "expr": "*/5 * * * *"},
            "payload": {"kind": "agentTurn", "message": "hello"},
            "delivery": {"channel": "web"},
        },
        context=context,
    )

    assert len(cron_tools.routes) == 1
    assert cron_tools.routes[0].request_id == "req-123"
    assert cron_tools.routes[0].channel_id == "web"
    assert cron_tools.routes[0].session_id == "sess-1"
    assert cron_tools.reset_tokens == ["token-1"]
    assert cron_tools.create_payloads[0]["id"] == "job-1"


@pytest.mark.asyncio
async def test_cron_backend_uses_original_schema_for_ordinary_job() -> None:
    cron_tools = _FakeCronTools()
    backend = _CronToolsCronBackend(cron_tools=cron_tools, message_handler=None)
    context = SimpleNamespace(
        channel_id="web",
        session_id="sess-1",
        metadata={"request_id": "req-123"},
    )

    await backend.create_job(
        {
            "name": "ordinary",
            "cron_expr": "*/5 * * * *",
            "timezone": "Asia/Shanghai",
            "description": "hello",
        },
        context=context,
    )

    assert len(cron_tools.create_payloads) == 1
    assert "required_device_intents" not in cron_tools.create_payloads[0]


@pytest.mark.asyncio
async def test_cron_backend_preflights_device_intents_before_create(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cron_tools = _FakeCronTools()
    planner = _FakePlanner(
        CronDeviceToolPlan(
            ("create_note", "create_alarm"),
            ("CreateNote", "CreateAlarm"),
            ("CreateNote", "CreateAlarm"),
        )
    )
    backend = _CronToolsCronBackend(
        cron_tools=cron_tools,
        message_handler=None,
        device_tool_planner=planner,
    )
    context = SimpleNamespace(
        channel_id="xiaoyi",
        session_id="sess-1",
        metadata={
            "request_id": "req-123",
            "xiaoyi_push_id": "push-1",
        },
    )
    checked: list[str] = []

    async def fake_check(intent: str) -> dict:
        checked.append(intent)
        return {"authorized": True, "code": 0}

    monkeypatch.setattr(
        runtime_module,
        "execute_plugin_privilege_check",
        fake_check,
    )

    await backend.create_job(
        {
            "name": "device",
            "cron_expr": "0 0 9 * * ? *",
            "timezone": "Asia/Shanghai",
            "description": "write note and create alarm",
        },
        context=context,
    )

    assert planner.calls[0]["description"] == "write note and create alarm"
    assert checked == ["CreateNote", "CreateAlarm"]
    assert cron_tools.create_payloads[0]["required_device_intents"] == [
        "CreateNote",
        "CreateAlarm",
    ]
    assert cron_tools.create_payloads[0]["xiaoyi_push_id"] == "push-1"


@pytest.mark.asyncio
async def test_cron_backend_does_not_create_when_privilege_denied(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cron_tools = _FakeCronTools()
    planner = _FakePlanner(
        CronDeviceToolPlan(
            ("create_note", "create_alarm"),
            ("CreateNote", "CreateAlarm"),
            ("CreateNote", "CreateAlarm"),
        )
    )
    backend = _CronToolsCronBackend(
        cron_tools=cron_tools,
        message_handler=None,
        device_tool_planner=planner,
    )
    context = SimpleNamespace(
        channel_id="xiaoyi",
        session_id="sess-1",
        metadata={
            "request_id": "req-123",
            "xiaoyi_push_id": "push-1",
        },
    )

    async def fake_check(intent: str) -> dict:
        return {"authorized": intent != "CreateAlarm"}

    monkeypatch.setattr(
        runtime_module,
        "execute_plugin_privilege_check",
        fake_check,
    )

    with pytest.raises(RuntimeError, match="denied"):
        await backend.create_job(
            {
                "name": "device",
                "cron_expr": "0 0 9 * * ? *",
                "timezone": "Asia/Shanghai",
                "description": "write note and create alarm",
            },
            context=context,
        )

    assert cron_tools.create_payloads == []


@pytest.mark.asyncio
async def test_cron_backend_does_not_create_when_privilege_check_times_out(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cron_tools = _FakeCronTools()
    planner = _FakePlanner(
        CronDeviceToolPlan(
            ("create_note",),
            ("CreateNote",),
            ("CreateNote",),
        )
    )
    backend = _CronToolsCronBackend(
        cron_tools=cron_tools,
        message_handler=None,
        device_tool_planner=planner,
    )
    context = SimpleNamespace(
        channel_id="xiaoyi",
        session_id="sess-1",
        metadata={
            "request_id": "req-123",
            "xiaoyi_push_id": "push-1",
        },
    )

    async def fake_check(intent: str) -> dict:
        raise asyncio.TimeoutError(intent)

    monkeypatch.setattr(
        runtime_module,
        "execute_plugin_privilege_check",
        fake_check,
    )

    with pytest.raises(asyncio.TimeoutError):
        await backend.create_job(
            {
                "name": "device",
                "cron_expr": "0 0 9 * * ? *",
                "timezone": "Asia/Shanghai",
                "description": "write note",
            },
            context=context,
        )

    assert cron_tools.create_payloads == []


@pytest.mark.asyncio
async def test_cron_backend_does_not_create_when_planning_fails(
) -> None:
    cron_tools = _FakeCronTools()
    planner = _FakePlanner(error=RuntimeError("planning failed"))
    backend = _CronToolsCronBackend(
        cron_tools=cron_tools,
        message_handler=None,
        device_tool_planner=planner,
    )
    context = SimpleNamespace(
        channel_id="xiaoyi",
        session_id="sess-1",
        metadata={
            "request_id": "req-123",
            "xiaoyi_push_id": "push-1",
        },
    )
    with pytest.raises(RuntimeError, match="planning failed"):
        await backend.create_job(
            {
                "name": "device",
                "cron_expr": "0 0 9 * * ? *",
                "timezone": "Asia/Shanghai",
                "description": "unsupported action",
            },
            context=context,
        )

    assert cron_tools.create_payloads == []


@pytest.mark.asyncio
async def test_cron_backend_routes_non_privileged_device_tool_without_check(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cron_tools = _FakeCronTools()
    planner = _FakePlanner(
        CronDeviceToolPlan(
            ("upload_file",),
            ("FileUploadForClaw",),
            (),
        )
    )
    backend = _CronToolsCronBackend(
        cron_tools=cron_tools,
        message_handler=None,
        device_tool_planner=planner,
    )
    context = SimpleNamespace(
        channel_id="xiaoyi",
        session_id="sess-1",
        metadata={
            "request_id": "req-123",
            "xiaoyi_push_id": "push-1",
        },
    )

    async def unexpected_check(intent: str) -> dict:
        raise AssertionError(f"unexpected privilege check: {intent}")

    monkeypatch.setattr(
        runtime_module,
        "execute_plugin_privilege_check",
        unexpected_check,
    )

    await backend.create_job(
        {
            "name": "upload",
            "cron_expr": "0 0 9 * * ? *",
            "timezone": "Asia/Shanghai",
            "description": "upload file",
        },
        context=context,
    )

    assert cron_tools.create_payloads[0]["required_device_intents"] == [
        "FileUploadForClaw"
    ]
    assert cron_tools.create_payloads[0]["xiaoyi_push_id"] == "push-1"
