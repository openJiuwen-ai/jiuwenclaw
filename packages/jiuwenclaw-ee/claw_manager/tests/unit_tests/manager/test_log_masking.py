# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""Claw Manager：日志脱敏内置种子与 Gateway sync push。"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw_manager.core.application_config.log_masking_rule import (
    _builtin_seed_rows,
    seed_builtin_log_masking_rules,
)


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
