# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

from __future__ import annotations

import os
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from jiuwenclaw.jiuwen_core_patch import _patched_build_request_params
from jiuwenclaw.local_env_config import (
    ENV_CONFIG_DICT,
    apply_env_overrides_to_active,
    bind_task_env_overlay,
    clear_staged_env,
    reset_local_env_state_for_tests,
    reset_task_env_overlay,
    set_os_environ,
    stage_env_overrides,
)
from jiuwenclaw.tool_calling_guard import is_tool_calling_guard_enabled, resolve_tool_calling_guard


def _guard_config(*, enabled: bool = False, limited_models: list[str] | None = None) -> dict:
    if limited_models is None:
        limited_models = ["qwen3-32b", "qwen3-30b-a3b"]
    return {
        "react": {
            "tool_calling_guard": {
                "enabled": enabled,
                "limited_models": limited_models,
            }
        }
    }


@pytest.fixture(autouse=True)
def _reset_env_state() -> None:
    saved = dict(os.environ)
    reset_local_env_state_for_tests()
    yield
    reset_local_env_state_for_tests()
    os.environ.clear()
    os.environ.update(saved)


@pytest.fixture
def mock_config(monkeypatch: pytest.MonkeyPatch):
    def _set(config: dict) -> None:
        monkeypatch.setattr("jiuwenclaw.tool_calling_guard.get_config", lambda: config)

    return _set


@pytest.mark.parametrize(
    ("env_value", "expected"),
    [
        pytest.param("", False, id="empty-string"),
        pytest.param("false", False, id="false"),
        pytest.param("0", False, id="zero"),
        pytest.param("no", False, id="no"),
        pytest.param("off", False, id="off"),
        pytest.param("true", True, id="true"),
        pytest.param("maybe", True, id="nonstandard-maybe"),
        pytest.param("garbage", True, id="nonstandard-garbage"),
        pytest.param("flase", True, id="typo-flase"),
    ],
)
def test_is_tool_calling_guard_enabled_env_parsing(
    mock_config,
    env_value: str,
    expected: bool,
) -> None:
    """Document _to_bool fallback: only explicit falsy strings disable the guard."""
    mock_config(_guard_config(enabled=True))
    set_os_environ("TOOL_CALLING_GUARD_ENABLED", env_value)
    assert is_tool_calling_guard_enabled() is expected


@pytest.mark.parametrize(
    ("guard_enabled", "disable_env", "model_name", "expect_strip"),
    [
        pytest.param(False, "1", "qwen3-32b", False, id="T1-guard-off-env-on"),
        pytest.param(False, None, "qwen3-32b", False, id="T2-guard-off-env-unset"),
        pytest.param(True, "1", "any-model", True, id="T3-guard-on-env-on"),
        pytest.param(True, "0", "qwen3-32b", False, id="T4-guard-on-env-off-escape"),
        pytest.param(True, None, "qwen3-32b", True, id="T5-guard-on-model-limited"),
        pytest.param(True, None, "deepseek-v3", False, id="T6-guard-on-model-not-limited"),
    ],
)
def test_resolve_tool_calling_guard_matrix(
    mock_config,
    guard_enabled: bool,
    disable_env: str | None,
    model_name: str,
    expect_strip: bool,
) -> None:
    mock_config(_guard_config(enabled=guard_enabled))
    if guard_enabled:
        set_os_environ("TOOL_CALLING_GUARD_ENABLED", "true")
    if disable_env is not None:
        set_os_environ("TOOL_CALLING_GUARD_DISABLE", disable_env)
    set_os_environ("MODEL_NAME", model_name)

    decision = resolve_tool_calling_guard()
    assert decision.strip_tools is expect_strip


def test_t3_env_override_reason(mock_config) -> None:
    mock_config(_guard_config(enabled=True))
    set_os_environ("TOOL_CALLING_GUARD_ENABLED", "true")
    set_os_environ("TOOL_CALLING_GUARD_DISABLE", "true")
    set_os_environ(
        "TOOL_CALLING_GUARD_STRIP_REASON",
        "huawei_maas_model_without_function_call",
    )

    decision = resolve_tool_calling_guard()
    assert decision.strip_tools is True
    assert decision.reason == "huawei_maas_model_without_function_call"


