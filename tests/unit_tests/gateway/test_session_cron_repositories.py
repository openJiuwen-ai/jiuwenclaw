# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""SessionMapRepository / CronJobRepository PersistentStore adapters."""

from __future__ import annotations

import pytest

from jiuwenswarm.gateway.cron.job_access import (
    clear_cron_persistent_store,
    create_tenant_cron_store,
    set_cron_persistent_store,
)
from jiuwenswarm.gateway.cron.job_repository import CronJobRepository
from jiuwenswarm.gateway.routing.session_map import Session
from jiuwenswarm.gateway.routing.session_map_access import (
    PersistentSessionStorage,
    clear_session_map_repository,
    set_session_map_repository,
)
from jiuwenswarm.gateway.routing.session_map_repository import SessionMapRepository
from jiuwenswarm.gateway.storage.backends.memory_persistent import InMemoryPersistentBackend


@pytest.mark.asyncio
async def test_session_map_repository_upsert_get() -> None:
    store = InMemoryPersistentBackend()
    repo = SessionMapRepository(store)
    sess = Session(session_id="s1", service_id="svc", agent_id="a1")
    await repo.upsert("feishu::chat::bot", sess)
    got = await repo.get("feishu::chat::bot")
    assert got is not None
    assert got.session_id == "s1"
    mapping = await repo.list_all()
    assert "feishu::chat::bot" in mapping


def test_persistent_session_storage_sync_bridge() -> None:
    store = InMemoryPersistentBackend()
    repo = SessionMapRepository(store)
    set_session_map_repository(repo)
    try:
        storage = PersistentSessionStorage(repo)
        sess = Session(session_id="s2", service_id="svc", agent_id=None)
        storage.set("web::c::b", sess)
        assert storage.get("web::c::b") is not None
        assert storage.get("web::c::b").session_id == "s2"
    finally:
        clear_session_map_repository()


@pytest.mark.asyncio
async def test_cron_job_repository_crud() -> None:
    store = InMemoryPersistentBackend()
    repo = CronJobRepository(store, service_id="default", agent_id="default")
    job = await repo.create_job(
        name="daily",
        cron_expr="0 9 * * *",
        timezone="Asia/Shanghai",
        description="d",
        targets="web",
    )
    assert job.id
    listed = await repo.list_jobs()
    assert len(listed) == 1
    updated = await repo.update_job(job.id, {"description": "updated"})
    assert updated.description == "updated"
    assert await repo.delete_job(job.id) is True
    assert await repo.list_jobs() == []


@pytest.mark.asyncio
async def test_create_tenant_cron_store_uses_repository_when_wired() -> None:
    store = InMemoryPersistentBackend()
    set_cron_persistent_store(store)
    try:
        backend = create_tenant_cron_store("svc1", "agent1")
        assert isinstance(backend, CronJobRepository)
        job = await backend.create_job(
            name="n",
            cron_expr="0 * * * *",
            timezone="UTC",
            description="d",
            targets="web",
        )
        assert job.service_id == "svc1"
        assert job.agent_id == "agent1"
        rows = await store.list(
            "cron_job",
            filters={"service_id": "svc1", "agent_id": "agent1"},
        )
        assert len(rows) == 1
    finally:
        clear_cron_persistent_store()
