from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.cron.cron_runtime import (
    _CronToolsCronBackend,
    _extract_legacy_params,
)
from jiuwenswarm.agents.harness.common.tools.cron.cron_tools import CronToolRoute, CronTools
from jiuwenswarm.gateway.cron.store import CronJobStore


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


class _FakeGatewayPush:
    def __init__(self) -> None:
        self.payloads: list[dict] = []

    async def send_push(self, payload: dict) -> None:
        self.payloads.append(payload)


def _setup_project_store(tmp_path, monkeypatch):
    root = tmp_path / "agent"
    root.mkdir()
    monkeypatch.setattr(
        "jiuwenswarm.server.runtime.session.project_store.get_agent_root_dir",
        lambda: root,
    )
    from jiuwenswarm.server.runtime.session import project_store

    project_store.invalidate_cache()
    return project_store


def _make_cron_tools(tmp_path, monkeypatch) -> tuple[CronTools, _FakeGatewayPush]:
    push = _FakeGatewayPush()
    tools = CronTools(gateway_push=push, agent_client=object(), message_handler=object())
    tools._local_store = CronJobStore(path=tmp_path / "cron_jobs.json")

    async def _noop_reload() -> None:
        return None

    monkeypatch.setattr(tools, "_reload_scheduler", _noop_reload)
    return tools, push


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

    assert out["mode"] == "agent"


def test_extract_legacy_params_inherits_context_mode_when_missing() -> None:
    context = SimpleNamespace(channel_id="web", session_id="sess-1", mode="team")
    payload = {
        "schedule": {"kind": "cron", "expr": "0 9 * * *"},
        "payload": {"kind": "agentTurn", "message": "daily report"},
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    assert out["mode"] == "team"


def test_extract_legacy_params_defaults_to_agent_without_context_mode() -> None:
    context = SimpleNamespace(channel_id="web", session_id="sess-1")
    payload = {
        "schedule": {"kind": "cron", "expr": "0 9 * * *"},
        "payload": {"kind": "agentTurn", "message": "daily report"},
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    assert out["mode"] == "agent"


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
async def test_cron_tools_create_job_resolves_route_project_dir(tmp_path, monkeypatch) -> None:
    project_store = _setup_project_store(tmp_path, monkeypatch)
    project_dir = tmp_path / "project-a"
    project_dir.mkdir()
    project = project_store.create_project("P1", str(project_dir))
    tools, push = _make_cron_tools(tmp_path, monkeypatch)

    token = tools.push_cron_route(CronToolRoute(project_dir=str(project_dir)))
    try:
        job = await tools.create_job(
            {
                "id": "job-1",
                "name": "daily",
                "cron_expr": "0 9 * * *",
                "timezone": "Asia/Shanghai",
                "description": "hello",
                "targets": "web",
            }
        )
    finally:
        tools.reset_cron_route(token)

    assert job["project_id"] == project.project_id
    synced = push.payloads[-1]["body"]["data"]
    assert synced["project_dir"] == str(project_dir)
    assert synced["project_id"] == project.project_id


@pytest.mark.asyncio
async def test_cron_tools_create_job_rejects_relative_project_dir(tmp_path, monkeypatch) -> None:
    _setup_project_store(tmp_path, monkeypatch)
    tools, push = _make_cron_tools(tmp_path, monkeypatch)

    token = tools.push_cron_route(CronToolRoute(project_dir="relative/path"))
    try:
        with pytest.raises(ValueError, match="project_dir must be an absolute path"):
            await tools.create_job(
                {
                    "id": "job-1",
                    "name": "daily",
                    "cron_expr": "0 9 * * *",
                    "timezone": "Asia/Shanghai",
                    "description": "hello",
                    "targets": "web",
                }
            )
    finally:
        tools.reset_cron_route(token)

    assert push.payloads == []
    assert await tools.list_jobs() == []


@pytest.mark.asyncio
async def test_cron_tools_update_job_resolves_project_dir_and_syncs_public_patch(
    tmp_path, monkeypatch
) -> None:
    project_store = _setup_project_store(tmp_path, monkeypatch)
    project_dir = tmp_path / "project-b"
    project_dir.mkdir()
    project = project_store.create_project("P2", str(project_dir))
    tools, push = _make_cron_tools(tmp_path, monkeypatch)
    await tools._local_store.create_job(
        job_id="job-1",
        name="daily",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="hello",
        targets="web",
    )

    job = await tools.update_job(
        "job-1",
        {"project_dir": str(project_dir), "project_id": "proj_should_be_ignored"},
    )

    assert job["project_id"] == project.project_id
    synced_patch = push.payloads[-1]["body"]["data"]["patch"]
    assert synced_patch["project_dir"] == str(project_dir)
    assert "project_id" not in synced_patch
    assert "project_dir" not in (await tools._local_store.get_job("job-1")).to_dict()


@pytest.mark.asyncio
async def test_cron_tools_update_job_validates_model(tmp_path, monkeypatch) -> None:
    _setup_project_store(tmp_path, monkeypatch)
    tools, push = _make_cron_tools(tmp_path, monkeypatch)
    await tools._local_store.create_job(
        job_id="job-1",
        name="daily",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="hello",
        targets="web",
    )
    monkeypatch.setattr(
        "jiuwenswarm.agents.harness.common.tools.cron.cron_tools.validate_cron_model",
        lambda raw: "checked-model" if raw == "valid-model" else None,
    )

    job = await tools.update_job("job-1", {"model_name": "valid-model"})

    assert job["model_name"] == "checked-model"
    synced_patch = push.payloads[-1]["body"]["data"]["patch"]
    assert synced_patch["model_name"] == "checked-model"


@pytest.mark.asyncio
async def test_cron_tools_create_job_tool_preserves_explicit_empty_project_dir(
    tmp_path, monkeypatch
) -> None:
    project_store = _setup_project_store(tmp_path, monkeypatch)
    project_dir = tmp_path / "project-c"
    project_dir.mkdir()
    project_store.create_project("P3", str(project_dir))
    tools, push = _make_cron_tools(tmp_path, monkeypatch)

    token = tools.push_cron_route(CronToolRoute(project_dir=str(project_dir)))
    try:
        job = await tools._create_job_tool(
            name="daily",
            cron_expr="0 9 * * *",
            timezone="Asia/Shanghai",
            description="hello",
            targets="web",
            project_dir="",
        )
    finally:
        tools.reset_cron_route(token)

    assert job["project_id"] == ""
    synced = push.payloads[-1]["body"]["data"]
    assert synced["project_dir"] == ""
