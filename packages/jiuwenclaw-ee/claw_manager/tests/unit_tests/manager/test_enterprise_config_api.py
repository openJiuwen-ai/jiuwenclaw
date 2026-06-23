# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""企业级配置演示数据 REST 流程测试。"""

from __future__ import annotations

from typing import Any

import pytest

from conftest import ManagerApiHarness
from demo_payloads import (
    extension_config_templates,
    model_templates,
    service_config_templates,
    skill_whitelist_templates,
)

pytestmark = pytest.mark.unit


async def _seed_enterprise_demo(manager_api: ManagerApiHarness) -> dict[str, Any]:
    """按文档 §2.1–§2.8 顺序写入演示配置，返回关键 id。"""
    h = manager_api
    await h.create_instance(name="enterprise-config-ut")

    template_ids: dict[str, str] = {}
    for key, body in model_templates():
        row = await h.post_json("/model-templates", body)
        template_ids[key.lower()] = row["template_id"]

    m1, m2, m3, m4, m5 = (
        template_ids["m1"],
        template_ids["m2"],
        template_ids["m3"],
        template_ids["m4"],
        template_ids["m5"],
    )
    group_map_default_model = f"${{group::g_demo_sales}} or {m1}"

    ext_ids: dict[str, str] = {}
    for key, body in extension_config_templates():
        row = await h.post_json("/extension-config-templates", body)
        ext_ids[key.lower()] = row["template_id"]
    e1, e2, e3, e4 = ext_ids["e1"], ext_ids["e2"], ext_ids["e3"], ext_ids["e4"]

    skill_ids: dict[str, str] = {}
    for key, body in skill_whitelist_templates():
        row = await h.post_json("/skill-whitelist-templates", body)
        skill_ids[key.lower()] = row["template_id"]
    w1, w2, w3 = skill_ids["w1"], skill_ids["w2"], skill_ids["w3"]

    svc_ids: dict[str, str] = {}
    for key, body in service_config_templates():
        row = await h.post_json("/service-config-templates", body)
        svc_ids[key.lower()] = row["template_id"]
    s1, s2 = svc_ids["s1"], svc_ids["s2"]

    sales = await h.post_json(
        "/config-effective/service-policies",
        {
            "service_id": "${group_id}::${bot_id}",
            "priority": 100,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {
                "default_model": [m2],
                "vision_model": [m2],
                "skill_whitelist": [w1, w2],
                "extension_config": [e1, e2],
                "service_config": [s1],
            },
            "enabled": True,
            "data": {},
        },
    )
    sales_policy_id = sales["policy_id"]

    fallback = await h.post_json(
        "/config-effective/service-policies",
        {
            "service_id": "${group_id}::${bot_id}",
            "priority": 10,
            "match_expr": "group_id == 'g_demo_sales'",
            "template_ref": {"default_model": [m1]},
            "enabled": True,
            "data": {},
        },
    )
    fallback_id = int(fallback["id"])

    vip = await h.post_json(
        "/config-effective/agent-policies",
        {
            "agent_id": "${user_id}",
            "service_policy_id": sales_policy_id,
            "priority": 100,
            "match_expr": "user_id == 'alice'",
            "template_ref": {
                "default_model": [m3],
                "vision_model": [m3],
                "skill_whitelist": [w1],
                "extension_config": [e3],
            },
            "enabled": True,
            "data": {
                "demo_context": {
                    "group_id": "g_demo_sales",
                    "bot_id": "bot_main",
                    "user_id": "alice",
                }
            },
        },
    )
    vip_id = int(vip["id"])

    mapping_rule = await h.post_json(
        "/config-effective/agent-policies",
        {
            "agent_id": "default_agent_id_1",
            "service_policy_id": sales_policy_id,
            "priority": 0,
            "match_expr": "",
            "template_ref": {"default_model": [group_map_default_model]},
            "enabled": True,
            "data": {"remark": "group:: 查 2.8.2，or 右侧为 M1"},
        },
    )
    mapping_policy_id = int(mapping_rule["id"])

    global_row = await h.post_json(
        "/config-effective/global-policies",
        {
            "priority": 0,
            "template_ref": {
                "default_model": [m1],
                "video_model": [m1],
                "audio_model": [m1],
                "vision_model": [m1],
                "skill_whitelist": [w3],
                "extension_config": [e4],
                "service_config": [s2],
            },
            "enabled": True,
            "data": {},
        },
    )
    global_id = int(global_row["id"])

    carol_map = await h.post_json(
        "/config-default-template-mappings",
        {
            "user_id": "carol",
            "group_id": None,
            "priority": 0,
            "template_id": m4,
            "template_type": "default_model",
            "enabled": True,
            "data": {"remark": "用户级 default_model 映射"},
        },
    )
    carol_map_id = int(carol_map["id"])

    group_map = await h.post_json(
        "/config-default-template-mappings",
        {
            "user_id": None,
            "group_id": "g_demo_sales",
            "priority": 0,
            "template_id": m5,
            "template_type": "default_model",
            "enabled": True,
            "data": {"remark": "组级 default_model 映射"},
        },
    )
    group_map_id = int(group_map["id"])

    return {
        "jiuwenclaw_id": h.jiuwenclaw_id,
        "m1": m1,
        "m2": m2,
        "m3": m3,
        "m4": m4,
        "m5": m5,
        "s1": s1,
        "s2": s2,
        "w3": w3,
        "sales_id": sales_id,
        "fallback_id": fallback_id,
        "vip_id": vip_id,
        "mapping_policy_id": mapping_policy_id,
        "global_id": global_id,
        "carol_map_id": carol_map_id,
        "group_map_id": group_map_id,
        "group_map_default_model": group_map_default_model,
    }


