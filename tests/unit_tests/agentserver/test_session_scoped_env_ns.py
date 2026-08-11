# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

from jiuwenswarm.common.local_env_config import (
    bind_agent_env_ns,
    effective_tip,
    get_local_config,
    replace_active_env,
    reset_agent_env_ns,
)
from jiuwenswarm.server.runtime.agent_adapter.interface_deep import JiuWenSwarmDeepAdapter


def test_new_session_scoped_adapter_inherits_tenant_env_namespace() -> None:
    parent = object.__new__(JiuWenSwarmDeepAdapter)
    parent._env_agent_id = "office"
    parent._env_service_id = "default"
    parent._skill_manager = None
    parent._session_adapters = {}
    parent._session_adapter_locks = {}

    child = parent._new_session_scoped_adapter("officeclaw_sess_test")

    assert child._is_session_scoped_adapter is True
    assert child._parent_session_id == "officeclaw_sess_test"
    assert child._env_agent_id == "office"
    assert child._env_service_id == "default"


def test_bound_office_tip_exposes_synced_model_name_not_placeholder() -> None:
    """create_instance must bind office tip so MODEL_NAME is glm-5.1, not .env placeholder."""
    replace_active_env(
        {
            "MODEL_NAME": "glm-5.1",
            "MODEL_PROVIDER": "OpenAI",
            "API_KEY": "test-key",
            "API_BASE": "https://example.test/v1",
        },
        service_id="default",
        agent_id="office",
        clear_staged=True,
    )
    token = bind_agent_env_ns("default", "office")
    try:
        tip = effective_tip()
        assert tip.get("MODEL_NAME") == "glm-5.1"
        assert get_local_config("MODEL_NAME") == "glm-5.1"
        assert get_local_config("API_BASE") == "https://example.test/v1"
    finally:
        reset_agent_env_ns(token)
        replace_active_env({}, service_id="default", agent_id="office", clear_staged=True)
