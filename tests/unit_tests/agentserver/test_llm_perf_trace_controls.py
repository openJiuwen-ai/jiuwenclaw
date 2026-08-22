# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Regression tests for low-overhead model timing diagnostics."""

from __future__ import annotations

import logging
from types import SimpleNamespace

from jiuwenswarm.server.runtime.agent_adapter import interface_deep
from jiuwenswarm.server.runtime.agent_adapter import llm_io_trace


def test_full_llm_io_trace_requires_explicit_opt_in(monkeypatch) -> None:
    monkeypatch.setattr(llm_io_trace.logger, "isEnabledFor", lambda level: level == logging.DEBUG)
    monkeypatch.delenv("JIUWENSWARM_LLM_IO_TRACE", raising=False)
    assert llm_io_trace._llm_trace_active() is False

    monkeypatch.setenv("JIUWENSWARM_LLM_IO_TRACE", "true")
    assert llm_io_trace._llm_trace_active() is True


def test_model_perf_timing_flag_is_independent_from_full_body_trace(monkeypatch) -> None:
    monkeypatch.setenv("JIUWEN_PERF_TIMING_LOG", "1")
    monkeypatch.delenv("JIUWENSWARM_LLM_IO_TRACE", raising=False)
    assert interface_deep._perf_timing_log_enabled() is True
    assert llm_io_trace._llm_trace_active() is False


def test_visible_model_token_accepts_reasoning_and_answer_text() -> None:
    assert interface_deep._chunk_has_visible_model_token({"reasoning_content": "想"})
    assert interface_deep._chunk_has_visible_model_token({"payload": {"content": "你"}})
    assert interface_deep._chunk_has_visible_model_token(SimpleNamespace(reasoning="考"))
    assert not interface_deep._chunk_has_visible_model_token({"content": ""})
    assert not interface_deep._chunk_has_visible_model_token({"payload": {"status": "started"}})


def test_stable_config_fast_clone_is_equal_and_independent() -> None:
    original = {
        "models": {"default": {"model_client_config": {"model_name": "glm-5.2"}}},
        "react": {"disabled_tools": ["git", "git_status"]},
    }

    cloned = interface_deep._clone_stable_config_base(original)

    assert cloned == original
    assert cloned is not original
    assert cloned["models"] is not original["models"]
    cloned["react"]["disabled_tools"].append("another")
    assert original["react"]["disabled_tools"] == ["git", "git_status"]


def test_stable_config_clone_preserves_non_json_types_via_fallback() -> None:
    original = {"custom": ("keep", "tuple")}

    cloned = interface_deep._clone_stable_config_base(original)

    assert cloned == original
    assert isinstance(cloned["custom"], tuple)
