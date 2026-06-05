# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Claw Manager：日志脱敏内置种子、Gateway sync push 与 REST CRUD。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from conftest import ManagerApiHarness
from jiuwenclaw_manager.core.application_config.log_masking_rule import (
    _builtin_seed_rows,
    seed_builtin_log_masking_rules,
)
from jiuwenclaw_manager.models.application_config_models import LOG_MASKING_RULE_TABLE_DEF

_BUILTIN_IDS = {row["rule_id"] for row in _builtin_seed_rows("placeholder")}


async def _bootstrap_builtin_rules(h: ManagerApiHarness) -> None:
    await h.create_instance(name="log-masking-rest-ut")
    await seed_builtin_log_masking_rules(h.handler, h.jiuwenclaw_id)


def _log_masking_url(h: ManagerApiHarness, suffix: str = "") -> str:
    return h.scoped_url(f"/log-masking-rules{suffix}")


async def _mdb_rule_ids(h: ManagerApiHarness) -> set[str]:
    rows = await h.handler.list_records(
        LOG_MASKING_RULE_TABLE_DEF.table_name,
        {"jiuwenclaw_id": h.jiuwenclaw_id},
    )
    return {str(getattr(row, "rule_id", "") or "") for row in rows}


@pytest.mark.asyncio
async def test_seed_builtin_log_masking_rules_writes_missing_rows():
    jiuwenclaw_id = "sp-test-seed"
    seeds = _builtin_seed_rows(jiuwenclaw_id)
    handler = MagicMock()
    handler.list_records = AsyncMock(return_value=[])
    handler.create = AsyncMock(
        side_effect=lambda _table, payload: SimpleNamespace(**payload, id=1)
    )

    created = await seed_builtin_log_masking_rules(handler, jiuwenclaw_id)

    assert created == len(seeds)
    assert handler.create.await_count == len(seeds)
    created_rule_ids = {call.args[1]["rule_id"] for call in handler.create.await_args_list}
    assert "builtin_kv_sensitive" in created_rule_ids
    assert all(call.args[1]["source"] == "builtin" for call in handler.create.await_args_list)
    assert all(call.args[1]["jiuwenclaw_id"] == jiuwenclaw_id for call in handler.create.await_args_list)


@pytest.mark.asyncio
async def test_seed_builtin_log_masking_rules_is_idempotent():
    jiuwenclaw_id = "sp-test-seed"
    existing = SimpleNamespace(rule_id="builtin_kv_sensitive")
    handler = MagicMock()
    handler.list_records = AsyncMock(return_value=[existing])
    handler.create = AsyncMock()

    created = await seed_builtin_log_masking_rules(handler, jiuwenclaw_id)

    assert created == len(_builtin_seed_rows(jiuwenclaw_id)) - 1
    created_rule_ids = {
        call.args[1]["rule_id"] for call in handler.create.await_args_list
    }
    assert "builtin_kv_sensitive" not in created_rule_ids


@pytest.mark.asyncio
async def test_push_log_masking_rules_sync_to_gateway():
    from jiuwenclaw_manager.core.application_config.log_masking_rule import (
        push_log_masking_rules_sync_to_gateway,
    )

    row = SimpleNamespace(
        id=1,
        jiuwenclaw_id="sp-sync",
        rule_id="builtin_email",
        rule_name="邮箱",
        description=None,
        pattern=r"\b[a-z]+@example\.com\b",
        replacement="******",
        priority=30,
        source="builtin",
        enabled=True,
        data=None,
        created_at=None,
        updated_at=None,
    )
    handler = MagicMock()
    handler.list_records = AsyncMock(return_value=[row])

    with patch(
        "jiuwenclaw_manager.core.application_config.log_masking_rule.push_log_masking_rule_op",
        new_callable=AsyncMock,
        return_value={"revision": "rev-1", "success_flag": True},
    ) as push_mock:
        ack = await push_log_masking_rules_sync_to_gateway(handler, "sp-sync")

    assert ack["revision"] == "rev-1"
    push_mock.assert_awaited_once()
    args, kwargs = push_mock.await_args
    assert args[0] == "sp-sync"
    assert args[1] == "sync"
    assert len(kwargs["rules"]) == 1
    assert kwargs["rules"][0]["rule_id"] == "builtin_email"
    assert "id" not in kwargs["rules"][0]


