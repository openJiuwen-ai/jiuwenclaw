# Copyright (c) Huawei Technologies Co., Ltd. 2026-2026. All rights reserved

"""Gateway manager_ws_client 配置（默认读取仓库根 ``.env`` 中的 ``GATEWAY_*``）。"""

from __future__ import annotations

from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


def _resolve_env_files() -> tuple[str | Path, ...]:
    """解析可用的 .env 路径（优先 cwd，兼容 venv 安装布局）。"""
    candidates: list[Path] = [Path.cwd() / ".env"]
    here = Path(__file__).resolve()
    for depth in (5, 6, 7):
        candidates.append(here.parents[depth] / ".env")
    return tuple(p for p in candidates if p.is_file())


def load_env() -> None:
    """从仓库根 ``.env`` 加载（优先 cwd，兼容 venv 安装布局）。"""
    try:
        from dotenv import load_dotenv
    except ImportError:
        return

    for env_path in _resolve_env_files():
        load_dotenv(env_path, override=True)


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=_resolve_env_files() or None,
        env_file_encoding="utf-8",
        extra="ignore",
    )

    gateway_db_type: str = Field(default="sqlite", validation_alias="GATEWAY_DB_TYPE")
    gateway_sqlite_path: str = Field(
        default="gateway.db",
        validation_alias="GATEWAY_SQLITE_PATH",
    )
    gateway_db_host: str = Field(default="127.0.0.1", validation_alias="GATEWAY_DB_HOST")
    gateway_db_port: int = Field(default=3306, validation_alias="GATEWAY_DB_PORT")
    gateway_db_user: str = Field(default="root", validation_alias="GATEWAY_DB_USER")
    gateway_db_password: str = Field(
        default="root",
        validation_alias="GATEWAY_DB_PASSWORD",
    )
    gateway_db_name: str = Field(
        default="gateway",
        validation_alias="GATEWAY_DB_NAME",
    )

    gateway_manager_ws_client_enabled: bool = Field(
        default=True,
        validation_alias="GATEWAY_MANAGER_WS_CLIENT_ENABLED",
    )
    gateway_manager_ws_url: str = Field(
        default="ws://127.0.0.1:8766",
        validation_alias="GATEWAY_MANAGER_WS_URL",
    )


def get_settings() -> Settings:
    """重新加载 .env 后返回当前配置（provision 子进程注入的环境变量生效）。"""
    load_env()
    return Settings()


settings = get_settings()
