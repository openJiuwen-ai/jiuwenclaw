# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""实例管理 API（instance_routers）单元测试。"""

from __future__ import annotations

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
    assert jid.startswith("sp-")

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
