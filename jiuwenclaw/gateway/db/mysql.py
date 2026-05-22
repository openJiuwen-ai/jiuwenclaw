from __future__ import annotations

from typing import Any

_PLACEHOLDER_MSG = (
    "MysqlUtil is a placeholder; replace jiuwenclaw/gateway/db/mysql.py "
    "with the deployment implementation"
)

SANDBOX_REGISTRY_TABLE = "claw_sandbox_registry"


class MysqlUtil:
    """MySQL 访问占位实现；部署时替换本文件为真实数据库客户端。

    沙箱路由开启时由 sandbox_registry / sandbox_info_http 调用，无需单独配置。
    """

    _instance: MysqlUtil | None = None

    @classmethod
    def instance(cls, force_new: bool = False) -> MysqlUtil:
        if cls._instance is None or force_new:
            cls._instance = cls()
        return cls._instance

    @classmethod
    def execute(
        cls,
        sql: str,
        params: tuple[Any, ...] | list[Any] | None = None,
    ) -> list[dict[str, Any]]:
        _ = sql, params
        raise NotImplementedError(_PLACEHOLDER_MSG)
