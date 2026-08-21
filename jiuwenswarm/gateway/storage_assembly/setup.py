# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""按 ``gateway.edition`` 装配 StorageContext。"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Any

from jiuwenswarm.gateway.edition import resolve_gateway_edition
from jiuwenswarm.gateway.storage.backends.db.persistent_store import DbPersistentBackend
from jiuwenswarm.gateway.storage.backends.file_persistent import FilePersistentBackend
from jiuwenswarm.gateway.storage.backends.memory_ephemeral import MemoryEphemeralBackend
from jiuwenswarm.gateway.storage.backends.redis_ephemeral import RedisEphemeralBackend
from jiuwenswarm.gateway.storage.context import StorageContext
from jiuwenswarm.gateway.storage.errors import StorageUnavailableError
from jiuwenswarm.gateway.storage.protocols.ephemeral import EphemeralStore
from jiuwenswarm.gateway.storage.protocols.persistent import PersistentStore
from jiuwenswarm.gateway.storage_assembly.db_connection import assert_replicas_db_compat
from jiuwenswarm.gateway.storage_assembly.layouts import build_gateway_store_registry

logger = logging.getLogger(__name__)


def _on_config_written(path: Path) -> None:
    try:
        from jiuwenswarm.common.config import clear_config_cache
        from jiuwenswarm.common.utils import get_config_file

        if path.resolve() == get_config_file().resolve():
            clear_config_cache()
    except Exception as exc:
        logger.warning("clear config cache after write failed: %s", exc)


def _redis_client() -> Any | None:
    try:
        from jiuwenswarm.extensions.redis.redis_runtime import get_gateway_redis_client

        client = get_gateway_redis_client()
    except Exception:
        client = None
    return client


def _create_persistent(edition: str) -> PersistentStore:
    if edition == "enterprise":
        from jiuwenswarm.gateway.storage_assembly.db_connection import GatewayDbConnection

        assert_replicas_db_compat()
        return DbPersistentBackend(
            GatewayDbConnection(),
            build_gateway_store_registry(),
        )

    from jiuwenswarm.common.utils import get_config_file, get_user_workspace_dir

    persistent_root = get_user_workspace_dir() / "gateway" / "persistent"
    config_file = get_config_file()
    return FilePersistentBackend(
        registry=build_gateway_store_registry(
            persistent_root=persistent_root,
            config_file=config_file,
        ),
        on_write=_on_config_written,
    )


def _require_redis_client() -> Any:
    client = _redis_client()
    if client is None:
        raise StorageUnavailableError(
            "enterprise edition requires Redis for ephemeral storage"
        )
    return client


def _create_ephemeral_factory(edition: str):
    if edition != "enterprise":
        def memory_factory(namespace: str) -> EphemeralStore:
            return MemoryEphemeralBackend(namespace)

        return memory_factory

    client = _require_redis_client()

    def factory(namespace: str) -> EphemeralStore:
        return RedisEphemeralBackend(
            namespace,
            client=client,
            key_prefix="gw:",
        )

    return factory


def resolve_storage_instance_id(cfg: dict[str, Any] | None = None) -> str:
    """企业表 ``jiuwenclaw_id``：gateway.instance_id → 环境变量。"""
    if cfg:
        raw = (cfg.get("gateway") or {}).get("instance_id")
        if raw and str(raw).strip():
            return str(raw).strip()
    try:
        from jiuwenswarm.extensions.redis.redis_runtime import get_gateway_instance_id
    except ImportError:
        value = None
    else:
        value = get_gateway_instance_id()
    if value:
        return str(value).strip()
    return (
        os.getenv("GATEWAY_INSTANCE_ID", "").strip()
        or os.getenv("JIUWENCLAW_ID", "").strip()
    )


def is_storage_repositories_enabled(cfg: dict[str, Any] | None = None) -> bool:
    """业务写路径是否已切到 PersistentStore Repository。

    迁移期固定为 ``False``：企业版 / 单机版均继续走旧路径
    （``config.py`` / LocalSessionStorage / FileCronJobStore / EE DBHandler）。
    Repository 与 access 适配层可继续开发；启动时不注入，业务接口调用保持原样。
    """
    _ = cfg
    return False


