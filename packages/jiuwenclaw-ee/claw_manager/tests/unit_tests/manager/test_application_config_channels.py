# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""应用配置 Channel API（application_config_routers）单元测试。"""

from __future__ import annotations

import pytest

from conftest import ManagerApiHarness

pytestmark = pytest.mark.unit


def _channels_prefix(h: ManagerApiHarness) -> str:
    return h.scoped_url("/channels")


@pytest.mark.asyncio
async def test_channel_register_list_activate_deactivate_delete(
    manager_api: ManagerApiHarness,
):
    h = manager_api
    await h.create_instance()
    prefix = _channels_prefix(h)

    register_resp = await h.http.post(
        prefix,
        json={
            "channel_id": "web-main",
            "channel_name": "Web 主通道",
            "channel_type": "web",
            "bot_id": "bot_main",
            "config": {"port": 8080},
            "status": "active",
        },
    )
    assert register_resp.status_code == 200
    assert register_resp.json()["data"]["channel_id"] == "web-main"

    list_resp = await h.http.get(prefix)
    assert list_resp.status_code == 200
    items = list_resp.json()["data"]["items"]
    assert len(items) == 1
    assert items[0]["channel_id"] == "web-main"

    deactivate_resp = await h.http.post(
        f"{prefix}/web-main/deactivate",
        json={"graceful": True, "timeout": 10},
    )
    assert deactivate_resp.status_code == 200
    assert deactivate_resp.json()["data"]["status"] == "inactive"

    activate_resp = await h.http.post(f"{prefix}/web-main/activate")
    assert activate_resp.status_code == 200
    assert activate_resp.json()["data"]["status"] == "active"

    delete_resp = await h.http.delete(f"{prefix}/web-main")
    assert delete_resp.status_code == 200

    empty_list = await h.http.get(prefix)
    assert empty_list.json()["data"]["items"] == []


@pytest.mark.asyncio
async def test_channel_register_duplicate_returns_400(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    prefix = _channels_prefix(h)
    body = {
        "channel_id": "dup-channel",
        "channel_name": "Dup",
        "channel_type": "web",
        "bot_id": "bot",
        "status": "active",
    }

    assert (await h.http.post(prefix, json=body)).status_code == 200
    dup_resp = await h.http.post(prefix, json=body)
    assert dup_resp.status_code == 400
