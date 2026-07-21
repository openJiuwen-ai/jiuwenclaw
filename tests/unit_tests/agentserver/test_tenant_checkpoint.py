# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tests for tenant-scoped checkpoint setup."""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from jiuwenclaw.agentserver.deep_agent.interface_deep import JiuWenClawDeepAdapter


@pytest.mark.asyncio
async def test_set_checkpoint_uses_tenant_workspace(tmp_path: Path):
    office_ws = tmp_path / "service_default" / "agent_office"
    assistant_ws = tmp_path / "service_default" / "agent_assistant"

    paths = {
        ("default", "office"): office_ws,
        ("default", "assistant"): assistant_ws,
    }

    def _fake_workspace(service_id: str, agent_id: str) -> Path:
        return paths[(service_id, agent_id)]

    mock_cp = MagicMock()
    created_confs: list[dict] = []

    async def _fake_create(config):
        created_confs.append(config.conf)
        return mock_cp

    with (
        patch(
            "jiuwenclaw.utils.get_multi_tenant_user_workspace_dir",
            side_effect=_fake_workspace,
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.CheckpointerFactory.create",
            new_callable=AsyncMock,
            side_effect=_fake_create,
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.PersistenceCheckpointerProvider",
        ),
    ):
        office_adapter = JiuWenClawDeepAdapter(
            workspace_dir=str(office_ws / "agent" / "jiuwenclaw_workspace"),
            agent_id="office",
            service_id="default",
        )
        assistant_adapter = JiuWenClawDeepAdapter(
            workspace_dir=str(assistant_ws / "agent" / "jiuwenclaw_workspace"),
            agent_id="assistant",
            service_id="default",
        )

        await office_adapter.set_checkpoint()
        await assistant_adapter.set_checkpoint()

    assert len(created_confs) == 2
    office_db = created_confs[0]["db_path"]
    assistant_db = created_confs[1]["db_path"]
    assert "agent_office" in office_db
    assert "agent_assistant" in assistant_db
    assert office_db != assistant_db
    assert office_adapter._checkpointer is mock_cp
    assert assistant_adapter._checkpointer is mock_cp


@pytest.mark.asyncio
async def test_set_checkpoint_uses_env_agent_id_under_agent_runtime(tmp_path: Path):
    """AGENT_RUNTIME rewrites agent_id to cache_key; checkpoint must still use env_agent_id."""
    office_ws = tmp_path / "service_default" / "agent_office"
    rewritten_ws = tmp_path / "service_default" / "agent_office_default"

    paths = {
        ("default", "office"): office_ws,
        ("default", "office_default"): rewritten_ws,
    }

    def _fake_workspace(service_id: str, agent_id: str) -> Path:
        return paths[(service_id, agent_id)]

    created_confs: list[dict] = []

    async def _fake_create(config):
        created_confs.append(config.conf)
        return MagicMock()

    with (
        patch(
            "jiuwenclaw.utils.get_multi_tenant_user_workspace_dir",
            side_effect=_fake_workspace,
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.CheckpointerFactory.create",
            new_callable=AsyncMock,
            side_effect=_fake_create,
        ),
        patch(
            "jiuwenclaw.agentserver.deep_agent.interface_deep.PersistenceCheckpointerProvider",
        ),
    ):
        adapter = JiuWenClawDeepAdapter(
            workspace_dir=str(office_ws / "agent" / "jiuwenclaw_workspace"),
            agent_id="office_default",
            service_id="default",
            env_agent_id="office",
            env_service_id="default",
        )
        await adapter.set_checkpoint()

    assert len(created_confs) == 1
    assert "agent_office" in created_confs[0]["db_path"]
    assert "agent_office_default" not in created_confs[0]["db_path"]


def test_legacy_migration_skips_mkdir_without_sources(tmp_path: Path, monkeypatch):
    import jiuwenclaw.utils as utils_mod

    monkeypatch.setattr(utils_mod, "get_user_workspace_dir", lambda: tmp_path)
    monkeypatch.setattr(
        utils_mod,
        "get_service_root_dir",
        lambda service_id="default": tmp_path / "service_default",
    )
    monkeypatch.setattr(
        utils_mod,
        "get_multi_tenant_user_workspace_dir",
        lambda service_id, agent_id: tmp_path / f"service_{service_id}" / f"agent_{agent_id}",
    )
    monkeypatch.setattr(
        utils_mod,
        "get_agent_root_dir",
        lambda: tmp_path / "service_default" / "agent_default" / "agent",
    )

    utils_mod._legacy_migration_done = False
    utils_mod._migrate_legacy_checkpoint_and_logs()

    assert not (tmp_path / "service_default" / "agent_default").exists()
    assert (tmp_path / "service_default").exists()