@pytest.mark.asyncio
async def test_rest_create_always_sets_source_custom():
    from jiuwenclaw_manager.core.application_config.log_masking_rule import (
        LogMaskingRuleService,
    )
    from jiuwenclaw_manager.schemas.application_config_schemas import (
        LogMaskingRuleCreateBody,
    )

    handler = MagicMock()
    handler.create = AsyncMock(
        return_value=SimpleNamespace(
            id=1,
            jiuwenclaw_id="sp-rest",
            rule_id="custom-rule-1",
            rule_name="test",
            description=None,
            pattern=r"secret=\d+",
            replacement="******",
            priority=5,
            source="custom",
            enabled=True,
            data=None,
            created_at=None,
            updated_at=None,
        )
    )
    svc = LogMaskingRuleService(handler)
    body = LogMaskingRuleCreateBody(
        rule_name="test",
        pattern=r"secret=\d+",
        priority=5,
    )

    with patch(
        "jiuwenclaw_manager.core.application_config.log_masking_rule.push_log_masking_rule_op",
        new_callable=AsyncMock,
        return_value={"revision": "rev-1", "success_flag": True},
    ):
        out = await svc.create("sp-rest", body)

    assert out.source == "custom"
    create_payload = handler.create.await_args.args[1]
    assert create_payload["source"] == "custom"


@pytest.mark.asyncio
async def test_rest_update_ignores_source_field():
    from jiuwenclaw_manager.core.application_config.log_masking_rule import (
        LogMaskingRuleService,
    )
    existing = SimpleNamespace(
        id=1,
        jiuwenclaw_id="sp-rest",
        rule_id="custom-rule-1",
        rule_name="test",
        description=None,
        pattern=r"secret=\d+",
        replacement="******",
        priority=5,
        source="custom",
        enabled=True,
        data=None,
        created_at=None,
        updated_at=None,
    )
    handler = MagicMock()
    handler.get = AsyncMock(return_value=existing)
    handler.update = AsyncMock(return_value=existing)

    svc = LogMaskingRuleService(handler)
    body = MagicMock()
    body.model_dump.return_value = {"enabled": False, "source": "builtin"}

    with patch(
        "jiuwenclaw_manager.core.application_config.log_masking_rule.push_log_masking_rule_op",
        new_callable=AsyncMock,
        return_value={"revision": "rev-2", "success_flag": True},
    ) as push_mock:
        await svc.update("sp-rest", "custom-rule-1", body)

    push_mock.assert_awaited_once()
    _, kwargs = push_mock.await_args
    assert "source" not in kwargs["updates"]
    db_updates = handler.update.await_args.args[2]
    assert "source" not in db_updates


# ---------------------------------------------------------------------------
# 《日志脱敏规则下发.md》§10.3 REST CRUD
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_rest_list_and_get_after_bootstrap(manager_api: ManagerApiHarness):
    """§10.3.1：bootstrap 后列表含 builtin 种子，单条 builtin_email 可查询。"""
    h = manager_api
    await _bootstrap_builtin_rules(h)

    list_resp = await h.http.get(_log_masking_url(h))
    assert list_resp.status_code == 200
    list_body = list_resp.json()
    assert list_body["code"] == 200
    items = list_body["data"]["items"]
    assert items
    rule_ids = {item["rule_id"] for item in items}
    assert "builtin_email" in rule_ids
    assert _BUILTIN_IDS.issubset(rule_ids)

    enabled_resp = await h.http.get(_log_masking_url(h), params={"enabled": "true"})
    assert enabled_resp.status_code == 200
    enabled_ids = {
        item["rule_id"] for item in enabled_resp.json()["data"]["items"]
    }
    assert "builtin_email" in enabled_ids

    get_resp = await h.http.get(_log_masking_url(h, "/builtin_email"))
    assert get_resp.status_code == 200
    assert get_resp.json()["data"]["rule_id"] == "builtin_email"


