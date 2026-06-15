# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from jiuwenclaw.utils import get_checkpoint_dir, get_multi_tenant_user_workspace_dir


def test_get_checkpoint_dir_default_unchanged():
    expected = get_multi_tenant_user_workspace_dir("default", "default") / ".checkpoint"
    assert get_checkpoint_dir() == expected
    assert get_checkpoint_dir(None, None) == expected


def test_get_checkpoint_dir_tenant_scoped():
    service_id = "vibeskill_test_session"
    agent_id = "agent_default"
    expected = get_multi_tenant_user_workspace_dir(service_id, agent_id) / ".checkpoint"
    assert get_checkpoint_dir(service_id, agent_id) == expected
    assert "service_vibeskill_test_session" in str(expected)


@pytest.mark.asyncio
async def test_ensure_persistent_checkpointer_uses_tenant_path(tmp_path, monkeypatch):
    service_id = "tenant_svc"
    agent_id = "tenant_agent"
    tenant_ckpt = tmp_path / f"service_{service_id}" / f"agent_{agent_id}" / ".checkpoint"

    monkeypatch.setattr(
        "jiuwenclaw.agentserver.checkpoint_setup.get_checkpoint_dir",
        lambda sid=None, aid=None: tenant_ckpt
        if sid == service_id and aid == agent_id
        else tmp_path / "default" / ".checkpoint",
    )

    mock_cp = object()
    with patch(
        "openjiuwen.core.session.checkpointer.CheckpointerFactory.create",
        new_callable=AsyncMock,
        return_value=mock_cp,
    ) as create_mock, patch(
        "openjiuwen.core.session.checkpointer.CheckpointerFactory.set_default_checkpointer",
    ) as set_default_mock, patch(
        "openjiuwen.core.session.checkpointer.persistence.PersistenceCheckpointerProvider",
    ):
        from jiuwenclaw.agentserver.checkpoint_setup import ensure_persistent_checkpointer

        await ensure_persistent_checkpointer(service_id, agent_id)

    create_mock.assert_awaited_once()
    conf = create_mock.await_args[0][0].conf
    assert conf["db_path"] == f"{tenant_ckpt}/checkpoint"
    set_default_mock.assert_called_once_with(mock_cp)
    assert tenant_ckpt.is_dir()


@pytest.mark.asyncio
async def test_ensure_persistent_checkpointer_reuses_cached_instance(tmp_path, monkeypatch):
    ckpt_dir = tmp_path / ".checkpoint"
    monkeypatch.setattr(
        "jiuwenclaw.agentserver.checkpoint_setup.get_checkpoint_dir",
        lambda sid=None, aid=None: ckpt_dir,
    )

    mock_cp = object()
    with patch(
        "openjiuwen.core.session.checkpointer.CheckpointerFactory.create",
        new_callable=AsyncMock,
        return_value=mock_cp,
    ) as create_mock, patch(
        "openjiuwen.core.session.checkpointer.CheckpointerFactory.set_default_checkpointer",
    ) as set_default_mock, patch(
        "openjiuwen.core.session.checkpointer.persistence.PersistenceCheckpointerProvider",
    ):
        from jiuwenclaw.agentserver import checkpoint_setup

        checkpoint_setup._checkpointers.clear()
        await checkpoint_setup.ensure_persistent_checkpointer("a", "b")
        await checkpoint_setup.ensure_persistent_checkpointer("a", "b")

    create_mock.assert_awaited_once()
    assert set_default_mock.call_count == 2
