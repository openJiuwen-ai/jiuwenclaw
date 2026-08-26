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

    from jiuwenswarm.common.utils import (
        get_checkpoint_dir,
        get_config_file,
        get_user_workspace_dir,
        resolve_gateway_cron_jobs_path_template,
    )

    persistent_root = get_user_workspace_dir() / "gateway" / "persistent"
    config_file = get_config_file()
    session_map_file = get_checkpoint_dir() / "session_map.json"
    return FilePersistentBackend(
        registry=build_gateway_store_registry(
            persistent_root=persistent_root,
            config_file=config_file,
            session_map_file=session_map_file,
            cron_jobs_path_template=resolve_gateway_cron_jobs_path_template(),
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


def _storage_flag(
    cfg: dict[str, Any] | None,
    *,
    config_key: str,
    env_key: str,
    default: bool = True,
) -> bool:
    if cfg is None:
        from jiuwenswarm.common.config import get_config

        cfg = get_config()
    storage = (cfg.get("gateway") or {}).get("storage") or {}
    if config_key in storage:
        return bool(storage[config_key])
    env = os.getenv(env_key, "").strip().lower()
    if env in ("0", "false", "no", "off"):
        return False
    if env in ("1", "true", "yes", "on"):
        return True
    return default


def is_storage_repositories_enabled(cfg: dict[str, Any] | None = None) -> bool:
    """channel / permissions / logging / memory / cron 是否切到 PersistentStore。"""
    return _storage_flag(
        cfg,
        config_key="repositories",
        env_key="GATEWAY_STORAGE_REPOSITORIES",
        default=True,
    )


def is_session_map_repository_enabled(cfg: dict[str, Any] | None = None) -> bool:
    """SessionMap 是否切到 PersistentStore Repository（其它域不受影响）。"""
    return _storage_flag(
        cfg,
        config_key="session_map_repository",
        env_key="GATEWAY_SESSION_MAP_REPOSITORY",
        default=True,
    )


def _wire_config_and_cron_repositories(
    store: PersistentStore,
    cfg: dict[str, Any] | None,
) -> None:
    """注入 channel / permissions / logging / memory Repository 与 cron PersistentStore。"""
    from jiuwenswarm.gateway.config.channel.access import set_channel_config_repository
    from jiuwenswarm.gateway.config.logging.access import set_logging_config_repository
    from jiuwenswarm.gateway.config.memory.access import set_memory_config_repository
    from jiuwenswarm.gateway.config.permissions.access import (
        set_permissions_config_repository,
    )
    from jiuwenswarm.gateway.cron.job_access import set_cron_persistent_store

    edition = resolve_gateway_edition(cfg)
    instance_id = resolve_storage_instance_id(cfg)
    set_channel_config_repository(
        create_channel_config_repository(
            store, edition, instance_id=instance_id
        )
    )
    set_permissions_config_repository(
        create_permissions_config_repository(
            store, edition, instance_id=instance_id
        )
    )
    set_logging_config_repository(
        create_logging_config_repository(
            store, edition, instance_id=instance_id
        )
    )
    set_memory_config_repository(
        create_memory_config_repository(
            store, edition, instance_id=instance_id
        )
    )
    set_cron_persistent_store(store)


def _clear_config_and_cron_repositories() -> None:
    from jiuwenswarm.gateway.config.channel.access import clear_channel_config_repository
    from jiuwenswarm.gateway.config.logging.access import clear_logging_config_repository
    from jiuwenswarm.gateway.config.memory.access import clear_memory_config_repository
    from jiuwenswarm.gateway.config.permissions.access import (
        clear_permissions_config_repository,
    )
    from jiuwenswarm.gateway.cron.job_access import clear_cron_persistent_store

    clear_channel_config_repository()
    clear_permissions_config_repository()
    clear_logging_config_repository()
    clear_memory_config_repository()
    clear_cron_persistent_store()


async def setup_gateway_storage_repositories(
    cfg: dict[str, Any] | None = None,
) -> StorageContext | None:
    """按开关装配 SessionMap + overlay/cron Repository，返回共享 ``StorageContext``。

    - ``session_map_repository``：SessionMap
    - ``repositories``：channel / permissions / logging / memory / cron

    任一开启则创建 Context；全关返回 ``None``。须在 MessageHandler / CronTenantRegistry
    创建 tenant store 之前调用。
    """
    session_on = is_session_map_repository_enabled(cfg)
    repos_on = is_storage_repositories_enabled(cfg)
    if not session_on and not repos_on:
        return None
    if cfg is None:
        from jiuwenswarm.common.config import get_config

        cfg = get_config()

    ctx = create_gateway_storage_context(cfg)
    store = await ctx.persistent()

    if session_on:
        from jiuwenswarm.gateway.routing.session_map_access import (
            set_session_map_repository,
        )

        set_session_map_repository(create_session_map_repository(store))

    if repos_on:
        _wire_config_and_cron_repositories(store, cfg)

    return ctx


async def teardown_gateway_storage_repositories(ctx: StorageContext) -> None:
    from jiuwenswarm.gateway.routing.session_map_access import clear_session_map_repository

    clear_session_map_repository()
    _clear_config_and_cron_repositories()
    await ctx.shutdown()


async def setup_session_map_repository(
    cfg: dict[str, Any] | None = None,
) -> StorageContext | None:
    """装配 SessionMap Repository（兼容旧入口；其它域见 ``setup_gateway_storage_repositories``）。"""
    if not is_session_map_repository_enabled(cfg):
        return None
    if cfg is None:
        from jiuwenswarm.common.config import get_config

        cfg = get_config()
    ctx = create_gateway_storage_context(cfg)
    store = await ctx.persistent()
    from jiuwenswarm.gateway.routing.session_map_access import set_session_map_repository

    set_session_map_repository(create_session_map_repository(store))
    return ctx


async def teardown_session_map_repository(ctx: StorageContext) -> None:
    from jiuwenswarm.gateway.routing.session_map_access import clear_session_map_repository

    clear_session_map_repository()
    await ctx.shutdown()


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


def create_a2a_outbound_repository(
    store: PersistentStore,
):
    """Create the personal-edition JSON-backed outbound Repository."""
    from jiuwenswarm.gateway.a2a_manager.outbound import (
        A2AOutboundRepository,
        JsonA2AOutboundRecordCodec,
    )

    return A2AOutboundRepository(store, JsonA2AOutboundRecordCodec())


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


def _require_personal_config_store(edition: str, store_name: str) -> None:
    """heartbeat / browser / preferred_language / a2ui 无企业同构表。"""
    from jiuwenswarm.gateway.edition import EDITION_ENTERPRISE

    if edition == EDITION_ENTERPRISE:
        raise ValueError(
            f"{store_name} is personal-only (YAML overlay); "
            "no enterprise DB table"
        )


def create_heartbeat_config_repository(
    store: PersistentStore,
    edition: str,
    *,
    instance_id: str = "",
):
    from jiuwenswarm.gateway.config.heartbeat import (
        HeartbeatConfigRepository,
        YamlSectionCodec,
    )

    _require_personal_config_store(edition, "heartbeat_config")
    return HeartbeatConfigRepository(
        store, YamlSectionCodec(), instance_id=instance_id
    )


def create_browser_config_repository(
    store: PersistentStore,
    edition: str,
    *,
    instance_id: str = "",
):
    from jiuwenswarm.gateway.config.browser import (
        BrowserConfigRepository,
        YamlSectionCodec,
    )

    _require_personal_config_store(edition, "browser_config")
    return BrowserConfigRepository(
        store, YamlSectionCodec(), instance_id=instance_id
    )


def create_preferred_language_config_repository(
    store: PersistentStore,
    edition: str,
    *,
    instance_id: str = "",
):
    from jiuwenswarm.gateway.config.locale import (
        PreferredLanguageConfigRepository,
        YamlSectionCodec,
    )

    _require_personal_config_store(edition, "preferred_language_config")
    return PreferredLanguageConfigRepository(
        store, YamlSectionCodec(), instance_id=instance_id
    )


def create_a2ui_config_repository(
    store: PersistentStore,
    edition: str,
    *,
    instance_id: str = "",
):
    from jiuwenswarm.gateway.config.a2ui import (
        A2uiConfigRepository,
        YamlSectionCodec,
    )

    _require_personal_config_store(edition, "a2ui_config")
    return A2uiConfigRepository(
        store, YamlSectionCodec(), instance_id=instance_id
    )


def create_enterprise_record_repository(
    store: PersistentStore,
    store_name: str,
    *,
    instance_id: str = "",
):
    """企业专属表通用 Repository（仅 DB；不注入则 EE 仍走 DBHandler）。"""
    from jiuwenswarm.gateway.config.enterprise import EnterpriseRecordRepository

    return EnterpriseRecordRepository(
        store,
        store_name,
        instance_id=instance_id,
    )


def create_enterprise_record_repositories(
    store: PersistentStore,
    *,
    instance_id: str = "",
) -> dict[str, Any]:
    """为全部企业专属 store name 创建 ``EnterpriseRecordRepository``。

    返回 ``{store_name: repo}``；迁移期不调用 ``set_enterprise_record_repositories``，
    业务仍走 EE ``DBHandler``。
    """
    from jiuwenswarm.gateway.config.enterprise.catalog import (
        ENTERPRISE_RECORD_STORE_NAMES,
    )

    return {
        name: create_enterprise_record_repository(
            store, name, instance_id=instance_id
        )
        for name in ENTERPRISE_RECORD_STORE_NAMES
    }


__all__ = [
    "create_a2a_outbound_repository",
    "create_a2ui_config_repository",
    "create_browser_config_repository",
    "create_channel_config_repository",
    "create_enterprise_record_repository",
    "create_enterprise_record_repositories",
    "create_gateway_storage_context",
    "create_heartbeat_config_repository",
    "create_logging_config_repository",
    "create_memory_config_repository",
    "create_permissions_config_repository",
    "create_preferred_language_config_repository",
    "create_session_map_repository",
    "is_session_map_repository_enabled",
    "is_storage_repositories_enabled",
    "resolve_storage_instance_id",
    "setup_gateway_storage_repositories",
    "setup_session_map_repository",
    "teardown_gateway_storage_repositories",
    "teardown_session_map_repository",
]