@pytest.mark.asyncio
async def test_rest_create_custom_rule(manager_api: ManagerApiHarness):
    """§10.3.2：POST 生成 UUID，source 为 custom。"""
    h = manager_api
    await _bootstrap_builtin_rules(h)

    create_resp = await h.http.post(
        _log_masking_url(h),
        json={
            "rule_name": "订单号脱敏",
            "description": "REST 验证用",
            "pattern": r"ORD-[0-9]{10,}",
            "replacement": "******",
            "priority": 60,
            "enabled": True,
        },
    )
    assert create_resp.status_code == 200
    created = create_resp.json()["data"]
    assert created["source"] == "custom"
    rule_id = created["rule_id"]
    assert rule_id
    assert len(rule_id) >= 8


@pytest.mark.asyncio
async def test_rest_create_invalid_pattern_returns_400(
    manager_api: ManagerApiHarness,
):
    """§10.3.2：非法 pattern 返回 400，MDB 无新增行。"""
    h = manager_api
    await _bootstrap_builtin_rules(h)
    before = await _mdb_rule_ids(h)

    bad_resp = await h.http.post(
        _log_masking_url(h),
        json={"rule_name": "bad", "pattern": "(", "enabled": True},
    )
    assert bad_resp.status_code == 400
    assert await _mdb_rule_ids(h) == before


@pytest.mark.asyncio
async def test_rest_patch_custom_rule_and_disable_builtin_email(
    manager_api: ManagerApiHarness,
):
    """§10.3.3：PATCH 自定义规则 replacement/priority；关闭 builtin_email。"""
    h = manager_api
    await _bootstrap_builtin_rules(h)

    create_resp = await h.http.post(
        _log_masking_url(h),
        json={
            "rule_name": "订单号脱敏",
            "pattern": r"ORD-[0-9]{10,}",
            "replacement": "******",
            "priority": 60,
            "enabled": True,
        },
    )
    assert create_resp.status_code == 200
    rule_id = create_resp.json()["data"]["rule_id"]

    patch_resp = await h.http.patch(
        _log_masking_url(h, f"/{rule_id}"),
        json={"enabled": True, "replacement": "REDACT", "priority": 120},
    )
    assert patch_resp.status_code == 200
    patched = patch_resp.json()["data"]
    assert patched["replacement"] == "REDACT"
    assert patched["priority"] == 120

    disable_resp = await h.http.patch(
        _log_masking_url(h, "/builtin_email"),
        json={"enabled": False},
    )
    assert disable_resp.status_code == 200
    assert disable_resp.json()["data"]["enabled"] is False


@pytest.mark.asyncio
async def test_rest_delete_then_get_404(manager_api: ManagerApiHarness):
    """§10.3.4：DELETE 后再次 GET 返回 404。"""
    h = manager_api
    await _bootstrap_builtin_rules(h)

    create_resp = await h.http.post(
        _log_masking_url(h),
        json={
            "rule_name": "订单号脱敏",
            "pattern": r"ORD-[0-9]{10,}",
            "priority": 60,
            "enabled": True,
        },
    )
    assert create_resp.status_code == 200
    rule_id = create_resp.json()["data"]["rule_id"]

    delete_resp = await h.http.delete(_log_masking_url(h, f"/{rule_id}"))
    assert delete_resp.status_code == 200

    missing_resp = await h.http.get(_log_masking_url(h, f"/{rule_id}"))
    assert missing_resp.status_code == 404
