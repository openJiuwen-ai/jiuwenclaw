# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""网关 Redis 运行时：无 redis 包 / 无 Redis 服务时的降级行为。"""

from __future__ import annotations

import builtins

import pytest

_real_import = builtins.__import__


def _block_redis_import(
    name: str,
    global_ns=None,
    local_ns=None,
    fromlist=(),
    level=0,
):
    if name == "redis" or (isinstance(name, str) and name.startswith("redis.")):
        raise ModuleNotFoundError(f"No module named '{name}' (blocked for test)")
    return _real_import(name, global_ns, local_ns, fromlist, level)


@pytest.fixture
def restore_import():
    builtins.__import__ = _real_import
    yield
    builtins.__import__ = _real_import


@pytest.mark.asyncio
async def test_standalone_skips_redis(restore_import):
    from jiuwenclaw.extensions.redis import (
        get_declared_deployment_mode,
        get_effective_distributed_redis_active,
        init_gateway_redis_from_config,
    )

    builtins.__import__ = _block_redis_import

    await init_gateway_redis_from_config({"gateway": {"deployment_mode": "standalone"}})
    assert get_declared_deployment_mode() == "standalone"
    assert not get_effective_distributed_redis_active()


@pytest.mark.asyncio
async def test_active_standby_without_redis_package_degrades(restore_import):
    from jiuwenclaw.extensions.redis import (
        get_declared_deployment_mode,
        get_effective_distributed_redis_active,
        init_gateway_redis_from_config,
    )

    builtins.__import__ = _block_redis_import

    await init_gateway_redis_from_config({
        "gateway": {"deployment_mode": "active-standby"},
        "redis": {"host": "127.0.0.1", "port": 6379},
    })
    assert get_declared_deployment_mode() == "active-standby"
    assert not get_effective_distributed_redis_active()


@pytest.mark.asyncio
async def test_active_standby_unreachable_server_degrades(restore_import):
    from jiuwenclaw.extensions.redis import (
        get_declared_deployment_mode,
        get_effective_distributed_redis_active,
        init_gateway_redis_from_config,
    )

    await init_gateway_redis_from_config({
        "gateway": {"deployment_mode": "active-standby"},
        "redis": {"host": "127.0.0.1", "port": 6379},
    })
    assert get_declared_deployment_mode() == "active-standby"
    assert not get_effective_distributed_redis_active()
