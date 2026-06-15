# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""模板 CRUD API（template_routers）单元测试。"""

from __future__ import annotations

import pytest

from conftest import ManagerApiHarness
from demo_payloads import (
    extension_config_templates,
    model_templates,
    service_config_templates,
    skill_whitelist_templates,
)

pytestmark = pytest.mark.unit

_TEMPLATE_CASES = [
    ("/model-templates", model_templates()[0][1]),
    ("/extension-config-templates", extension_config_templates()[0][1]),
    ("/skill-whitelist-templates", skill_whitelist_templates()[0][1]),
    ("/service-config-templates", service_config_templates()[0][1]),
]


@pytest.mark.parametrize("path,create_body", _TEMPLATE_CASES)
@pytest.mark.asyncio
async def test_template_crud_lifecycle(
    manager_api: ManagerApiHarness,
    path: str,
    create_body: dict,
):
    h = manager_api

    created = await h.post_json(path, create_body)
    template_id = created["template_id"]
    assert template_id
    assert created["template_name"] == create_body["template_name"]

    fetched = await h.get_json(f"{path}/{template_id}")
    assert fetched["template_id"] == template_id

    listed = await h.get_json(path, page=1, page_size=50)
    assert listed["total"] >= 1
    assert template_id in {item["template_id"] for item in listed["items"]}

    patched = await h.patch_json(
        f"{path}/{template_id}",
        {"enabled": False},
    )
    assert patched["enabled"] is False

    await h.delete_ok(f"{path}/{template_id}")

    missing = await h.http.get(h.templates_url(f"{path}/{template_id}"))
    assert missing.status_code == 404


@pytest.mark.asyncio
async def test_model_template_list_filter_by_model_type(manager_api: ManagerApiHarness):
    h = manager_api
    for _key, body in model_templates():
        await h.post_json("/model-templates", body)

    vision_rows = await h.get_json("/model-templates", model_type="vision")
    assert vision_rows["total"] >= 1
    for item in vision_rows["items"]:
        model_type = item["model_type"]
        if isinstance(model_type, list):
            assert "vision" in model_type
        else:
            assert model_type == "vision"


@pytest.mark.asyncio
async def test_extension_config_template_list_filter(manager_api: ManagerApiHarness):
    h = manager_api
    for _key, body in extension_config_templates():
        await h.post_json("/extension-config-templates", body)

    gateway_hooks = await h.get_json(
        "/extension-config-templates",
        component="gateway",
        hook_type="pre_request",
    )
    assert gateway_hooks["total"] >= 1
    for item in gateway_hooks["items"]:
        assert item["component"] == "gateway"
        assert item["hook_type"] == "pre_request"
