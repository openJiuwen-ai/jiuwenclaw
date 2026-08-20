# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
from unittest.mock import AsyncMock, MagicMock

import pytest

from jiuwenclaw.agentserver.agent_manager import AgentManager
from jiuwenclaw.agentserver.reload_result import ReloadResult
from jiuwenclaw.agentserver.tenant_agent_pool import TenantAgentPool
from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    bind_task_env_overlay,
    clear_staged_env,
    get_local_config,
    reset_task_env_overlay,
)


@pytest.fixture(autouse=True)
def _reset_env_state():
    saved_environ = dict(os.environ)
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    TenantAgentPool.reset_instance()
    yield
    ENV_CONFIG_DICT.clear()
    clear_staged_env()
    TenantAgentPool.reset_instance()
    os.environ.clear()
    os.environ.update(saved_environ)


@pytest.mark.asyncio
async def test_office_reload_does_not_pollute_assistant_get_local_config():
    """office reload 后 assistant overlay 下 get_local_config 读不到 office 独有键。"""
    TenantAgentPool.reset_instance()
    pool = TenantAgentPool.get_instance()

    office_manager = AgentManager(agent_id="office", service_id="default")
    office_manager._latest_env_overrides = {"MODEL_NAME": "office-model"}
    assistant_manager = AgentManager(agent_id="assistant", service_id="default")
    assistant_manager._latest_env_overrides = {"MODEL_NAME": "assistant-model"}

    async def _fake_get(key):
        if key == ("office", "default"):
            return office_manager
        if key == ("assistant", "default"):
            return assistant_manager
        return None

    pool._agent_wrappers.get = _fake_get

    await pool.reload_tenant_config(
        "office",
        "default",
        {},
        {"MODEL_NAME": "office-hot-reload"},
    )

    assert assistant_manager._latest_env_overrides["MODEL_NAME"] == "assistant-model"
    assert office_manager._latest_env_overrides["MODEL_NAME"] == "office-hot-reload"
    assert "MODEL_NAME" not in ENV_CONFIG_DICT

    token = bind_task_env_overlay(dict(assistant_manager._latest_env_overrides))
    try:
        assert get_local_config("MODEL_NAME") == "assistant-model"
    finally:
        reset_task_env_overlay(token)

    ENV_CONFIG_DICT["MODEL_NAME"] = "global-pollution"
    token = bind_task_env_overlay(dict(assistant_manager._latest_env_overrides))
    try:
        assert get_local_config("MODEL_NAME") == "assistant-model"
    finally:
        reset_task_env_overlay(token)


@pytest.mark.asyncio
async def test_assistant_overlay_ignores_global_after_office_reload():
    office_manager = AgentManager(agent_id="office", service_id="default")
    mock_agent = MagicMock()
    mock_agent.reload_agent_config = AsyncMock(return_value=ReloadResult(applied=True))
    office_manager.agents["default"] = {"agent": {"sess": mock_agent}}

    await office_manager.reload_agents_config({}, {"OFFICE_ONLY": "secret"})

    assistant_manager = AgentManager(agent_id="assistant", service_id="default")
    assistant_manager._latest_env_overrides = {}

    token = bind_task_env_overlay(dict(assistant_manager._latest_env_overrides))
    try:
        assert get_local_config("OFFICE_ONLY") is None
    finally:
        reset_task_env_overlay(token)