def test_t7_guard_hot_reload_off_via_staged_env(mock_config) -> None:
    mock_config(_guard_config(enabled=True))
    set_os_environ("MODEL_NAME", "qwen3-32b")
    set_os_environ("TOOL_CALLING_GUARD_ENABLED", "true")

    assert resolve_tool_calling_guard().strip_tools is True

    stage_env_overrides({"TOOL_CALLING_GUARD_ENABLED": "false"})
    assert resolve_tool_calling_guard().strip_tools is False


def test_guard_enabled_staged_true_overrides_config_false(mock_config) -> None:
    mock_config(_guard_config(enabled=False))
    stage_env_overrides({"TOOL_CALLING_GUARD_ENABLED": "true"})
    assert is_tool_calling_guard_enabled() is True


def test_guard_enabled_staged_false_overrides_active_true(mock_config) -> None:
    mock_config(_guard_config(enabled=True))
    ENV_CONFIG_DICT["TOOL_CALLING_GUARD_ENABLED"] = "true"
    stage_env_overrides({"TOOL_CALLING_GUARD_ENABLED": "false"})
    assert is_tool_calling_guard_enabled() is False


def test_guard_enabled_falls_back_to_config_when_unset(mock_config) -> None:
    mock_config(_guard_config(enabled=True))
    assert is_tool_calling_guard_enabled() is True


def test_guard_enabled_overlay_empty_string_disables(mock_config) -> None:
    mock_config(_guard_config(enabled=True))
    token = bind_task_env_overlay({"TOOL_CALLING_GUARD_ENABLED": ""})
    try:
        assert is_tool_calling_guard_enabled() is False
    finally:
        reset_task_env_overlay(token)


def test_t8_reload_removes_limited_models(mock_config) -> None:
    mock_config(_guard_config(enabled=True, limited_models=["qwen3-32b"]))
    set_os_environ("TOOL_CALLING_GUARD_ENABLED", "true")
    set_os_environ("MODEL_NAME", "qwen3-32b")

    assert resolve_tool_calling_guard().strip_tools is True

    mock_config(_guard_config(enabled=True, limited_models=[]))
    assert resolve_tool_calling_guard().strip_tools is False


def test_patched_build_request_params_strips_tools(monkeypatch: pytest.MonkeyPatch) -> None:
    set_os_environ("TOOL_CALLING_GUARD_ENABLED", "true")
    set_os_environ("TOOL_CALLING_GUARD_DISABLE", "1")
    monkeypatch.setattr(
        "jiuwenclaw.jiuwen_core_patch._ORIGINAL_BUILD_REQUEST_PARAMS",
        lambda self, *, stream, **kwargs: {
            "tools": [{"type": "function"}],
            "tool_choice": "auto",
            "messages": [],
        },
    )

    params = _patched_build_request_params(SimpleNamespace(), stream=False)
    assert "tools" not in params
    assert "tool_choice" not in params


def test_patched_build_request_params_keeps_tools_when_guard_off(
    monkeypatch: pytest.MonkeyPatch,
    mock_config,
) -> None:
    mock_config(_guard_config(enabled=False))
    set_os_environ("TOOL_CALLING_GUARD_DISABLE", "1")
    set_os_environ("MODEL_NAME", "qwen3-32b")
    monkeypatch.setattr(
        "jiuwenclaw.jiuwen_core_patch._ORIGINAL_BUILD_REQUEST_PARAMS",
        lambda self, *, stream, **kwargs: {
            "tools": [{"type": "function"}],
            "tool_choice": "auto",
            "messages": [],
        },
    )

    params = _patched_build_request_params(SimpleNamespace(), stream=False)
    assert "tools" in params
    assert params["tool_choice"] == "auto"


def test_patched_build_request_params_logs_reason(monkeypatch: pytest.MonkeyPatch) -> None:
    set_os_environ("TOOL_CALLING_GUARD_ENABLED", "true")
    set_os_environ("TOOL_CALLING_GUARD_DISABLE", "1")
    set_os_environ("TOOL_CALLING_GUARD_STRIP_REASON", "test_reason")
    monkeypatch.setattr(
        "jiuwenclaw.jiuwen_core_patch._ORIGINAL_BUILD_REQUEST_PARAMS",
        lambda self, *, stream, **kwargs: {"tools": [], "tool_choice": "auto", "messages": []},
    )
    debug_mock = MagicMock()
    monkeypatch.setattr("jiuwenclaw.jiuwen_core_patch.llm_logger.debug", debug_mock)

    _patched_build_request_params(SimpleNamespace(), stream=False)

    debug_mock.assert_called_once_with(
        "[tool_calling_guard] stripped tools tool_choice reason=%s",
        "test_reason",
    )
