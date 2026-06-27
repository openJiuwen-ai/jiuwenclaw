# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例管理 API（instance_routers）单元测试。"""

from __future__ import annotations

import uuid

import pytest

from conftest import ManagerApiHarness
from demo_payloads import instance_create_body

pytestmark = pytest.mark.unit


@pytest.mark.asyncio
async def test_instance_create_list_get_patch_delete(manager_api: ManagerApiHarness):
    h = manager_api

    create_resp = await h.http.post(
        h.instances_url(),
        json=instance_create_body(jiuwenclaw_name="ut-instance-crud"),
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["data"]
    jid = created["jiuwenclaw_id"]
    parsed = uuid.UUID(jid)
    assert str(parsed) == jid

    get_after_create = await h.http.get(h.instances_url(f"/{jid}"))
    assert get_after_create.status_code == 200
    assert get_after_create.json()["data"]["jiuwenclaw_name"] == "ut-instance-crud"

    list_resp = await h.http.get(h.instances_url(), params={"page": 1, "page_size": 20})
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]
    assert any(item["jiuwenclaw_id"] == jid for item in items)

    get_resp = await h.http.get(h.instances_url(f"/{jid}"))
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["jiuwenclaw_name"] == "ut-instance-crud"

    patch_resp = await h.http.patch(
        h.instances_url(f"/{jid}"),
        json={"description": "updated by ut", "jiuwenclaw_name": "ut-renamed"},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()["data"]
    assert patched["description"] == "updated by ut"
    assert patched["jiuwenclaw_name"] == "ut-renamed"

    delete_resp = await h.http.delete(h.instances_url(f"/{jid}"))
    assert delete_resp.status_code == 200

    missing_resp = await h.http.get(h.instances_url(f"/{jid}"))
    assert missing_resp.status_code == 404


@pytest.mark.asyncio
async def test_instance_list_search(manager_api: ManagerApiHarness):
    h = manager_api
    create_resp = await h.http.post(
        h.instances_url(),
        json=instance_create_body(jiuwenclaw_name="ut-search-target"),
    )
    assert create_resp.status_code == 200
    jid = create_resp.json()["data"]["jiuwenclaw_id"]
    try:
        by_name = await h.http.get(
            h.instances_url(),
            params={"page": 1, "page_size": 50, "search": "ut-search-target"},
        )
        assert by_name.status_code == 200
        names = [item["jiuwenclaw_name"] for item in by_name.json()["data"]["items"]]
        assert "ut-search-target" in names

        by_id = await h.http.get(
            h.instances_url(),
            params={"page": 1, "page_size": 50, "search": jid[:8]},
        )
        assert by_id.status_code == 200
        ids = [item["jiuwenclaw_id"] for item in by_id.json()["data"]["items"]]
        assert jid in ids

        missing = await h.http.get(
            h.instances_url(),
            params={"page": 1, "page_size": 50, "search": "ut-search-not-exists-xyz"},
        )
        assert missing.status_code == 200
        assert missing.json()["data"]["items"] == []
    finally:
        await h.http.delete(h.instances_url(f"/{jid}"))


@pytest.mark.asyncio
async def test_instance_list_sort_by_name(manager_api: ManagerApiHarness):
    h = manager_api
    created_ids: list[str] = []
    try:
        for name in ("ut-sort-aaa", "ut-sort-zzz"):
            create_resp = await h.http.post(
                h.instances_url(),
                json=instance_create_body(jiuwenclaw_name=name),
            )
            assert create_resp.status_code == 200
            created_ids.append(create_resp.json()["data"]["jiuwenclaw_id"])

        asc_resp = await h.http.get(
            h.instances_url(),
            params={
                "page": 1,
                "page_size": 50,
                "sort_by": "jiuwenclaw_name",
                "sort_order": "asc",
            },
        )
        assert asc_resp.status_code == 200
        asc_names = [
            item["jiuwenclaw_name"]
            for item in asc_resp.json()["data"]["items"]
            if item["jiuwenclaw_id"] in created_ids
        ]
        assert asc_names == sorted(asc_names)

        desc_resp = await h.http.get(
            h.instances_url(),
            params={
                "page": 1,
                "page_size": 50,
                "sort_by": "jiuwenclaw_name",
                "sort_order": "desc",
            },
        )
        assert desc_resp.status_code == 200
        desc_names = [
            item["jiuwenclaw_name"]
            for item in desc_resp.json()["data"]["items"]
            if item["jiuwenclaw_id"] in created_ids
        ]
        assert desc_names == sorted(desc_names, reverse=True)
    finally:
        for jid in created_ids:
            await h.http.delete(h.instances_url(f"/{jid}"))


@pytest.mark.asyncio
async def test_instance_get_not_found(manager_api: ManagerApiHarness):
    resp = await manager_api.http.get(
        manager_api.instances_url("/sp-does-not-exist"),
    )
    assert resp.status_code == 404


@pytest.mark.asyncio
async def test_instance_patch_not_found(manager_api: ManagerApiHarness):
    resp = await manager_api.http.patch(
        manager_api.instances_url("/sp-does-not-exist"),
        json={"description": "noop"},
    )
    assert resp.status_code == 404
