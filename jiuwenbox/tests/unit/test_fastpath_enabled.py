# Copyright (c) Huawei Technologies Co., Ltd. 2025. All rights reserved.
"""FastPath default-ON enable semantics.

The fast path is now a *transparent optimisation*: it is ON by default and no
environment variable is required to benefit from it. ``JIUWENBOX_PYTHON_FASTPATH``
is now a tri-state opt-in/opt-out flag:

* unset  -> ON   (the user does nothing and still gets the fast path)
* ``"1"`` -> ON  (explicit enable, equivalent to the new default)
* ``"0"`` -> OFF (explicit opt-out)
* any other value -> OFF, fail-safe, with a warning

These tests pin the four cases and the fail-safe warning. They are pure
env-parsing tests (no fork, no worker), so they run on every platform.
"""

from __future__ import annotations

import logging
import os

import pytest

from jiuwenbox.supervisor import sandbox_daemon as sd

# 豁免 G.CLS.11 protected-access：本文件为 FastPath 默认开关语义的白盒单测，
# 需直接调用 sd._fastpath_enabled() 验证 env 解析后的启用判定；将其改名为
# public 会破坏封装语义，故仅在本测试侧豁免（不改产品符号可见性）。
# pylint: disable=protected-access

pytestmark = pytest.mark.unit


@pytest.fixture
def clean_fastpath_env(monkeypatch):
    """Remove the FastPath enable env so each test starts from a known state."""
    monkeypatch.delenv(sd.FASTPATH_ENV, raising=False)
    yield


def test_unset_means_enabled(clean_fastpath_env):
    # Default ON: no env var -> the user gets the fast path for free.
    assert os.environ.get(sd.FASTPATH_ENV) is None
    assert sd._fastpath_enabled() is True


def test_explicit_one_means_enabled(clean_fastpath_env, monkeypatch):
    monkeypatch.setenv(sd.FASTPATH_ENV, "1")
    assert sd._fastpath_enabled() is True


def test_explicit_zero_means_disabled(clean_fastpath_env, monkeypatch):
    monkeypatch.setenv(sd.FASTPATH_ENV, "0")
    assert sd._fastpath_enabled() is False


@pytest.mark.parametrize("bad", ["", "true", "false", "on", "off", "yes",
                                  "2", " 1 ", "enabled", "True"])
def test_unrecognised_value_is_fail_safe_off(clean_fastpath_env, monkeypatch,
                                             caplog, bad):
    # Fail-safe = OFF: a typo must not silently leave the fast path on when the
    # operator intended to disable it. A warning is emitted so it is visible.
    monkeypatch.setenv(sd.FASTPATH_ENV, bad)
    with caplog.at_level(logging.WARNING, logger="jiuwenbox.sandbox_daemon"):
        result = sd._fastpath_enabled()
    assert result is False
    assert any(sd.FASTPATH_ENV in rec.message and "OFF" in rec.message
               for rec in caplog.records)


def test_disabled_path_does_not_touch_pool(clean_fastpath_env, monkeypatch):
    # When disabled, main() never calls _FORK_POOL.start_reaper(); the request
    # handler never calls _try_fastpath_exec. The OFF path is the unchanged
    # subprocess.Popen path. We assert the routing predicate is False so the
    # handler falls through unconditionally.
    monkeypatch.setenv(sd.FASTPATH_ENV, "0")
    assert sd._fastpath_enabled() is False
    # The handler gate is ``header.get("python_fastpath") and _fastpath_enabled()``;
    # with enabled False the gate is False regardless of the marker.
    assert (True and sd._fastpath_enabled()) is False


def test_default_on_routes_marked_candidates(clean_fastpath_env):
    # Default ON: a server-marked candidate reaches the fast path gate.
    assert sd._fastpath_enabled() is True
    assert (True and sd._fastpath_enabled()) is True
