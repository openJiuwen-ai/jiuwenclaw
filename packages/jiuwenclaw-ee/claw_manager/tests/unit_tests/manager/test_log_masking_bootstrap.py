"""Gateway 注册时 log_masking bootstrap（``log_masking_seeded`` 仅 seed 一次）。"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw_manager.core.instance.instance_service import (
    bootstrap_gateway_log_masking,
    is_log_masking_seeded,
    merge_instance_data,
)


@pytest.mark.asyncio
async def test_is_log_masking_seeded_reads_instance_data():
    handler = AsyncMock()
    row = MagicMock(data={"log_masking_seeded": True})
    handler.get = AsyncMock(return_value=row)

    assert await is_log_masking_seeded(handler, "sp-abc") is True
    handler.get.assert_awaited_once()


@pytest.mark.asyncio
async def test_bootstrap_skips_seed_when_already_seeded():
    handler = AsyncMock()
    row = MagicMock(data={"log_masking_seeded": True})
    handler.get = AsyncMock(return_value=row)

    with (
        patch(
            "jiuwenclaw_manager.core.application_config.log_masking_rule.seed_builtin_log_masking_rules",
            new_callable=AsyncMock,
        ) as seed_mock,
        patch(
            "jiuwenclaw_manager.core.application_config.log_masking_rule.push_log_masking_rules_sync_to_gateway",
            new_callable=AsyncMock,
            return_value={"revision": "1"},
        ) as sync_mock,
        patch(
            "jiuwenclaw_manager.core.instance.instance_service.merge_instance_data",
            new_callable=AsyncMock,
        ) as merge_mock,
    ):
        await bootstrap_gateway_log_masking(handler, "sp-abc")

    seed_mock.assert_not_awaited()
    sync_mock.assert_awaited_once_with(handler, "sp-abc")
    merge_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_bootstrap_seeds_once_and_sets_flag():
    handler = AsyncMock()
    handler.get = AsyncMock(return_value=MagicMock(data=None))

    with (
        patch(
            "jiuwenclaw_manager.core.application_config.log_masking_rule.seed_builtin_log_masking_rules",
            new_callable=AsyncMock,
            return_value=3,
        ) as seed_mock,
        patch(
            "jiuwenclaw_manager.core.application_config.log_masking_rule.push_log_masking_rules_sync_to_gateway",
            new_callable=AsyncMock,
            return_value={"revision": "1"},
        ),
        patch(
            "jiuwenclaw_manager.core.instance.instance_service.merge_instance_data",
            new_callable=AsyncMock,
        ) as merge_mock,
    ):
        await bootstrap_gateway_log_masking(handler, "sp-new")

    seed_mock.assert_awaited_once_with(handler, "sp-new")
    merge_mock.assert_awaited_once_with(
        handler, "sp-new", {"log_masking_seeded": True}
    )


@pytest.mark.asyncio
async def test_merge_instance_data_sets_log_masking_seeded():
    handler = AsyncMock()
    row = MagicMock(data={"gateway_version": "1.0"})
    handler.get = AsyncMock(return_value=row)
    handler.update = AsyncMock(return_value=row)

    await merge_instance_data(handler, "sp-x", {"log_masking_seeded": True})

    handler.update.assert_awaited_once()
    _table, _filters, updates = handler.update.await_args.args
    assert updates["data"]["log_masking_seeded"] is True
    assert updates["data"]["gateway_version"] == "1.0"
