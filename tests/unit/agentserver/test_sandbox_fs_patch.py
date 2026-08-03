# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.

"""Unit tests for sandbox FS concurrency patch in ``jiuwen_core_patch``."""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from jiuwenclaw import jiuwen_core_patch as patch_mod

pytest.importorskip(
    "openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox"
)


@pytest.fixture
def sandbox_fs_patch_state():
    """Save/restore global patch flag and upstream mixin method; shut down FS pool."""
    from openjiuwen.extensions.sys_operation.sandbox.providers.jiuwenbox import (
        _JiuwenBoxProviderMixin,
    )

    orig_flag = patch_mod._SANDBOX_FS_PATCHED
    orig_method = _JiuwenBoxProviderMixin._execute_with_sandbox_retry
    try:
        yield _JiuwenBoxProviderMixin
    finally:
        _JiuwenBoxProviderMixin._execute_with_sandbox_retry = orig_method
        patch_mod._SANDBOX_FS_PATCHED = orig_flag
        patch_mod._shutdown_sandbox_fs_executor()


@pytest.mark.asyncio
async def test_run_in_sandbox_fs_executor_completes(sandbox_fs_patch_state):
    result = await patch_mod._run_in_sandbox_fs_executor(lambda: 42)
    assert result == 42


def test_sandbox_fs_executor_worker_count(sandbox_fs_patch_state):
    patch_mod._shutdown_sandbox_fs_executor()
    cpu = __import__("os").cpu_count() or 1
    expected = min(32, max(8, cpu * 2 + 4))
    assert patch_mod._get_sandbox_fs_executor()._max_workers == expected


def test_patch_sandbox_fs_idempotent(sandbox_fs_patch_state):
    mixin = sandbox_fs_patch_state
    patch_mod._SANDBOX_FS_PATCHED = False
    patch_mod._patch_sandbox_fs()

    first = mixin._execute_with_sandbox_retry
    patch_mod._patch_sandbox_fs()
    assert mixin._execute_with_sandbox_retry is first
    assert patch_mod._SANDBOX_FS_PATCHED is True


@pytest.mark.asyncio
async def test_patched_execute_routes_through_fs_executor(
    monkeypatch, sandbox_fs_patch_state
):
    """Patched retry path must dispatch sync op via dedicated executor."""
    mixin = sandbox_fs_patch_state
    patch_mod._SANDBOX_FS_PATCHED = False
    patch_mod._patch_sandbox_fs()

    seen: dict[str, bool] = {"used": False}

    async def _tracking_run(fn):
        seen["used"] = True
        return fn()

    monkeypatch.setattr(patch_mod, "_run_in_sandbox_fs_executor", _tracking_run)

    class _Prov:
        config = SimpleNamespace(timeout_seconds=30)

        def _get_sandbox_id(self) -> str:
            return "sbx-test"

    out = await mixin._execute_with_sandbox_retry(
        _Prov(), lambda sid: f"ok:{sid}"
    )
    assert out == "ok:sbx-test"
    assert seen["used"] is True
