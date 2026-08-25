# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""企业专属表 EnterpriseRecordRepository（不注入运行时）。"""

from __future__ import annotations

import pytest

from jiuwenswarm.gateway.config.enterprise import (
    ENTERPRISE_RECORD_STORE_NAMES,
    EnterpriseRecordRepository,
    clear_enterprise_record_repositories,
    get_enterprise_record_repository,
    set_enterprise_record_repositories,
)
from jiuwenswarm.gateway.storage.backends.memory_persistent import InMemoryPersistentBackend
from jiuwenswarm.gateway.storage_assembly.layouts import build_gateway_store_registry
from jiuwenswarm.gateway.storage_assembly.setup import (
    create_enterprise_record_repository,
    create_enterprise_record_repositories,
)


@pytest.mark.asyncio
async def test_policy_crud_and_scope() -> None:
    store = InMemoryPersistentBackend()
    repo = EnterpriseRecordRepository(
        store,
        "config_effective_global_policy",
        instance_id="inst-1",
    )
    created = await repo.create(
        {
            "policy_id": "p1",
            "policy_name": "global",
            "priority": 10,
            "template_ref": {"model": "t1"},
            "enabled": True,
        }
    )
    assert created["jiuwenclaw_id"] == "inst-1"
    assert created["policy_id"] == "p1"

    got = await repo.get(policy_id="p1")
    assert got is not None
    assert got["policy_name"] == "global"

    updated = await repo.update({"policy_id": "p1"}, {"priority": 20})
    assert updated is not None
    assert updated["priority"] == 20

    other = EnterpriseRecordRepository(
        store,
        "config_effective_global_policy",
        instance_id="inst-2",
    )
    assert await other.list() == []
    assert await repo.delete(policy_id="p1") is True
    assert await repo.get(policy_id="p1") is None


@pytest.mark.asyncio
async def test_template_upsert_and_sync() -> None:
    store = InMemoryPersistentBackend()
    repo = create_enterprise_record_repository(
        store, "model_template", instance_id="gw-1"
    )
    await repo.upsert(
        {
            "template_id": "m1",
            "template_name": "one",
            "api_base": "http://a",
            "api_key": "k",
            "model_id": "id",
            "model_provider": "p",
            "model_type": ["default"],
        }
    )
    await repo.upsert(
        {
            "template_id": "m2",
            "template_name": "two",
            "api_base": "http://b",
            "api_key": "k",
            "model_id": "id2",
            "model_provider": "p",
            "model_type": ["default"],
        }
    )
    result = await repo.sync_by_business_key(
        [
            {
                "template_id": "m1",
                "template_name": "one-renamed",
                "api_base": "http://a",
                "api_key": "k",
                "model_id": "id",
                "model_provider": "p",
                "model_type": ["default"],
            }
        ]
    )
    assert result == {"synced_count": 1, "deleted_count": 1}
    rows = await repo.list()
    assert len(rows) == 1
    assert rows[0]["template_id"] == "m1"
    assert rows[0]["template_name"] == "one-renamed"


@pytest.mark.asyncio
async def test_singleton_keypair_no_scope() -> None:
    store = InMemoryPersistentBackend()
    repo = EnterpriseRecordRepository(store, "gateway_enc_keypair")
    await repo.create(
        {
            "id": "default",
            "enc_alg": "x25519",
            "private_key": "priv",
            "public_key": "pub",
            "fingerprint": "fp",
        }
    )
    row = await repo.get(id="default")
    assert row is not None
    assert "jiuwenclaw_id" not in row
    assert await repo.update({"id": "default"}, {"fingerprint": "fp2"}) is not None
    assert (await repo.get(id="default"))["fingerprint"] == "fp2"


@pytest.mark.asyncio
async def test_task_memory_single_document_sync() -> None:
    store = InMemoryPersistentBackend()
    repo = EnterpriseRecordRepository(
        store, "task_memory_config", instance_id="inst-1"
    )
    await repo.sync_by_business_key([{"enabled": True, "llm_model": "m"}])
    body = await repo.get()
    assert body is not None
    assert body["enabled"] is True
    assert body["jiuwenclaw_id"] == "inst-1"

    await repo.sync_by_business_key([])
    assert await repo.get() is None


@pytest.mark.asyncio
async def test_access_not_injected_by_default() -> None:
    clear_enterprise_record_repositories()
    assert get_enterprise_record_repository("model_template") is None

    store = InMemoryPersistentBackend()
    repos = create_enterprise_record_repositories(
        store, instance_id="inst-1"
    )
    assert set(repos) == set(ENTERPRISE_RECORD_STORE_NAMES)
    set_enterprise_record_repositories(repos)
    assert get_enterprise_record_repository("model_template") is repos["model_template"]
    clear_enterprise_record_repositories()
    assert get_enterprise_record_repository("model_template") is None


def test_layouts_register_all_enterprise_names() -> None:
    registry = build_gateway_store_registry()
    for name in ENTERPRISE_RECORD_STORE_NAMES:
        layout = registry.get(name)
        assert layout.db is not None
        assert layout.db.table == name
        assert layout.file is None
