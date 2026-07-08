# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.

"""Tests for process-wide checkpoint singleton in interface_deep."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent import interface_deep as iface
from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter


@pytest.fixture(autouse=True)
def _reset_checkpoint_singleton():
    iface.reset_shared_checkpoint_for_tests()
    yield
    iface.reset_shared_checkpoint_for_tests()


@pytest.mark.asyncio
async def test_set_checkpoint_initializes_once(monkeypatch):
    monkeypatch.delenv("GATEWAY_DB_TYPE", raising=False)
    mock_checkpointer = MagicMock(name="checkpointer")

    with patch.object(
        iface.CheckpointerFactory,
        "create",
        new=AsyncMock(return_value=mock_checkpointer),
    ) as create_mock, patch.object(
        iface.CheckpointerFactory,
        "set_default_checkpointer",
    ) as set_default_mock, patch.object(
        iface,
        "_build_mysql_handler_engine",
        new=AsyncMock(),
    ) as mysql_mock:
        await JiuWenClawDeepAdapter.set_checkpoint()
        await JiuWenClawDeepAdapter.set_checkpoint()

    assert create_mock.await_count == 1
    assert set_default_mock.call_count == 2
    set_default_mock.assert_called_with(mock_checkpointer)
    mysql_mock.assert_not_awaited()


@pytest.mark.asyncio
async def test_set_checkpoint_reuses_mysql_engine(monkeypatch):
    monkeypatch.setenv("GATEWAY_DB_TYPE", "mysql")
    mock_engine = MagicMock(name="mysql_engine")
    mock_checkpointer = MagicMock(name="checkpointer")

    with patch.object(
        iface,
        "_build_mysql_handler_engine",
        new=AsyncMock(return_value=mock_engine),
    ) as mysql_mock, patch.object(
        iface.CheckpointerFactory,
        "create",
        new=AsyncMock(return_value=mock_checkpointer),
    ) as create_mock:
        await asyncio.gather(
            JiuWenClawDeepAdapter.set_checkpoint(),
            JiuWenClawDeepAdapter.set_checkpoint(),
            JiuWenClawDeepAdapter.set_checkpoint(),
        )

    assert mysql_mock.await_count == 1
    assert create_mock.await_count == 1
    create_conf = create_mock.await_args_list[0].args[0].conf
    assert create_conf["db_client"] is mock_engine
