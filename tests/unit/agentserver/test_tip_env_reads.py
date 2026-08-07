# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Tip-aware reads for browser timeout policy and SSL verify patch."""

from __future__ import annotations

import importlib.util
import os
from pathlib import Path

import pytest

from jiuwenclaw.browser_timeout_policy import (
    allow_short_timeout_override,
    resolve_browser_task_timeout,
)
from jiuwenclaw.local_env_config import (
    reset_local_env_state_for_tests,
    set_os_environ,
)


@pytest.fixture(autouse=True)
def _reset_env_state() -> None:
    saved = dict(os.environ)
    reset_local_env_state_for_tests()
    yield
    reset_local_env_state_for_tests()
    os.environ.clear()
    os.environ.update(saved)


def _load_ollama_embedding_module():
    path = (
        Path(__file__).resolve().parents[3]
        / "jiuwenclaw"
        / "agentserver"
        / "tools"
        / "browser-move"
        / "src"
        / "openjiuwen_patch_sources"
        / "openjiuwen"
        / "core"
        / "retrieval"
        / "embedding"
        / "ollama_embedding.py"
    )
    spec = importlib.util.spec_from_file_location("ollama_embedding_tip_test", path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def test_allow_short_timeout_override_reads_tip_not_os_environ():
    os.environ.pop("BROWSER_ALLOW_SHORT_TIMEOUT_OVERRIDE", None)
    set_os_environ("BROWSER_ALLOW_SHORT_TIMEOUT_OVERRIDE", "true")
    assert "BROWSER_ALLOW_SHORT_TIMEOUT_OVERRIDE" not in os.environ
    assert allow_short_timeout_override() is True
    assert resolve_browser_task_timeout(10, default_timeout_s=180) == 10


def test_allow_short_timeout_override_unset_enforces_minimum():
    assert allow_short_timeout_override() is False
    assert resolve_browser_task_timeout(10, default_timeout_s=180) == 180


def test_ollama_ssl_verify_reads_tip_not_os_environ():
    os.environ.pop("JIUWENCLAW_SSL_VERIFY", None)
    set_os_environ("JIUWENCLAW_SSL_VERIFY", "false")
    assert "JIUWENCLAW_SSL_VERIFY" not in os.environ
    mod = _load_ollama_embedding_module()
    assert mod._ssl_verify() is False
