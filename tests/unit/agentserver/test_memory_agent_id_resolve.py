# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""memory_tools agent_id resolution: explicit > memory CV > env_ns > TypeError."""

from __future__ import annotations

import pytest

from jiuwenclaw.agentserver.tools.memory_tools import (
    _resolve_memory_agent_id,
    bind_memory_agent_id,
    clear_memory_agent_id_binding,
    get_bound_memory_agent_id,
    reset_memory_agent_id,
)
from jiuwenclaw.local_env_config import (
    bind_agent_env_ns,
    get_bound_agent_env_ns,
    reset_agent_env_ns,
)


@pytest.fixture(autouse=True)
def _clear_memory_agent_bindings():
    clear_memory_agent_id_binding()
    yield
    clear_memory_agent_id_binding()


def test_explicit_agent_id_wins_over_bindings() -> None:
    mem_tok = bind_memory_agent_id("from_memory_cv")
    ns_tok = bind_agent_env_ns("svc", "from_env_ns")
    try:
        assert _resolve_memory_agent_id("explicit") == "explicit"
    finally:
        reset_agent_env_ns(ns_tok)
        reset_memory_agent_id(mem_tok)


def test_bound_memory_agent_id_used_when_no_explicit() -> None:
    tok = bind_memory_agent_id("aid_office")
    try:
        assert get_bound_memory_agent_id() == "aid_office"
        assert _resolve_memory_agent_id() == "aid_office"
        assert _resolve_memory_agent_id(None) == "aid_office"
    finally:
        reset_memory_agent_id(tok)


def test_bound_env_ns_used_when_memory_cv_unbound() -> None:
    tok = bind_agent_env_ns("sid1", "aid1")
    try:
        assert get_bound_agent_env_ns() == ("sid1", "aid1")
        assert _resolve_memory_agent_id() == "aid1"
    finally:
        reset_agent_env_ns(tok)


def test_unbound_raises_type_error_no_silent_default() -> None:
    with pytest.raises(TypeError, match="memory agent_id is required"):
        _resolve_memory_agent_id()
    with pytest.raises(TypeError, match="memory agent_id is required"):
        _resolve_memory_agent_id(None)


def test_empty_explicit_agent_id_raises() -> None:
    with pytest.raises(TypeError, match="non-empty"):
        _resolve_memory_agent_id("  ")


def test_init_memory_manager_async_requires_keyword_agent_id() -> None:
    from jiuwenclaw.agentserver.tools import memory_tools

    with pytest.raises(TypeError):
        # positional agent_id must fail (keyword-only)
        memory_tools.init_memory_manager_async(".", "aid1")

    with pytest.raises(TypeError, match="agent_id"):
        memory_tools.init_memory_manager_async(".")
