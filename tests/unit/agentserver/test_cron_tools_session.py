from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.tools.cron_tools import CronToolRoute, CronTools
from jiuwenclaw.gateway.cron.store import FileCronJobStore


@pytest.fixture
def cron_tools(tmp_path: Path) -> CronTools:
    store = FileCronJobStore(path=tmp_path / "cron_jobs.json")
    tools = CronTools(gateway_push=MagicMock())
    tools._local_store = store
    tools._gateway_push.send_push = AsyncMock()
    tools._reload_scheduler = AsyncMock()
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