@pytest.mark.asyncio
async def test_seed_enterprise_demo_config_full_workflow(manager_api: ManagerApiHarness):
    """§2.1–§2.8：完整演示数据写入后各资源可查询。"""
    ids = await _seed_enterprise_demo(manager_api)
    h = manager_api

    models = await h.get_json("/model-templates", page_size=50)
    assert models["total"] == 5
    assert ids["m1"] in {item["template_id"] for item in models["items"]}

    sales = await h.get_json(f"/config-effective/service-policies/{ids['sales_id']}")
    assert sales["priority"] == 100
    assert sales["template_ref"]["default_model"] == [ids["m2"]]
    assert sales["template_ref"]["service_config"] == [ids["s1"]]
    assert len(sales["template_ref"]["skill_whitelist"]) == 2

    vip = await h.get_json(f"/config-effective/agent-policies/{ids['vip_id']}")
    assert vip["match_expr"] == "user_id == 'alice'"
    assert vip["template_ref"]["default_model"] == [ids["m3"]]

    mapping_agent = await h.get_json(
        f"/config-effective/agent-policies/{ids['mapping_policy_id']}"
    )
    assert ids["group_map_default_model"] in mapping_agent["template_ref"]["default_model"]

    global_policy = await h.get_json(f"/config-effective/global-policies/{ids['global_id']}")
    assert global_policy["template_ref"]["skill_whitelist"] == [ids["w3"]]
    assert global_policy["template_ref"]["service_config"] == [ids["s2"]]

    carol = await h.get_json(
        f"/config-default-template-mappings/{ids['carol_map_id']}"
    )
    assert carol["user_id"] == "carol"
    assert carol["template_id"] == ids["m4"]

    group = await h.get_json(
        f"/config-default-template-mappings/{ids['group_map_id']}"
    )
    assert group["group_id"] == "g_demo_sales"
    assert group["template_id"] == ids["m5"]

    service_list = await h.get_json("/config-effective/service-policies")
    assert service_list["total"] == 2
    assert {ids["sales_id"], ids["fallback_id"]} == {
        item["id"] for item in service_list["items"]
    }


@pytest.mark.asyncio
async def test_patch_model_template_m3_model_id(manager_api: ManagerApiHarness):
    """§3.1.4：PATCH 更新 M3 的 model_id，策略表 template_ref 仍引用同一 UUID。"""
    ids = await _seed_enterprise_demo(manager_api)
    m3 = ids["m3"]

    patched = await manager_api.patch_json(
        f"/model-templates/{m3}",
        {"model_id": "gpt-5-modify"},
    )
    assert patched["model_id"] == "gpt-5-modify"

    fetched = await manager_api.get_json(f"/model-templates/{m3}")
    assert fetched["model_id"] == "gpt-5-modify"

    vip = await manager_api.get_json(
        f"/config-effective/agent-policies/{ids['vip_id']}"
    )
    assert vip["template_ref"]["default_model"] == [m3]


@pytest.mark.asyncio
async def test_global_policy_allows_multiple_per_instance(manager_api: ManagerApiHarness):
    """§2.7：同一实例可创建多条 global policy。"""
    await manager_api.create_instance()
    h = manager_api

    first = await h.post_json(
        "/config-effective/global-policies",
        {
            "policy_name": "global-a",
            "priority": 0,
            "template_ref": {"default_model": []},
            "enabled": True,
        },
    )
    second = await h.post_json(
        "/config-effective/global-policies",
        {
            "policy_name": "global-b",
            "priority": 10,
            "template_ref": {"default_model": []},
            "enabled": True,
        },
    )
    assert first["id"] != second["id"]
    assert first["policy_id"] != second["policy_id"]


@pytest.mark.asyncio
async def test_service_policy_requires_valid_instance(manager_api: ManagerApiHarness):
    """实例不存在时创建 service policy 返回 400。"""
    h = manager_api
    h.jiuwenclaw_id = "sp-nonexistent"

    resp = await h.http.post(
        h.scoped_url("/config-effective/service-policies"),
        json={
            "service_id": "svc-1",
            "priority": 1,
            "template_ref": {},
            "enabled": True,
        },
    )
    assert resp.status_code == 400
    assert "unknown jiuwenclaw_id" in resp.json()["detail"]
