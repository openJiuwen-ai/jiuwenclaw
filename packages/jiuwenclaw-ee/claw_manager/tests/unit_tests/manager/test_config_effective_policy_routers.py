# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""配置生效策略与默认模板映射 API 单元测试。"""

from __future__ import annotations

import pytest

from conftest import ManagerApiHarness
from demo_payloads import model_templates

pytestmark = pytest.mark.unit


async def _create_model_template(h: ManagerApiHarness) -> str:
    row = await h.post_json("/model-templates", model_templates()[0][1])
    return row["template_id"]


@pytest.mark.asyncio
async def test_service_policy_crud(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    created = await h.post_json(
        "/config-effective/service-policies",
        {
            "service_id": "g_demo_sales::bot_main",
            "priority": 50,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    policy_id = int(created["id"])

    fetched = await h.get_json(f"/config-effective/service-policies/{policy_id}")
    assert fetched["service_id"] == "g_demo_sales::bot_main"

    patched = await h.patch_json(
        f"/config-effective/service-policies/{policy_id}",
        {"priority": 80, "enabled": False},
    )
    assert patched["priority"] == 80
    assert patched["enabled"] is False

    await h.delete_ok(f"/config-effective/service-policies/{policy_id}")

    missing = await h.http.get(
        h.scoped_url(f"/config-effective/service-policies/{policy_id}")
    )
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_agent_policy_crud(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    service = await h.post_json(
        "/config-effective/service-policies",
        {
            "service_id": "svc-parent",
            "priority": 1,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    service_id = int(service["id"])

    created = await h.post_json(
        "/config-effective/agent-policies",
        {
            "agent_id": "alice",
            "service_policy_id": service_id,
            "priority": 10,
            "match_expr": "user_id == 'alice'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    policy_id = int(created["id"])

    listed = await h.get_json(
        "/config-effective/agent-policies",
        service_policy_id=service_id,
    )
    assert listed["total"] == 1

    await h.patch_json(
        f"/config-effective/agent-policies/{policy_id}",
        {"match_expr": "user_id == 'bob'"},
    )

    await h.delete_ok(f"/config-effective/agent-policies/{policy_id}")


@pytest.mark.asyncio
async def test_global_policy_patch_and_delete(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    created = await h.post_json(
        "/config-effective/global-policies",
        {
            "priority": 0,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    policy_id = int(created["id"])

    patched = await h.patch_json(
        f"/config-effective/global-policies/{policy_id}",
        {"priority": 5},
    )
    assert patched["priority"] == 5

    await h.delete_ok(f"/config-effective/global-policies/{policy_id}")


@pytest.mark.asyncio
async def test_default_template_mapping_crud(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m4 = await h.post_json("/model-templates", model_templates()[3][1])
    template_id = m4["template_id"]

    created = await h.post_json(
        "/config-default-template-mappings",
        {
            "user_id": "carol",
            "group_id": None,
            "priority": 0,
            "template_id": template_id,
            "template_type": "default_model",
            "enabled": True,
        },
    )
    mapping_id = int(created["id"])

    listed = await h.get_json(
        "/config-default-template-mappings",
        user_id="carol",
        template_type="default_model",
    )
    assert listed["total"] == 1

    patched = await h.patch_json(
        f"/config-default-template-mappings/{mapping_id}",
        {"enabled": False},
    )
    assert patched["enabled"] is False

    await h.delete_ok(f"/config-default-template-mappings/{mapping_id}")
