from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenclaw.agentserver.deep_agent.cron_runtime import (
    CronRuntimeBridge,
    _CronToolsCronBackend,
    _extract_legacy_params,
    _tenant_scope_from_context,
)
from jiuwenclaw.agentserver.tools.cron_tools import CronTools, resolve_cron_jobs_path


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


def test_extract_legacy_params_maps_implicit_web_to_context_channel() -> None:
    context = SimpleNamespace(
        channel_id="feishu:open_id:abc",
        session_id="sess-1",
        metadata={"request_id": "req-1"},
    )
    payload = {
        "schedule": {"kind": "cron", "expr": "*/5 * * * *"},
        "payload": {"kind": "agentTurn", "message": "ping"},
        "delivery": {"channel": "web"},
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    # normalize_target_channel_id keeps the canonical multi-bot feishu channel prefix.
    assert out["targets"] == "feishu:open_id"


def test_extract_legacy_params_delivery_channel_takes_priority_over_targets() -> None:
    context = SimpleNamespace(channel_id="feishu:open_id:abc")
    payload = {
        "schedule": {"kind": "cron", "expr": "*/5 * * * *"},
        "payload": {"kind": "agentTurn", "message": "ping"},
        "delivery": {"channel": "web"},
        "targets": "wecom",
    }

    out = _extract_legacy_params(payload, context=context, require_schedule=True)

    assert out["targets"] == "web"


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


def test_tenant_scope_from_context_reads_metadata() -> None:
    context = SimpleNamespace(
        metadata={"service_id": "svc-a", "agent_id": "office"},
    )
    assert _tenant_scope_from_context(context) == ("svc-a", "office")


def test_resolve_cron_jobs_path_uses_multi_tenant_tree(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jiuwenclaw.utils.get_user_workspace_dir", lambda: tmp_path)
    path = resolve_cron_jobs_path("default", "office")
    assert path == (
        tmp_path
        / "service_default"
        / "agent_office"
        / "agent"
        / "home"
        / "cron_jobs.json"
    )


def test_cron_tools_store_path_is_per_tenant(tmp_path, monkeypatch) -> None:
    monkeypatch.setattr("jiuwenclaw.utils.get_user_workspace_dir", lambda: tmp_path)
    tools = CronTools(service_id="default", agent_id="office")
    assert tools._local_store.path == resolve_cron_jobs_path("default", "office")


def test_cron_runtime_bridge_caches_backends_per_tenant(monkeypatch) -> None:
    created: list[tuple[str, str]] = []

    class _FakeCronTools:
        def __init__(self, *, service_id: str, agent_id: str) -> None:
            created.append((service_id, agent_id))

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.cron_runtime.CronTools",
        _FakeCronTools,
    )
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.deep_agent.cron_runtime.create_cron_tools",
        lambda backend, **kwargs: [],
    )

    bridge = CronRuntimeBridge()
    bridge.build_tools(
        context=SimpleNamespace(metadata={"service_id": "default", "agent_id": "office"}),
        agent_id="jiuwenclaw",
        service_id="default",
        tenant_agent_id="office",
    )
    bridge.build_tools(
        context=SimpleNamespace(metadata={"service_id": "default", "agent_id": "office"}),
        agent_id="jiuwenclaw",
        service_id="default",
        tenant_agent_id="office",
    )
    bridge.build_tools(
        context=SimpleNamespace(metadata={"service_id": "default", "agent_id": "assistant"}),
        agent_id="jiuwenclaw",
        service_id="default",
        tenant_agent_id="assistant",
    )

    assert created == [
        ("default", "office"),
        ("default", "assistant"),
    ]
