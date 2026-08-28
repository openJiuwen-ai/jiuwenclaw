# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Manager WS 显式 Repository 入口（须在 Gateway 启动时装配）。"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from jiuwenswarm.gateway.config.channel.repository import ChannelConfigRepository
    from jiuwenswarm.gateway.config.enterprise.repository import EnterpriseRecordRepository
    from jiuwenswarm.gateway.config.logging.repository import LoggingConfigRepository
    from jiuwenswarm.gateway.config.memory.repository import MemoryConfigRepository
    from jiuwenswarm.gateway.config.permissions.repository import PermissionsConfigRepository


def require_enterprise_repository(store_name: str) -> EnterpriseRecordRepository:
    from jiuwenswarm.gateway.config.enterprise.access import (
        get_enterprise_record_repository,
    )
    from jiuwenswarm.gateway.config.enterprise.repository import (
        EnterpriseRecordRepository,
    )

    repo = get_enterprise_record_repository(store_name)
    if repo is None:
        raise RuntimeError(
            f"EnterpriseRecordRepository {store_name!r} is not wired; "
            "ensure setup_gateway_storage_repositories / "
            "wire_enterprise_manager_ws_store_async ran at Gateway startup"
        )
    return repo


def require_channel_repository() -> ChannelConfigRepository:
    from jiuwenswarm.gateway.config.channel.access import get_channel_config_repository
    from jiuwenswarm.gateway.config.channel.repository import ChannelConfigRepository

    repo = get_channel_config_repository()
    if repo is None:
        raise RuntimeError(
            "ChannelConfigRepository is not wired; "
            "ensure gateway.storage.repositories is enabled at startup"
        )
    return repo


def require_permissions_repository() -> PermissionsConfigRepository:
    from jiuwenswarm.gateway.config.permissions.access import (
        get_permissions_config_repository,
    )
    from jiuwenswarm.gateway.config.permissions.repository import (
        PermissionsConfigRepository,
    )

    repo = get_permissions_config_repository()
    if repo is None:
        raise RuntimeError("PermissionsConfigRepository is not wired")
    return repo


def require_logging_repository() -> LoggingConfigRepository:
    from jiuwenswarm.gateway.config.logging.access import get_logging_config_repository
    from jiuwenswarm.gateway.config.logging.repository import LoggingConfigRepository

    repo = get_logging_config_repository()
    if repo is None:
        raise RuntimeError("LoggingConfigRepository is not wired")
    return repo


def require_memory_repository() -> MemoryConfigRepository:
    from jiuwenswarm.gateway.config.memory.access import get_memory_config_repository
    from jiuwenswarm.gateway.config.memory.repository import MemoryConfigRepository

    repo = get_memory_config_repository()
    if repo is None:
        raise RuntimeError("MemoryConfigRepository is not wired")
    return repo


__all__ = [
    "require_channel_repository",
    "require_enterprise_repository",
    "require_logging_repository",
    "require_memory_repository",
    "require_permissions_repository",
]
