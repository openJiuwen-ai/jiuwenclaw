from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.tools.cron_tools import (
    CronToolRoute,
    CronTools,
    _format_query_timestamp,
)


@pytest.fixture
def cron_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> CronTools:
    (tmp_path / "agent" / "home").mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.tools.cron_tools.get_user_workspace_dir",
        lambda: tmp_path,
    )

    gateway_push = MagicMock()
    gateway_push.send_push = AsyncMock()
    tools = CronTools(gateway_push=gateway_push)
    monkeypatch.setattr(tools, "ensure_scheduler", AsyncMock(return_value=None))
    return tools


@pytest.mark.asyncio
async def test_create_job_persists_web_session_id(cron_tools: CronTools) -> None:
    token = cron_tools.push_cron_route(
        CronToolRoute(request_id="req-1", channel_id="web", session_id="sess_web")
    )
    try:
        job = await cron_tools.create_job(
            {
                "name": "daily",
                "cron_expr": "*/5 * * * *",
                "timezone": "Asia/Shanghai",
                "description": "ping",
                "targets": "web",
            }
        )
    finally:
        cron_tools.reset_cron_route(token)

    assert job["session_id"] == "sess_web"


@pytest.mark.asyncio
async def test_update_job_description_does_not_clear_web_session_id(cron_tools: CronTools) -> None:
    token = cron_tools.push_cron_route(
        CronToolRoute(request_id="req-1", channel_id="web", session_id="sess_web")
    )
    try:
        created = await cron_tools.create_job(
            {
                "name": "daily",
                "cron_expr": "*/5 * * * *",
                "timezone": "Asia/Shanghai",
                "description": "before",
                "targets": "web",
            }
        )
        updated = await cron_tools.update_job(created["id"], {"description": "after"})
    finally:
        cron_tools.reset_cron_route(token)

    assert updated["session_id"] == "sess_web"


@pytest.mark.parametrize(
    ("timezone_name", "expected"),
    [
        ("America/New_York", "2026-08-19T05:02:00-04:00"),
        ("Asia/Shanghai", "2026-08-19T17:02:00+08:00"),
        ("invalid/timezone", "2026-08-19T09:02:00+00:00"),
    ],
)
def test_format_query_timestamp_uses_job_timezone(timezone_name: str, expected: str) -> None:
    source_time = "2026-08-19T09:02:00+00:00"

    assert _format_query_timestamp(source_time, timezone_name) == expected


@pytest.mark.asyncio
async def test_get_and_list_jobs_format_times_without_changing_storage(
    cron_tools: CronTools,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stored_timestamp = datetime(2026, 8, 19, 9, 2, tzinfo=timezone.utc).timestamp()
    monkeypatch.setattr(
        "jiuwenclaw.gateway.cron.cron_job_mutations.time.time",
        lambda: stored_timestamp,
    )
    created = await cron_tools.create_job(
        {
            "name": "drink water",
            "cron_expr": "*/5 * * * *",
            "timezone": "America/New_York",
            "description": "drink water",
            "targets": "web",
        }
    )

    fetched = await cron_tools.get_job(created["id"])
    listed = await cron_tools.list_jobs()
    persisted = await cron_tools._local_store.get_job(created["id"])

    assert isinstance(created["created_at"], float)
    assert fetched["created_at"] == "2026-08-19T05:02:00-04:00"
    assert fetched["updated_at"] == "2026-08-19T05:02:00-04:00"
    assert listed[0]["created_at"] == "2026-08-19T05:02:00-04:00"
    assert listed[0]["updated_at"] == "2026-08-19T05:02:00-04:00"
    assert persisted is not None
    assert persisted.created_at == stored_timestamp
    assert persisted.updated_at == stored_timestamp


@pytest.mark.asyncio
async def test_get_and_list_jobs_format_enterprise_database_times(
    cron_tools: CronTools,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    enterprise_job = {
        "id": "enterprise-job",
        "timezone": "Asia/Shanghai",
        "created_at": "2026-08-20T09:58:57+00:00",
        "updated_at": "2026-08-20T09:58:57+00:00",
        "group_id": "group-1",
        "bot_id": "bot-1",
        "user_id": "user-1",
    }
    monkeypatch.setattr(cron_tools, "_enterprise_ready", lambda: True)
    monkeypatch.setattr(
        cron_tools,
        "_list_jobs_enterprise",
        AsyncMock(return_value=[enterprise_job]),
    )

    listed = await cron_tools.list_jobs()
    fetched = await cron_tools.get_job("enterprise-job")

    assert listed[0]["created_at"] == "2026-08-20T17:58:57+08:00"
    assert listed[0]["updated_at"] == "2026-08-20T17:58:57+08:00"
    assert fetched is not None
    assert fetched["created_at"] == "2026-08-20T17:58:57+08:00"
    assert fetched["updated_at"] == "2026-08-20T17:58:57+08:00"
