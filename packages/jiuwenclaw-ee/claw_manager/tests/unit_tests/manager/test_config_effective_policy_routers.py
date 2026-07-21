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
            "policy_name": "销售服务策略",
            "service_id": "g_demo_sales::bot_main",
            "priority": 50,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    policy_id = int(created["id"])
    assert created["policy_id"]
    assert created["policy_name"] == "销售服务策略"

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
async def test_service_policy_list_search(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    created = await h.post_json(
        "/config-effective/service-policies",
        {
            "policy_name": "Sales Service Policy",
            "policy_desc": "route sales traffic",
            "service_id": "g_demo_sales::bot_main",
            "priority": 50,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-effective/service-policies",
        {
            "policy_name": "Support Service",
            "service_id": "support_pool",
            "priority": 10,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )

    policy_uuid = created["policy_id"]

    by_name = await h.get_json("/config-effective/service-policies", search="Sales Service")
    assert by_name["total"] == 1

    by_desc = await h.get_json("/config-effective/service-policies", search="sales traffic")
    assert by_desc["total"] == 1

    by_service_id = await h.get_json(
        "/config-effective/service-policies",
        search="g_demo_sales::bot_main",
    )
    assert by_service_id["total"] == 1

    by_priority = await h.get_json("/config-effective/service-policies", search="50")
    assert by_priority["total"] == 1
    assert by_priority["items"][0]["priority"] == 50

    by_match_expr = await h.get_json(
        "/config-effective/service-policies",
        search="group_id == 'g_demo_sales'",
    )
    assert by_match_expr["total"] == 1

    by_policy_id = await h.get_json(
        "/config-effective/service-policies",
        search=policy_uuid[:8],
    )
    assert by_policy_id["total"] == 1
    assert by_policy_id["items"][0]["policy_id"] == policy_uuid


@pytest.mark.asyncio
async def test_service_policy_list_sort(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    await h.post_json(
        "/config-effective/service-policies",
        {
            "policy_name": "Charlie Service",
            "policy_desc": "third",
            "service_id": "svc-charlie",
            "priority": 30,
            "match_expr": "group_id == 'charlie'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-effective/service-policies",
        {
            "policy_name": "Alpha Service",
            "policy_desc": "first",
            "service_id": "svc-alpha",
            "priority": 10,
            "match_expr": "group_id == 'alpha'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-effective/service-policies",
        {
            "policy_name": "Bravo Service",
            "policy_desc": "second",
            "service_id": "svc-bravo",
            "priority": 20,
            "match_expr": "group_id == 'bravo'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )

    by_name_asc = await h.get_json(
        "/config-effective/service-policies",
        sort_by="policy_name",
        sort_order="asc",
        page_size=50,
    )
    assert [item["policy_name"] for item in by_name_asc["items"]] == [
        "Alpha Service",
        "Bravo Service",
        "Charlie Service",
    ]

    by_service_id_desc = await h.get_json(
        "/config-effective/service-policies",
        sort_by="service_id",
        sort_order="desc",
        page_size=50,
    )
    assert [item["service_id"] for item in by_service_id_desc["items"]] == [
        "svc-charlie",
        "svc-bravo",
        "svc-alpha",
    ]

    default_order = await h.get_json(
        "/config-effective/service-policies",
        page_size=50,
    )
    assert default_order["total"] == 3


@pytest.mark.asyncio
async def test_service_policy_allows_duplicate_priority(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    await h.post_json(
        "/config-effective/service-policies",
        {
            "service_id": "svc-a",
            "priority": 50,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )

    second = await h.post_json(
        "/config-effective/service-policies",
        {
            "service_id": "svc-b",
            "priority": 50,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    assert second["priority"] == 50
    assert second["service_id"] == "svc-b"


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
    service_policy_id = service["policy_id"]

    created = await h.post_json(
        "/config-effective/agent-policies",
        {
            "agent_id": "alice",
            "service_policy_id": service_policy_id,
            "priority": 10,
            "match_expr": "user_id == 'alice'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    policy_id = int(created["id"])
    assert created["send_file_allowed"] is True

    listed = await h.get_json(
        "/config-effective/agent-policies",
        service_policy_id=service_policy_id,
    )
    assert listed["total"] == 1

    await h.patch_json(
        f"/config-effective/agent-policies/{policy_id}",
        {"match_expr": "user_id == 'bob'", "send_file_allowed": True},
    )

    await h.delete_ok(f"/config-effective/agent-policies/{policy_id}")


@pytest.mark.asyncio
async def test_agent_policy_list_search(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    service = await h.post_json(
        "/config-effective/service-policies",
        {
            "policy_name": "Parent Service",
            "service_id": "svc-parent",
            "priority": 1,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    service_policy_id = service["policy_id"]

    agent = await h.post_json(
        "/config-effective/agent-policies",
        {
            "policy_name": "Alice Agent Policy",
            "policy_desc": "vip users only",
            "agent_id": "agent_alice",
            "service_policy_id": service_policy_id,
            "priority": 10,
            "match_expr": "user_id == 'alice'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-effective/agent-policies",
        {
            "agent_id": "agent_bob",
            "service_policy_id": service_policy_id,
            "priority": 5,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )

    agent_policy_uuid = agent["policy_id"]

    by_name = await h.get_json("/config-effective/agent-policies", search="Alice Agent")
    assert by_name["total"] == 1

    by_desc = await h.get_json("/config-effective/agent-policies", search="vip users")
    assert by_desc["total"] == 1

    by_agent_id = await h.get_json("/config-effective/agent-policies", search="agent_alice")
    assert by_agent_id["total"] == 1

    by_service_policy_id = await h.get_json(
        "/config-effective/agent-policies",
        search=service_policy_id[:8],
    )
    assert by_service_policy_id["total"] == 2

    by_service_policy_name = await h.get_json(
        "/config-effective/agent-policies",
        search="Parent Service",
    )
    assert by_service_policy_name["total"] == 2

    by_priority = await h.get_json("/config-effective/agent-policies", search="10")
    assert by_priority["total"] == 1
    assert by_priority["items"][0]["priority"] == 10

    by_match_expr = await h.get_json(
        "/config-effective/agent-policies",
        search="user_id == 'alice'",
    )
    assert by_match_expr["total"] == 1

    by_policy_id = await h.get_json(
        "/config-effective/agent-policies",
        search=agent_policy_uuid[:8],
    )
    assert by_policy_id["total"] == 1
    assert by_policy_id["items"][0]["policy_id"] == agent_policy_uuid


@pytest.mark.asyncio
async def test_agent_policy_list_sort(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    service_alpha = await h.post_json(
        "/config-effective/service-policies",
        {
            "policy_name": "Alpha Service",
            "service_id": "svc-alpha",
            "priority": 1,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    service_bravo = await h.post_json(
        "/config-effective/service-policies",
        {
            "policy_name": "Bravo Service",
            "service_id": "svc-bravo",
            "priority": 1,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )

    await h.post_json(
        "/config-effective/agent-policies",
        {
            "policy_name": "Charlie Agent",
            "agent_id": "agent-charlie",
            "service_policy_id": service_bravo["policy_id"],
            "priority": 30,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-effective/agent-policies",
        {
            "policy_name": "Alpha Agent",
            "agent_id": "agent-alpha",
            "service_policy_id": service_alpha["policy_id"],
            "priority": 10,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-effective/agent-policies",
        {
            "policy_name": "Bravo Agent",
            "agent_id": "agent-bravo",
            "service_policy_id": service_bravo["policy_id"],
            "priority": 20,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )

    by_name_asc = await h.get_json(
        "/config-effective/agent-policies",
        sort_by="policy_name",
        sort_order="asc",
        page_size=50,
    )
    assert [item["policy_name"] for item in by_name_asc["items"]] == [
        "Alpha Agent",
        "Bravo Agent",
        "Charlie Agent",
    ]

    by_service_policy_asc = await h.get_json(
        "/config-effective/agent-policies",
        sort_by="service_policy_id",
        sort_order="asc",
        page_size=50,
    )
    assert [item["service_policy_id"] for item in by_service_policy_asc["items"]] == sorted(
        [
            service_alpha["policy_id"],
            service_bravo["policy_id"],
            service_bravo["policy_id"],
        ]
    )

    by_agent_id_desc = await h.get_json(
        "/config-effective/agent-policies",
        sort_by="agent_id",
        sort_order="desc",
        page_size=50,
    )
    assert [item["agent_id"] for item in by_agent_id_desc["items"]] == [
        "agent-charlie",
        "agent-bravo",
        "agent-alpha",
    ]

    by_priority_asc = await h.get_json(
        "/config-effective/agent-policies",
        sort_by="priority",
        sort_order="asc",
        page_size=50,
    )
    assert [item["priority"] for item in by_priority_asc["items"]] == [10, 20, 30]

    by_priority_desc = await h.get_json(
        "/config-effective/agent-policies",
        sort_by="priority",
        sort_order="desc",
        page_size=50,
    )
    assert [item["priority"] for item in by_priority_desc["items"]] == [30, 20, 10]


@pytest.mark.asyncio
async def test_service_policy_delete_blocked_by_agent_policies(manager_api: ManagerApiHarness):
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
    service_row_id = int(service["id"])
    service_policy_id = service["policy_id"]

    agent = await h.post_json(
        "/config-effective/agent-policies",
        {
            "agent_id": "alice",
            "service_policy_id": service_policy_id,
            "priority": 10,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    agent_row_id = int(agent["id"])

    resp = await h.http.delete(
        h.scoped_url(f"/config-effective/service-policies/{service_row_id}")
    )
    assert resp.status_code == 400
    assert "linked agent policies exist" in resp.json()["detail"]

    await h.delete_ok(f"/config-effective/agent-policies/{agent_row_id}")
    await h.delete_ok(f"/config-effective/service-policies/{service_row_id}")


@pytest.mark.asyncio
async def test_global_policy_list_search(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    alpha = await h.post_json(
        "/config-effective/global-policies",
        {
            "policy_name": "Alpha Global",
            "policy_desc": "fallback for sales",
            "priority": 42,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-effective/global-policies",
        {
            "policy_name": "Beta Global",
            "policy_desc": "other team",
            "priority": 7,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )

    by_name = await h.get_json("/config-effective/global-policies", search="Alpha")
    assert by_name["total"] == 1
    assert by_name["items"][0]["policy_name"] == "Alpha Global"

    by_desc = await h.get_json("/config-effective/global-policies", search="sales")
    assert by_desc["total"] == 1

    by_priority = await h.get_json("/config-effective/global-policies", search="42")
    assert by_priority["total"] == 1
    assert by_priority["items"][0]["priority"] == 42

    policy_uuid = alpha["policy_id"]
    by_policy_id = await h.get_json(
        "/config-effective/global-policies",
        search=policy_uuid[:8],
    )
    assert by_policy_id["total"] == 1
    assert by_policy_id["items"][0]["policy_id"] == policy_uuid


@pytest.mark.asyncio
async def test_global_policy_list_sort(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    await h.post_json(
        "/config-effective/global-policies",
        {
            "policy_name": "Charlie Global",
            "policy_desc": "third",
            "priority": 30,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-effective/global-policies",
        {
            "policy_name": "Alpha Global",
            "policy_desc": "first",
            "priority": 10,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-effective/global-policies",
        {
            "policy_name": "Bravo Global",
            "policy_desc": "second",
            "priority": 20,
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )

    by_name_asc = await h.get_json(
        "/config-effective/global-policies",
        sort_by="policy_name",
        sort_order="asc",
        page_size=50,
    )
    assert [item["policy_name"] for item in by_name_asc["items"]] == [
        "Alpha Global",
        "Bravo Global",
        "Charlie Global",
    ]

    by_priority_desc = await h.get_json(
        "/config-effective/global-policies",
        sort_by="priority",
        sort_order="desc",
        page_size=50,
    )
    assert [item["priority"] for item in by_priority_desc["items"]] == [30, 20, 10]

    default_order = await h.get_json(
        "/config-effective/global-policies",
        page_size=50,
    )
    assert default_order["total"] == 3
    assert {item["policy_name"] for item in default_order["items"]} == {
        "Alpha Global",
        "Bravo Global",
        "Charlie Global",
    }


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

    replaced = await h.patch_json(
        f"/config-effective/global-policies/{policy_id}",
        {"template_ref": {"service_config": [m1]}},
    )
    assert replaced["template_ref"] == {"service_config": [m1]}

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
            "scope_type": "user",
            "scope_id": "carol",
            "priority": 0,
            "template_id": template_id,
            "template_type": "default_model",
            "enabled": True,
        },
    )
    mapping_id = int(created["id"])

    listed = await h.get_json(
        "/config-default-template-mappings",
        scope_type="user",
        scope_id="carol",
        template_type="default_model",
    )
    assert listed["total"] == 1

    patched = await h.patch_json(
        f"/config-default-template-mappings/{mapping_id}",
        {"enabled": False},
    )
    assert patched["enabled"] is False

    await h.delete_ok(f"/config-default-template-mappings/{mapping_id}")


@pytest.mark.asyncio
async def test_mapping_list_search(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m4 = await h.post_json("/model-templates", model_templates()[3][1])
    template_id = m4["template_id"]

    alpha = await h.post_json(
        "/config-default-template-mappings",
        {
            "policy_name": "Alpha Mapping",
            "policy_desc": "sales team default",
            "scope_type": "user",
            "scope_id": "carol",
            "priority": 42,
            "template_id": template_id,
            "template_type": "default_model",
            "enabled": True,
        },
    )
    await h.post_json(
        "/config-default-template-mappings",
        {
            "policy_name": "Beta Mapping",
            "policy_desc": "other team",
            "scope_type": "user",
            "scope_id": "dave",
            "priority": 7,
            "template_id": template_id,
            "template_type": "video_model",
            "enabled": True,
        },
    )

    by_name = await h.get_json("/config-default-template-mappings", search="Alpha")
    assert by_name["total"] == 1
    assert by_name["items"][0]["policy_name"] == "Alpha Mapping"

    by_user = await h.get_json("/config-default-template-mappings", search="carol")
    assert by_user["total"] == 1

    by_group = await h.get_json("/config-default-template-mappings", search="sales")
    assert by_group["total"] >= 1

    by_slot = await h.get_json("/config-default-template-mappings", search="video_model")
    assert by_slot["total"] == 1
    assert by_slot["items"][0]["template_type"] == "video_model"

    by_template_id = await h.get_json(
        "/config-default-template-mappings",
        search=template_id[:8],
    )
    assert by_template_id["total"] == 2

    by_priority = await h.get_json("/config-default-template-mappings", search="42")
    assert by_priority["total"] == 1
    assert by_priority["items"][0]["priority"] == 42

    policy_uuid = alpha["policy_id"]
    by_policy_id = await h.get_json(
        "/config-default-template-mappings",
        search=policy_uuid[:8],
    )
    assert by_policy_id["total"] == 1
    assert by_policy_id["items"][0]["policy_id"] == policy_uuid


@pytest.mark.asyncio
async def test_service_policy_rejects_invalid_match_expr(manager_api: ManagerApiHarness):
    h = manager_api
    await h.create_instance()
    m1 = await _create_model_template(h)

    resp = await h.http.post(
        h.scoped_url("/config-effective/service-policies"),
        json={
            "service_id": "svc-1",
            "priority": 1,
            "match_expr": "group_id === 'g_demo_sales'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    assert resp.status_code == 400
    assert "invalid match_expr" in resp.json()["detail"]


@pytest.mark.asyncio
async def test_agent_policy_rejects_invalid_match_expr(manager_api: ManagerApiHarness):
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
    service_policy_id = service["policy_id"]
    agent = await h.post_json(
        "/config-effective/agent-policies",
        {
            "agent_id": "alice",
            "service_policy_id": service_policy_id,
            "priority": 10,
            "match_expr": "user_id == 'alice'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
        },
    )
    policy_id = int(agent["id"])

    resp = await h.http.patch(
        h.scoped_url(f"/config-effective/agent-policies/{policy_id}"),
        json={"match_expr": "${user::carol}"},
    )
    assert resp.status_code == 400
    assert "invalid match_expr" in resp.json()["detail"]
