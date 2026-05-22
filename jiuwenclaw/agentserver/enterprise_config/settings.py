"""企业配置生效策略库连接设置（对齐 manager_ws_client / config.yaml）。"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from jiuwenclaw.utils import get_user_workspace_dir, logger

DbType = Literal["mysql", "sqlite"]
DEFAULT_DB_TYPE: DbType = "mysql"
_LOG = "[enterprise_config]"


@dataclass(frozen=True)
class EffectivePolicyDatabaseSettings:
    """AgentServer 读取策略库与 ``model_template`` 的连接配置。"""

    db_type: DbType = DEFAULT_DB_TYPE
    sqlite_path: str = "gateway.db"
    mysql_host: str = "127.0.0.1"
    mysql_port: int = 3306
    mysql_user: str = "root"
    mysql_password: str = "123456"
    mysql_database: str = "openjiuwen_gateway"


_settings: EffectivePolicyDatabaseSettings | None = None


def _env(name: str, fallback: str = "") -> str:
    return os.getenv(name, fallback).strip()


def _env_int(name: str, fallback: int) -> int:
    raw = _env(name)
    if not raw:
        return fallback
    try:
        return int(raw)
    except ValueError:
        return fallback


def _read_yaml_database_section() -> dict[str, Any]:
    try:
        from jiuwenclaw.config import get_config


        section = (
            (get_config().get("extensions") or {})
            .get("agent_client_rest", {})
            .get("database", {})
        )
        return section if isinstance(section, dict) else {}
    except Exception as exc:
        logger.debug("%s read config.yaml database failed: %s", _LOG, exc)
        return {}


def load_settings() -> EffectivePolicyDatabaseSettings:
    """解析顺序：MANAGER_WS_CLIENT_* / AGENT_CLIENT_* / DB_* 环境变量 > config.yaml。"""
    yaml_cfg = _read_yaml_database_section()
    yaml_db = yaml_cfg.get("db") if isinstance(yaml_cfg.get("db"), dict) else {}

    db_type = (
        _env("MANAGER_WS_CLIENT_DB_TYPE")
        or _env("AGENT_CLIENT_DB_TYPE")
        or _env("JIUWENCLAW_GATEWAY_DB_TYPE")
        or str(yaml_cfg.get("db_type") or DEFAULT_DB_TYPE)
    ).strip().lower()
    if db_type not in ("mysql", "sqlite"):
        logger.warning(
            "%s unsupported db_type=%r, fallback to %s",
            _LOG,
            db_type,
            DEFAULT_DB_TYPE,
        )
        db_type = DEFAULT_DB_TYPE

    sqlite_path = (
        _env("MANAGER_WS_CLIENT_SQLITE_PATH")
        or _env("AGENT_CLIENT_SQLITE_PATH")
        or str(yaml_cfg.get("sqlite_path") or "gateway.db").strip()
    )

    return EffectivePolicyDatabaseSettings(
        db_type=db_type,  # type: ignore[arg-type]
        sqlite_path=sqlite_path or "gateway.db",
        mysql_host=(
            _env("MANAGER_WS_CLIENT_DB_HOST")
            or _env("DB_HOST")
            or _env("JIUWENCLAW_GATEWAY_DB_HOST")
            or str(yaml_db.get("host") or "127.0.0.1")
        ).strip(),
        mysql_port=_env_int(
            "MANAGER_WS_CLIENT_DB_PORT",
            _env_int(
                "DB_PORT",
                int(yaml_db.get("port") or 3306),
            ),
        ),
        mysql_user=(
            _env("MANAGER_WS_CLIENT_DB_USER")
            or _env("DB_USER")
            or _env("JIUWENCLAW_GATEWAY_DB_USER")
            or str(yaml_db.get("user") or "root")
        ).strip(),
        mysql_password=(
            _env("MANAGER_WS_CLIENT_DB_PASSWORD")
            or _env("DB_PASSWORD")
            or _env("JIUWENCLAW_GATEWAY_DB_PASSWORD")
            or str(yaml_db.get("password") or "123456")
        ),
        mysql_database=(
            _env("MANAGER_WS_CLIENT_DB_NAME")
            or _env("DB_NAME")
            or _env("JIUWENCLAW_GATEWAY_DB_NAME")
            or str(yaml_db.get("db_name") or "openjiuwen_gateway")
        ).strip(),
    )


def get_settings() -> EffectivePolicyDatabaseSettings:
    global _settings
    if _settings is None:
        _settings = load_settings()
    return _settings


def reset_settings() -> None:
    global _settings
    _settings = None


def enterprise_policy_enabled() -> bool:
    """需组网实例 id；MySQL 默认可用，SQLite 需可解析路径。"""
    flag = _env("JIUWENCLAW_ENTERPRISE_MODEL_POLICY").lower()
    if flag in ("0", "false", "no", "off"):
        return False
    if flag in ("1", "true", "yes", "on"):
        return True
    if not _env("JIUWENCLAW_PROVISIONED_INSTANCE_ID"):
        return False
    settings = get_settings()
    if settings.db_type == "mysql":
        return True
    explicit = _env("JIUWENCLAW_EFFECTIVE_POLICY_DB_PATH") or _env("JIUWENCLAW_GATEWAY_DB_PATH")
    if explicit:
        return True
    return False
