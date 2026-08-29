# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for overlay-aware ``get_config`` caching."""

from __future__ import annotations

from typing import Any

import pytest

import jiuwenswarm.common.config as config_mod
from jiuwenswarm.common.local_env_config import (
    bind_agent_env_ns,
    bind_task_env_overlay,
    reset_agent_env_ns,
    reset_task_env_overlay,
)


@pytest.fixture(autouse=True)
def _clear_resolved_config_cache():
    config_mod.clear_config_cache()
    yield
    config_mod.clear_config_cache()


def _patch_merged_config(
    monkeypatch: pytest.MonkeyPatch,
    *,
    counter: list[int],
    base: dict[str, Any] | None = None,
) -> None:
    payload = base if base is not None else {
        "models": {
            "default": {
                "model_client_config": {
                    "model_name": "${MODEL_NAME:-fallback-model}",
                    "api_key": "${API_KEY:-}",
                }
            }
        }
    }

    def _merged() -> dict[str, Any]:
        counter[0] += 1
        return {
            "models": {
                "default": {
                    "model_client_config": dict(
                        payload["models"]["default"]["model_client_config"]
                    )
                }
            }
        }

    monkeypatch.setattr(config_mod, "get_merged_config_dict", _merged)


def test_get_config_reuses_cache_for_same_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[int] = [0]
    _patch_merged_config(monkeypatch, counter=loads)

    ns_token = bind_agent_env_ns("svc-a", "agent-a")
    overlay_token = bind_task_env_overlay({"MODEL_NAME": "model-x", "API_KEY": "k1"})
    try:
        first = config_mod.get_config()
        second = config_mod.get_config()
    finally:
        reset_task_env_overlay(overlay_token)
        reset_agent_env_ns(ns_token)

    assert loads[0] == 1
    assert first is second
    assert (
        first["models"]["default"]["model_client_config"]["model_name"] == "model-x"
    )


def test_get_config_separates_different_overlay_content(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[int] = [0]
    _patch_merged_config(monkeypatch, counter=loads)

    ns_token = bind_agent_env_ns("svc-a", "agent-a")
    try:
        token_a = bind_task_env_overlay({"MODEL_NAME": "model-a"})
        try:
            cfg_a = config_mod.get_config()
        finally:
            reset_task_env_overlay(token_a)

        token_b = bind_task_env_overlay({"MODEL_NAME": "model-b"})
        try:
            cfg_b = config_mod.get_config()
        finally:
            reset_task_env_overlay(token_b)
    finally:
        reset_agent_env_ns(ns_token)

    assert loads[0] == 2
    assert (
        cfg_a["models"]["default"]["model_client_config"]["model_name"] == "model-a"
    )
    assert (
        cfg_b["models"]["default"]["model_client_config"]["model_name"] == "model-b"
    )


def test_clear_config_cache_invalidates_overlay_keyed_entries(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[int] = [0]
    _patch_merged_config(monkeypatch, counter=loads)

    ns_token = bind_agent_env_ns("svc-a", "agent-a")
    overlay_token = bind_task_env_overlay({"MODEL_NAME": "model-x"})
    try:
        config_mod.get_config()
        assert loads[0] == 1
        config_mod.clear_config_cache(service_id="svc-a", agent_id="agent-a")
        config_mod.get_config()
        assert loads[0] == 2
    finally:
        reset_task_env_overlay(overlay_token)
        reset_agent_env_ns(ns_token)


def test_get_config_caches_without_overlay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    loads: list[int] = [0]
    _patch_merged_config(monkeypatch, counter=loads)

    ns_token = bind_agent_env_ns("svc-b", "agent-b")
    try:
        first = config_mod.get_config()
        second = config_mod.get_config()
    finally:
        reset_agent_env_ns(ns_token)

    assert loads[0] == 1
    assert first is second
    assert (
        first["models"]["default"]["model_client_config"]["model_name"]
        == "fallback-model"
    )


def test_overlay_cache_key_stable_for_same_content() -> None:
    ns = ("svc", "agent")
    key1 = config_mod._overlay_cache_key(ns, {"B": "2", "A": "1"})
    key2 = config_mod._overlay_cache_key(ns, {"A": "1", "B": "2"})
    key3 = config_mod._overlay_cache_key(ns, {"A": "1", "B": "3"})
    assert key1 == key2
    assert key1 != key3
    assert config_mod._overlay_cache_key(ns, None) == ns
