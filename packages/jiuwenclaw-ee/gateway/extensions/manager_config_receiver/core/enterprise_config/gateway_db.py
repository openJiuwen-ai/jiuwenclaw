# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway 本地库：进程内单例 + handler 入口（底层连库见 ``infrastructure.db.Database``）。"""

from __future__ import annotations

from pathlib import Path
from typing import Any, ClassVar

from openjiuwen_runtime.foundation.db.handler import DBHandler
from openjiuwen_runtime.foundation.log import get_logger

from ...infrastructure.config import Settings
from ...infrastructure.db import Database

logger = get_logger(__name__)

_DEFAULT_RELATIVE_ROOT = Path(__file__).resolve().parents[2]


class GatewayDb(Database):
    """Gateway 企业库连接；进程内仅一个实例持有连接池。"""

    _current: ClassVar[GatewayDb | None] = None

    def __init__(
        self,
        *,
        cfg: Settings | None = None,
        relative_root: Path | None = None,
        _with_connection: bool = False,
    ) -> None:
        if _with_connection:
            super().__init__(cfg=cfg, relative_root=relative_root or _DEFAULT_RELATIVE_ROOT)

    @classmethod
    def _ensure_singleton(cls) -> GatewayDb:
        if cls._current is None:
            cls._current = cls(_with_connection=True)
        return cls._current

    @classmethod
    def bind(cls, *_args: Any, **_kwargs: Any) -> GatewayDb:
        """兼容旧调用：返回进程内单例（忽略历史实例 id 参数）。"""
        return cls._ensure_singleton()

    @classmethod
    def current(cls) -> GatewayDb:
        return cls._ensure_singleton()

    @classmethod
    async def release(cls) -> None:
        """断连/注销时释放连接池。"""
        if cls._current is not None:
            await cls._current.close()
            cls._current = None


def get_shared_gateway_database() -> GatewayDb:
    """进程内唯一的 Gateway 本地库（``GatewayDb`` 单例）。"""
    return GatewayDb.current()


async def ensure_gateway_db_handler(
    *,
    log_prefix: str = "gateway_db",
) -> DBHandler:
    """直连 ``GatewayDb`` 单例（AgentServer / 企业配置读等无 PersistentStore 的场景）。

    不探测 ``wire_manager_ws_table_store``；与 Gateway 写库共用同一连接池。
    """
    return await GatewayDb.current().ensure_ready(log_prefix=log_prefix)


async def ensure_db_handler(*, log_prefix: str = "manager_config_receiver") -> DBHandler:
    """获取 DB CRUD 入口。

    - 已 ``wire_manager_ws_table_store``：返回 ``PersistentStore`` 适配器。
    - 未注入：``GatewayDb`` 直连（同 ``ensure_gateway_db_handler``）。

    AgentServer 与 ``reader`` 请优先用 ``ensure_gateway_db_handler``。
    Manager Config Receiver 写路径走 ``require_*_repository`` / ``ensure_table_store``。
    """
    from ...infrastructure.table_store_access import get_table_store_handler_if_wired

    wired = await get_table_store_handler_if_wired()
    if wired is not None:
        return wired  # type: ignore[return-value]
    logger.warning(
        "[%s] PersistentStore not wired; falling back to GatewayDb "
        "(legacy / Agent path; Manager routers must use ensure_table_store)",
        log_prefix,
    )
    return await ensure_gateway_db_handler(log_prefix=log_prefix)


__all__ = (
    "GatewayDb",
    "ensure_db_handler",
    "ensure_gateway_db_handler",
    "get_shared_gateway_database",
)