def create_gateway_storage_context(cfg: dict[str, Any] | None = None) -> StorageContext:
    """按 ``gateway.edition`` 组装进程级 ``StorageContext``（持久化 + 临时态入口）。

    - 解析 edition（``personal`` / ``enterprise``）
    - 选择 Persistent backend：personal → 文件；enterprise → DB
    - 选择 Ephemeral 工厂：personal → 内存；enterprise → Redis

    返回的 Context 提供 ``persistent()`` / ``ephemeral(ns)``。
    不创建 channel / logging 等 Repository（由 ``create_*_repository`` 负责）。
    """
    if cfg is None:
        from jiuwenswarm.common.config import get_config

        cfg = get_config()
    edition = resolve_gateway_edition(cfg)
    return StorageContext(
        _create_persistent(edition),
        ephemeral_factory=_create_ephemeral_factory(edition),
    )


def create_channel_config_repository(
    store: PersistentStore,
    edition: str,
    *,
    instance_id: str = "",
):
    """按 edition 选 Codec，业务侧拿到的是同一个 Repository。"""
    from jiuwenswarm.gateway.config.channel import (
        ChannelConfigRepository,
        DbRowChannelCodec,
        YamlMapChannelCodec,
    )
    from jiuwenswarm.gateway.edition import EDITION_ENTERPRISE

    if edition == EDITION_ENTERPRISE:
        codec = DbRowChannelCodec(instance_id=instance_id)
    else:
        codec = YamlMapChannelCodec()
    return ChannelConfigRepository(store, codec)


def create_session_map_repository(store: PersistentStore):
    from jiuwenswarm.gateway.routing.session_map_repository import SessionMapRepository

    return SessionMapRepository(store)


def create_permissions_config_repository(
    store: PersistentStore,
    edition: str,
    *,
    instance_id: str = "",
):
    from jiuwenswarm.gateway.config.permissions import (
        DbBodySectionCodec,
        PermissionsConfigRepository,
        YamlSectionCodec,
    )
    from jiuwenswarm.gateway.edition import EDITION_ENTERPRISE

    codec = (
        DbBodySectionCodec()
        if edition == EDITION_ENTERPRISE
        else YamlSectionCodec()
    )
    return PermissionsConfigRepository(
        store, codec, instance_id=instance_id
    )


def create_logging_config_repository(
    store: PersistentStore,
    edition: str,
    *,
    instance_id: str = "",
):
    from jiuwenswarm.gateway.config.logging import (
        LoggingConfigRepository,
        YamlSectionCodec,
        db_logging_codec,
    )
    from jiuwenswarm.gateway.edition import EDITION_ENTERPRISE

    codec = (
        db_logging_codec()
        if edition == EDITION_ENTERPRISE
        else YamlSectionCodec()
    )
    return LoggingConfigRepository(store, codec, instance_id=instance_id)


def create_memory_config_repository(
    store: PersistentStore,
    edition: str,
    *,
    instance_id: str = "",
):
    from jiuwenswarm.gateway.config.memory import (
        DbBodySectionCodec,
        MemoryConfigRepository,
        YamlSectionCodec,
    )
    from jiuwenswarm.gateway.edition import EDITION_ENTERPRISE

    codec = (
        DbBodySectionCodec()
        if edition == EDITION_ENTERPRISE
        else YamlSectionCodec()
    )
    return MemoryConfigRepository(store, codec, instance_id=instance_id)


def create_config_record_repository(
    store: PersistentStore,
    store_name: str,
    *,
    instance_id: str = "",
):
    """企业专属表通用 Repository（仅 DB；不注入则 EE 仍走 DBHandler）。"""
    from jiuwenswarm.gateway.config.enterprise import ConfigRecordRepository

    return ConfigRecordRepository(
        store,
        store_name,
        instance_id=instance_id,
    )


def create_enterprise_config_record_repositories(
    store: PersistentStore,
    *,
    instance_id: str = "",
) -> dict[str, Any]:
    """为全部企业专属 store name 创建 ``ConfigRecordRepository``。

    返回 ``{store_name: repo}``；迁移期不调用 ``set_config_record_repositories``，
    业务仍走 EE ``DBHandler``。
    """
    from jiuwenswarm.gateway.config.enterprise.catalog import (
        ENTERPRISE_RECORD_STORE_NAMES,
    )

    return {
        name: create_config_record_repository(
            store, name, instance_id=instance_id
        )
        for name in ENTERPRISE_RECORD_STORE_NAMES
    }


__all__ = [
    "create_channel_config_repository",
    "create_config_record_repository",
    "create_enterprise_config_record_repositories",
    "create_gateway_storage_context",
    "create_logging_config_repository",
    "create_memory_config_repository",
    "create_permissions_config_repository",
    "create_session_map_repository",
    "is_storage_repositories_enabled",
    "resolve_storage_instance_id",
]
