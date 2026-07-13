from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenswarm.agents.harness.common.tools.cron.cron_runtime import (
    _CronToolsCronBackend,
    _extract_legacy_params,
)
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
