# Copyright (c) Huawei Technologies Co., Ltd. 2026. All rights reserved.
"""按 ``gateway.edition`` 装配 StorageContext。"""

from __future__ import annotations

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


def _on_config_written(path: Path) -> None:
    from jiuwenswarm.common.config import clear_config_cache
    from jiuwenswarm.common.utils import get_config_file

    if path.resolve() == get_config_file().resolve():
        clear_config_cache()


def _redis_client() -> Any | None:
    try:
        from jiuwenswarm.extensions.redis.redis_runtime import get_gateway_redis_client

        return get_gateway_redis_client()
    except Exception:
        return None


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


def create_gateway_storage_context(cfg: dict[str, Any] | None = None) -> StorageContext:
    if cfg is None:
        from jiuwenswarm.common.config import get_config

        cfg = get_config()
    edition = resolve_gateway_edition(cfg)
    return StorageContext(
        _create_persistent(edition),
        ephemeral_factory=_create_ephemeral_factory(edition),
    )


__all__ = ["create_gateway_storage_context"]
